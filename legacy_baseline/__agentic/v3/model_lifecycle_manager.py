"""V3 Model Lifecycle Manager - discovery, sandboxed benchmarking and audited registry patches.

Zero Rogue Hotfix mandate
=========================
The router registry (``MODEL_REGISTRY`` in ``llm_router.py``) is production
routing state. No automated component may edit it in place. This module
therefore only ever *reads* the router (parsed with ``ast``, never imported),
and every proposed model change goes through the same path:

1. ``discover_models``    - query the provider CLIs offline and list model ids
                            that are not yet registered anywhere in the router.
2. ``execute_benchmark``  - run the benchmark suite for a candidate inside a
                            hardened, network-less Docker sandbox.
3. ``evaluate_benchmark`` - approve only on exit code 0 plus a clean pytest
                            summary; every verdict is written to
                            ``kanban_telemetry_v3`` (MODEL_BENCHMARK_EVALUATED).
4. ``generate_patch``     - render a unified diff that changes exactly one
                            registry entry, with a fallback chain satisfying the
                            V2 invariants R1/R2/R4/R5 and asymmetry pairs (R6).
5. ``submit_patch_task``  - store the diff under ``data/patches`` and enqueue a
                            priority-1 ``audit`` task. A human/auditor applies it;
                            this module never writes ``llm_router.py``.

All subprocesses run windowless (``CREATE_NO_WINDOW``) with utf-8 decoding.
No network access and no LLM calls happen inside SQLite write transactions.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import importlib.util
import json
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

CLAUDE_PROVIDER = "claude-subscription"
GEMINI_PROVIDER = "gemini"
HALT_PROVIDER = "halt"
HALT_SENTINEL: tuple[str, str] = (HALT_PROVIDER, "graceful")
CLOUD_PROVIDERS: tuple[str, str] = (CLAUDE_PROVIDER, GEMINI_PROVIDER)

DEFAULT_CLI_PREFIXES: dict[str, list[str]] = {
    CLAUDE_PROVIDER: ["claude", "--help"],
    GEMINI_PROVIDER: ["agy", "models"],
    "ollama": ["ollama", "list"],
}

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CLAUDE_ID_RE = re.compile(r"claude-[a-z0-9][a-z0-9.-]*[a-z0-9]")
_GEMINI_ID_RE = re.compile(r"gemini-[a-z0-9][a-z0-9.-]*[a-z0-9]")
_FAILED_RE = re.compile(r"(\d+)\s+(?:failed|errors?)\b")
_PASSED_RE = re.compile(r"(\d+)\s+passed\b")
_UNSAFE_CANDIDATE_RE = re.compile(r"[\s'\"\\]")
_LINE_BREAK_RE = re.compile(rb"\r\n|\r|\n")
SNIPPET_CHARS = 2000


def _to_text(data: Any) -> str:
    """Normalise partial subprocess output (None/bytes/str) to text."""
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _split_lines(text: str) -> list[str]:
    """Split on LF/CRLF only (the line model used by ``ast``), no trailing empty item."""
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


@dataclass
class DockerRunResult:
    """Raw outcome of one sandboxed (or CLI) process execution."""

    exit_code: int
    stdout: str
    stderr: str
    cmd: list[str]
    duration_s: float = 0.0


class DockerSandboxRunner:
    """Builds hardened ``docker run`` argv lists and executes them windowless."""

    def __init__(self, docker_cli: Sequence[str] = ("docker",), image: str = "cochem-v3:latest") -> None:
        """Store the docker CLI prefix (e.g. ``["docker"]``) and the default image."""
        cli = [str(tok) for tok in docker_cli]
        if not cli:
            raise ValueError("docker_cli must contain at least one token")
        self.docker_cli: list[str] = cli
        self.image: str = image

    def build_docker_args(
        self,
        image: str,
        test_command: Sequence[str],
        volume_mount: str = "cochem-data:/workspace/data",
        env: Optional[Mapping[str, str]] = None,
    ) -> list[str]:
        """Return the hardened argv: read-only, non-root, no network, no capabilities."""
        if not image or not str(image).strip():
            raise ValueError("image must be a non-empty string")
        command = [str(tok) for tok in test_command]
        if not command:
            raise ValueError("test_command must be non-empty")
        if not volume_mount:
            raise ValueError("volume_mount must be non-empty")
        args: list[str] = [
            *self.docker_cli, "run", "--rm", "--read-only",
            "--user", "1000:1000",
            "--network", "none",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp",
            "-v", volume_mount,
        ]
        for key, value in (env or {}).items():
            if not _ENV_KEY_RE.match(str(key)):
                raise ValueError(f"invalid environment variable name: {key!r}")
            args.extend(["-e", f"{key}={value}"])
        args.append(str(image))
        args.extend(command)
        return args

    def run(self, cmd_args: Sequence[str], timeout: float = 120) -> DockerRunResult:
        """Execute ``cmd_args`` and return the raw exit code and streams (never raises on exit != 0).

        A missing executable yields exit code 127, a timeout yields 124.
        """
        cmd = [str(tok) for tok in cmd_args]
        started = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stderr = _to_text(exc.stderr)
            note = f"[DockerSandboxRunner] timeout after {timeout}s"
            return DockerRunResult(
                exit_code=124,
                stdout=_to_text(exc.stdout),
                stderr=(stderr + "\n" + note) if stderr else note,
                cmd=cmd,
                duration_s=time.monotonic() - started,
            )
        except OSError as exc:  # includes FileNotFoundError
            return DockerRunResult(
                exit_code=127,
                stdout="",
                stderr=f"[DockerSandboxRunner] cannot execute {cmd[0]!r}: {exc}",
                cmd=cmd,
                duration_s=time.monotonic() - started,
            )
        return DockerRunResult(
            exit_code=int(proc.returncode),
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            cmd=cmd,
            duration_s=time.monotonic() - started,
        )


class V3ModelLifecycleManager:
    """Discovers, benchmarks and proposes (never applies) router model upgrades."""

    def __init__(
        self,
        db_path: str | Path,
        schema_path: Optional[str | Path] = None,
        docker_runner: Optional[DockerSandboxRunner] = None,
        router_path: Optional[str | Path] = None,
        patch_dir: Optional[str | Path] = None,
        v2_registry_path: Optional[str | Path] = None,
    ) -> None:
        """Open the WAL-mode kanban database and resolve router/patch/V2 paths."""
        here = Path(__file__).resolve()
        self.db_path = Path(db_path)
        self.router_path = Path(router_path) if router_path is not None else here.parents[2] / "llm_router.py"
        self.patch_dir = Path(patch_dir) if patch_dir is not None else here.parent / "data" / "patches"
        self.v2_registry_path = (
            Path(v2_registry_path) if v2_registry_path is not None
            else here.parents[1] / "v2" / "MODEL_REGISTRY_V2.py"
        )
        self.docker_runner = docker_runner if docker_runner is not None else DockerSandboxRunner()
        self._v2_cache: Optional[tuple[dict[str, dict], tuple[tuple[str, str], ...]]] = None

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = sqlite3.connect(str(self.db_path), timeout=5.0)
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.execute("PRAGMA foreign_keys=ON")
            if schema_path is not None:
                script = Path(schema_path).read_text(encoding="utf-8")
                self.conn.executescript(script)
                self.conn.commit()
        except Exception:
            self.conn.close()
            self.conn = None
            raise

    # ── connection handling ──────────────────────────────────────────────
    def get_connection(self) -> sqlite3.Connection:
        """Return the open SQLite connection (RuntimeError after ``close``)."""
        if self.conn is None:
            raise RuntimeError("V3ModelLifecycleManager connection is closed")
        return self.conn

    def close(self) -> None:
        """Close the database connection; safe to call more than once."""
        conn, self.conn = self.conn, None
        if conn is not None:
            conn.close()

    # ── registry sources ─────────────────────────────────────────────────
    def _read_router_source(self) -> str:
        """Read the router file exactly (bytes -> utf-8), keeping line endings."""
        return self.router_path.read_bytes().decode("utf-8")

    @staticmethod
    def _registry_assignment(source: str) -> ast.Assign | ast.AnnAssign:
        """Locate the top-level ``MODEL_REGISTRY = {...}`` statement."""
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "MODEL_REGISTRY" for t in node.targets
            ):
                return node
            if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                    and node.target.id == "MODEL_REGISTRY" and node.value is not None):
                return node
        raise ValueError("no top-level MODEL_REGISTRY assignment found in router source")

    def _load_router_registry(self, source: Optional[str] = None) -> dict[str, dict]:
        """Parse MODEL_REGISTRY from the router via ast.literal_eval (never imports it)."""
        text = source if source is not None else self._read_router_source()
        node = self._registry_assignment(text)
        registry = ast.literal_eval(node.value)
        if not isinstance(registry, dict):
            raise ValueError("MODEL_REGISTRY is not a dict literal")
        return registry

    @staticmethod
    def _registered_models(registry: Mapping[str, Mapping[str, Any]]) -> set[str]:
        """All model ids in the registry: primaries and every fallback model."""
        known: set[str] = set()
        for entry in registry.values():
            if "model" in entry:
                known.add(str(entry["model"]))
            for link in entry.get("fallbacks", []):
                if len(link) >= 2:
                    known.add(str(link[1]))
        return known

    def _load_v2(self) -> tuple[dict[str, dict], tuple[tuple[str, str], ...]]:
        """Load MODEL_TIERS and ASYMMETRY_PAIRS from the side-effect-free V2 registry file."""
        if self._v2_cache is None:
            spec = importlib.util.spec_from_file_location("_v3_mlm_model_registry_v2", str(self.v2_registry_path))
            if spec is None or spec.loader is None:
                raise RuntimeError(f"cannot load V2 registry from {self.v2_registry_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            tiers = {str(m): dict(t) for m, t in module.MODEL_TIERS.items()}
            pairs = tuple((str(a), str(b)) for a, b in module.ASYMMETRY_PAIRS)
            self._v2_cache = (tiers, pairs)
        return self._v2_cache

    # ── discovery ────────────────────────────────────────────────────────
    @staticmethod
    def _parse_cli_models(provider: str, stdout: str) -> list[str]:
        """Extract model ids from one provider CLI's stdout (order-preserving, deduped)."""
        if provider == CLAUDE_PROVIDER:
            found = _CLAUDE_ID_RE.findall(stdout)
        elif provider == GEMINI_PROVIDER:
            found = _GEMINI_ID_RE.findall(stdout)
        else:
            found = []
            for line in stdout.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("NAME"):
                    continue
                found.append(stripped.split()[0])
        return list(dict.fromkeys(found))

    def discover_models(self, cli_prefixes: Optional[dict[str, list[str]]] = None) -> dict[str, list[str]]:
        """Run each provider CLI and return unregistered model ids per provider.

        A missing CLI, a timeout or a non-zero exit yields ``[]`` for that
        provider; the remaining providers are still queried.
        """
        prefixes = cli_prefixes if cli_prefixes is not None else DEFAULT_CLI_PREFIXES
        known = self._registered_models(self._load_router_registry())
        result: dict[str, list[str]] = {}
        for provider, argv in prefixes.items():
            result[provider] = []
            cmd = [str(tok) for tok in argv]
            if not cmd:
                continue
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                continue
            if proc.returncode != 0:
                continue
            ids = self._parse_cli_models(provider, proc.stdout or "")
            result[provider] = [m for m in ids if m not in known]
        return result

    # ── benchmarking ─────────────────────────────────────────────────────
    def execute_benchmark(self, candidate: str, provider: str, test_suite: str = "tests/benchmark",
                          timeout: float = 600) -> DockerRunResult:
        """Run ``pytest -q <test_suite>`` for ``candidate`` inside the hardened sandbox."""
        if not candidate or not str(candidate).strip():
            raise ValueError("candidate must be a non-empty model id")
        if not test_suite:
            raise ValueError("test_suite must be non-empty")
        runner = self.docker_runner
        argv = runner.build_docker_args(
            runner.image,
            ["python", "-m", "pytest", "-q", test_suite],
            env={"COCHEM_CANDIDATE_MODEL": candidate, "COCHEM_CANDIDATE_PROVIDER": provider},
        )
        return runner.run(argv, timeout=timeout)

    def evaluate_benchmark(self, candidate: str, run_result: DockerRunResult, task_id: str) -> bool:
        """Approve iff exit 0, no failed/error tests and >=1 passed; log one telemetry row."""
        stdout = run_result.stdout or ""
        stderr = run_result.stderr or ""
        output = stdout + "\n" + stderr
        failed = sum(int(n) for n in _FAILED_RE.findall(output))
        passed = sum(int(n) for n in _PASSED_RE.findall(output))
        exit_code = int(run_result.exit_code)
        approved = bool(exit_code == 0 and failed == 0 and passed > 0)

        conn = self.get_connection()
        exists = conn.execute("SELECT 1 FROM kanban_tasks_v3 WHERE task_id = ?", (task_id,)).fetchone()
        if exists is None:
            raise ValueError(f"task {task_id!r} does not exist in kanban_tasks_v3")
        payload = json.dumps({
            "candidate": candidate,
            "exit_code": exit_code,
            "approved": approved,
            "passed": passed,
            "failed": failed,
            "cmd": [str(tok) for tok in run_result.cmd],
            "stdout_snippet": stdout[-SNIPPET_CHARS:],
            "stderr_snippet": stderr[-SNIPPET_CHARS:],
        })
        try:
            conn.execute(
                "INSERT INTO kanban_telemetry_v3 (task_id, event_type, payload) VALUES (?, ?, ?)",
                (task_id, "MODEL_BENCHMARK_EVALUATED", payload),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return approved

    # ── patch generation ─────────────────────────────────────────────────
    @staticmethod
    def _build_fallback_chain(candidate: str, provider: str, estimated_cost: int,
                              tiers: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, str]]:
        """Cost-non-increasing, provider-alternating chain ending in the halt sentinel."""
        other = GEMINI_PROVIDER if provider == CLAUDE_PROVIDER else CLAUDE_PROVIDER

        def sort_key(model: str) -> tuple[int, int, str]:
            """Order by cost desc, then rank desc, then name."""
            tier = tiers[model]
            return (-int(tier["cost_in"]), -int(tier.get("rank", 0)), model)

        pools: dict[str, list[str]] = {}
        for prov in CLOUD_PROVIDERS:
            pools[prov] = sorted(
                (m for m, t in tiers.items()
                 if t["provider"] == prov and m != candidate and int(t["cost_in"]) <= estimated_cost),
                key=sort_key,
            )

        chain: list[tuple[str, str]] = []
        used: set[str] = set()
        if pools[other]:
            first = pools[other][0]
        else:
            same_costs = [int(t["cost_in"]) for m, t in tiers.items() if t["provider"] == provider and m != candidate]
            prim_is_floor = estimated_cost <= min(same_costs + [estimated_cost])
            other_models = [m for m, t in tiers.items() if t["provider"] == other and m != candidate]
            if not prim_is_floor or not other_models:
                raise ValueError(
                    f"R1 impossible: no {other!r} fallback with cost <= {estimated_cost} for {candidate!r}"
                )
            first = min(other_models, key=lambda m: (int(tiers[m]["cost_in"]), int(tiers[m].get("rank", 0)), m))
        chain.append((other, first))
        used.add(first)

        prev = other
        while True:
            nxt_prov = GEMINI_PROVIDER if prev == CLAUDE_PROVIDER else CLAUDE_PROVIDER
            picked: Optional[tuple[str, str]] = None
            for prov in (nxt_prov, prev):
                remaining = [m for m in pools[prov] if m not in used]
                if remaining:
                    picked = (prov, remaining[0])
                    break
            if picked is None:
                break
            chain.append(picked)
            used.add(picked[1])
            prev = picked[0]
        chain.append(HALT_SENTINEL)
        return chain

    @staticmethod
    def _check_entry_invariants(entry: Mapping[str, Any], estimated_cost: int,
                                cost_map: Mapping[str, int], provider_map: Mapping[str, str]) -> None:
        """Validate R1, R2 (with V2 floor exception), R4 and R5 for one entry; ValueError on violation."""
        provider = entry["provider"]
        chain = [(entry["provider"], entry["model"])] + [tuple(fb) for fb in entry["fallbacks"]]
        halt_idx = [i for i, (p, _m) in enumerate(chain) if p == HALT_PROVIDER]
        if halt_idx != [len(chain) - 1]:
            raise ValueError(f"R4 violated: halt sentinel positions {halt_idx} in {chain}")
        if len(set(chain)) != len(chain):
            raise ValueError(f"R5 violated: duplicate (provider, model) in {chain}")
        cloud_fb = [(p, m) for p, m in chain[1:] if p != HALT_PROVIDER]
        if not cloud_fb or cloud_fb[0][0] == provider or cloud_fb[0][0] not in CLOUD_PROVIDERS:
            raise ValueError(f"R1 violated: first fallback must be on the other cloud provider: {chain}")

        def min_cost(prov: str) -> int:
            """Cheapest cost weight on ``prov`` (tiers plus candidate)."""
            return min(c for m, c in cost_map.items() if provider_map[m] == prov)

        prim_is_floor = estimated_cost == min_cost(provider)
        for p, m in cloud_fb:
            if p not in CLOUD_PROVIDERS or m not in cost_map or provider_map[m] != p:
                raise ValueError(f"R2 violated: {m!r} is not a known tier model on {p!r}")
            mc = cost_map[m]
            floor_ok = prim_is_floor and p != provider and mc == min_cost(p)
            if mc > estimated_cost and not floor_ok:
                raise ValueError(f"R2 violated: fallback {m!r} (cost {mc}) exceeds primary cost {estimated_cost}")

    @staticmethod
    def _render_entry_value(provider: str, model: str, fallbacks: Sequence[tuple[str, str]],
                            column: int, eol: str) -> str:
        """Render an entry dict in the router's two-line style."""
        fb = ", ".join(f"({json.dumps(p)}, {json.dumps(m)})" for p, m in fallbacks)
        return (
            f'{{"provider": {json.dumps(provider)}, "model": {json.dumps(model)},{eol}'
            f'{" " * (column + 1)}"fallbacks": [{fb}]}}'
        )

    @staticmethod
    def _line_byte_offsets(data: bytes) -> list[int]:
        """Byte offset of the start of every line (ast line model: CRLF, CR, LF)."""
        offsets = [0]
        offsets.extend(m.end() for m in _LINE_BREAK_RE.finditer(data))
        return offsets

    def generate_patch(self, candidate: str, provider: str, target_role: str, estimated_cost: int = 1) -> str:
        """Return a unified diff replacing ``MODEL_REGISTRY[target_role]`` with ``candidate``.

        The router file is only read; the diff is meant for the audit queue.
        """
        if provider not in CLOUD_PROVIDERS:
            raise ValueError(f"provider must be one of {CLOUD_PROVIDERS}, got {provider!r}")
        if not isinstance(candidate, str) or not candidate or _UNSAFE_CANDIDATE_RE.search(candidate):
            raise ValueError(f"invalid candidate model id: {candidate!r}")
        if isinstance(estimated_cost, bool) or not isinstance(estimated_cost, int) or estimated_cost < 0:
            raise ValueError(f"estimated_cost must be an int >= 0, got {estimated_cost!r}")

        source = self._read_router_source()
        registry = self._load_router_registry(source)
        if target_role not in registry:
            raise ValueError(f"target_role {target_role!r} not present in MODEL_REGISTRY")

        tiers, pairs = self._load_v2()
        if candidate in tiers and tiers[candidate]["provider"] != provider:
            raise ValueError(f"{candidate!r} is a {tiers[candidate]['provider']!r} tier model, not {provider!r}")
        cost_map: dict[str, int] = {m: int(t["cost_in"]) for m, t in tiers.items()}
        cost_map[candidate] = estimated_cost
        provider_map: dict[str, str] = {m: str(t["provider"]) for m, t in tiers.items()}
        provider_map[candidate] = provider

        chain = self._build_fallback_chain(candidate, provider, estimated_cost, tiers)
        new_entry = {"provider": provider, "model": candidate, "fallbacks": chain}
        proposed = dict(registry)
        proposed[target_role] = new_entry

        for producer, verifier in pairs:
            if producer in proposed and verifier in proposed:
                if proposed[producer]["provider"] == proposed[verifier]["provider"]:
                    raise ValueError(
                        f"R6 violated: asymmetry pair ({producer}, {verifier}) would share "
                        f"primary provider {proposed[producer]['provider']!r}"
                    )
        self._check_entry_invariants(new_entry, estimated_cost, cost_map, provider_map)

        # Locate the exact byte span of the target entry's value (ast offsets are UTF-8 bytes).
        node = self._registry_assignment(source)
        if not isinstance(node.value, ast.Dict):
            raise ValueError("MODEL_REGISTRY is not a dict display")
        value_node: Optional[ast.expr] = None
        for key, value in zip(node.value.keys, node.value.values):
            if isinstance(key, ast.Constant) and key.value == target_role:
                value_node = value
        if value_node is None or value_node.end_lineno is None or value_node.end_col_offset is None:
            raise ValueError(f"cannot locate source span of {target_role!r}")

        data = source.encode("utf-8")
        offsets = self._line_byte_offsets(data)
        start = offsets[value_node.lineno - 1] + value_node.col_offset
        end = offsets[value_node.end_lineno - 1] + value_node.end_col_offset
        column = len(data[offsets[value_node.lineno - 1]:start].decode("utf-8"))
        eol = "\r\n" if "\r\n" in source else "\n"
        rendered = self._render_entry_value(provider, candidate, chain, column, eol)
        new_source = (data[:start] + rendered.encode("utf-8") + data[end:]).decode("utf-8")

        if self._load_router_registry(new_source) != proposed:
            raise RuntimeError("rendered MODEL_REGISTRY does not match the proposed registry")

        diff = difflib.unified_diff(
            _split_lines(source), _split_lines(new_source),
            fromfile="a/llm_router.py", tofile="b/llm_router.py", lineterm="", n=3,
        )
        return "\n".join(diff) + "\n"

    # ── audit submission ─────────────────────────────────────────────────
    @staticmethod
    def _same_path(uri: Optional[str], target: Path) -> bool:
        """True when ``uri`` (plain local path) resolves to ``target``."""
        if not uri:
            return False
        try:
            return Path(uri).resolve() == target
        except (OSError, ValueError):
            return False

    def submit_patch_task(self, patch_content: str, patch_filename: str, task_id: Optional[str] = None) -> str:
        """Save the patch and enqueue an idempotent priority-1 ``audit`` task; return its task_id."""
        if not isinstance(patch_content, str) or not patch_content:
            raise ValueError("patch_content must be a non-empty string")
        name = str(patch_filename)
        if (not name or name in (".", "..") or "/" in name or "\\" in name
                or Path(name).name != name or ":" in name):
            raise ValueError(f"patch_filename must be a plain file name: {patch_filename!r}")
        if task_id is not None and not str(task_id).strip():
            raise ValueError("task_id must be non-empty when given")

        self.patch_dir.mkdir(parents=True, exist_ok=True)
        content = patch_content.encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        tid = str(task_id) if task_id is not None else f"MODEL_PATCH_{digest[:16]}"
        target = (self.patch_dir / name).resolve()
        payload_uri = str(target)
        on_disk = target.read_bytes() if target.is_file() else None

        conn = self.get_connection()
        existing = conn.execute("SELECT payload_uri FROM kanban_tasks_v3 WHERE task_id = ?", (tid,)).fetchone()
        if existing is not None:
            if self._same_path(existing[0], target) and on_disk == content:
                return tid
            raise ValueError(f"task {tid!r} already exists with a different patch payload")

        referencing = [
            row[0] for row in conn.execute(
                "SELECT task_id, payload_uri FROM kanban_tasks_v3 WHERE workflow_type = 'audit'"
            ).fetchall()
            if self._same_path(row[1], target)
        ]
        if referencing:
            if on_disk == content:
                return referencing[0]
            raise ValueError(
                f"{target} is already referenced by audit task {referencing[0]!r} with different content"
            )

        target.write_bytes(content)
        telemetry = json.dumps({"patch_file": payload_uri, "sha256": digest})
        try:
            conn.execute(
                "INSERT INTO kanban_tasks_v3 (task_id, workflow_type, status, priority, current_state, payload_uri) "
                "VALUES (?, 'audit', 'todo', 1, 0, ?)",
                (tid, payload_uri),
            )
            conn.execute(
                "INSERT INTO kanban_telemetry_v3 (task_id, event_type, payload) VALUES (?, ?, ?)",
                (tid, "MODEL_PATCH_SUBMITTED", telemetry),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return tid
