"""Live inter-agent handoff verification suite (Task 196.14).

Executes real model calls through ``llm_router.structured_call`` for the five
V4 handoff exchanges:

    A: PlanDossier            (plan revision loop)
    B: AuditFeedback          (audit feedback loop)
    C: ExecutionChunk         (task dispatch)
    D: ImplementationDossier  (verification handoff)
    E: PhysicsAutopsyReport   (diagnostics loop)

For every exchange a producer provider emits the exchange payload and an
independently selected verifier provider (chosen from the physically available
providers by ``_select_verifier``) audits that payload into an
``AuditFeedback``. The physical ``provider_used`` of both StructuredResults plus
the executed CLI argv[0] / HTTP endpoint are recorded into
``.evidence/live_handoffs/providers.json``.

Without ``COCHEM_LIVE=1`` every test raises ``RuntimeError('COCHEM_LIVE not set')``
and fails hard. The project modules (``llm_router`` and ``v4_schemas``) are
imported lazily, only after the ``COCHEM_LIVE`` guard has passed, so pytest
collection always succeeds and the guard is always the first failure reported,
regardless of the state of those modules or of pydantic in the host environment.

Canonical invocation (PowerShell):
    $env:COCHEM_LIVE='1'; pytest D:/__CoChem/__agentic/test_v4_live_handoffs.py -q 2>&1 |
        Tee-Object -FilePath .evidence/live_handoffs/live_run.txt
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVIDENCE_DIR = ROOT / ".evidence" / "live_handoffs"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EXCHANGE_KEYS = ("A", "B", "C", "D", "E")
LIVE_TIMEOUT_S = float(os.environ.get("COCHEM_LIVE_TIMEOUT", "600"))

# Attribute names inspected on StructuredResult / AttemptRecord objects to
# recover the physically executed CLI argv (argv[0]) or HTTP endpoint.
_ENDPOINT_ATTRS = (
    "argv",
    "executed_argv",
    "command",
    "cmd",
    "executable",
    "binary",
    "endpoint",
    "url",
)


# --------------------------------------------------------------------------- helpers


def _run_host(argv: list[str]) -> subprocess.CompletedProcess:
    """Execute a real host command (never a shell) and return the completed process."""
    return subprocess.run(
        argv,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        shell=False,
        creationflags=NO_WINDOW,
    )


def _known_binary(names: tuple[str, ...], fallbacks: tuple[Path, ...]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    for candidate in fallbacks:
        if candidate.is_file():
            return str(candidate)
    return None


def _resolve_provider_endpoint(provider: str) -> str | None:
    """Map a provider identifier to its physical binary path or HTTP endpoint."""
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    lowered = provider.lower()
    if "ollama" in lowered:
        ollama_bin = _known_binary(
            ("ollama",), (local / "Programs" / "Ollama" / "ollama.exe",)
        )
        if ollama_bin is None:
            return None
        host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        return host + "/api/chat"
    if "claude" in lowered or "anthropic" in lowered:
        return _known_binary(
            ("claude",), (Path.home() / ".local" / "bin" / "claude.exe",)
        )
    if any(tok in lowered for tok in ("gemini", "agy", "antigravity", "google")):
        return _known_binary(
            ("agy", "gemini"), (local / "agy" / "bin" / "agy.exe",)
        )
    return None


def _available_providers() -> list[str]:
    configured = os.environ.get("COCHEM_LIVE_PROVIDERS", "claude,gemini,ollama")
    names = [p.strip() for p in configured.split(",") if p.strip()]
    available = [p for p in names if _resolve_provider_endpoint(p) is not None]
    if len(available) < 2:
        raise RuntimeError(
            "Hard abort: fewer than two physical providers present on host "
            f"(configured={names}, available={available}); asymmetric verification impossible"
        )
    return available


def _select_verifier(producer: str, available: list[str]) -> str:
    """Pick a verifier provider physically distinct from the producer.

    Candidates are walked cyclically starting after the producer so verifier
    load rotates across providers. A candidate is accepted only if its name
    differs from the producer AND it resolves to a different physical binary
    or HTTP endpoint (so aliases of the same CLI are never treated as
    independent verifiers).
    """
    producer_endpoint = _resolve_provider_endpoint(producer)
    if producer in available:
        start = available.index(producer)
        ordered = available[start + 1 :] + available[:start]
    else:
        ordered = list(available)
    for candidate in ordered:
        if candidate == producer:
            continue
        candidate_endpoint = _resolve_provider_endpoint(candidate)
        if candidate_endpoint and candidate_endpoint != producer_endpoint:
            return candidate
    raise RuntimeError(
        f"Hard abort: no physically distinct verifier available for producer "
        f"{producer!r} (available={available}); asymmetric verification impossible"
    )


def _endpoint_from(obj: Any) -> str | None:
    for attr in _ENDPOINT_ATTRS:
        value = obj.get(attr) if isinstance(obj, dict) else getattr(obj, attr, None)
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], str):
            return value[0]
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _executed_endpoint(result: Any) -> tuple[str, str]:
    """Return (endpoint, source) for the physical execution behind a StructuredResult."""
    candidates: list[Any] = [result]
    attempts = getattr(result, "attempts", None) or []
    candidates.extend(reversed(list(attempts)))
    for candidate in candidates:
        found = _endpoint_from(candidate)
        if found:
            return found, "structured_result"
    resolved = _resolve_provider_endpoint(str(result.provider_used))
    assert resolved, (
        f"Cannot determine executed argv[0]/endpoint for provider_used={result.provider_used!r}"
    )
    return resolved, "resolved_from_provider_used"


def _validated_payload(result: Any, schema: type) -> Any:
    """Extract the validated model from a StructuredResult and re-validate it."""
    for attr in ("value", "data", "parsed", "model"):
        candidate = getattr(result, attr, None)
        if isinstance(candidate, schema):
            revalidated = schema.model_validate(candidate.model_dump())
            assert revalidated == candidate, f"{schema.__name__} round-trip mismatch"
            return candidate
    raise AssertionError(
        f"StructuredResult does not carry a validated {schema.__name__} instance"
    )


def _schema_prompt(schema: type, task: str) -> str:
    return (
        f"{task}\n\nReturn ONLY a single JSON object that validates against the "
        f"Pydantic model `{schema.__name__}` with this JSON schema:\n"
        f"{json.dumps(schema.model_json_schema(), indent=2)}\n"
        "No prose, no markdown outside the JSON."
    )


def _verifier_prompt(key: str, schema: type, producer_provider: str, payload_json: str) -> str:
    # Deferred import: only reached after the COCHEM_LIVE guard has passed.
    from v4_schemas import AuditFeedback

    task = (
        f"You are the independent verifier for CoChem handoff Exchange {key}. "
        f"A different model provider ({producer_provider}) produced the following "
        f"`{schema.__name__}` payload. Audit it for schema conformance, internal "
        "consistency, and signs of spoofed or fabricated content. Score 0-100. "
        "If the verdict is FAIL or SPOOFING_RISK you must list at least one violation.\n\n"
        f"PAYLOAD:\n{payload_json}"
    )
    return _schema_prompt(AuditFeedback, task)


def _physical_inputs_d() -> dict[str, str]:
    """Collect real host facts for Exchange D (git hash + real stdout + sha256)."""
    head = _run_host(["git", "rev-parse", "--short=12", "HEAD"])
    assert head.returncode == 0, f"git rev-parse failed: {head.stderr}"
    git_hash = head.stdout.strip()
    probe = _run_host([sys.executable, "-c", "import sys; print(sys.version.split()[0])"])
    assert probe.returncode == 0, f"python probe failed: {probe.stderr}"
    raw_stdout = probe.stdout.strip()
    stdout_path = EVIDENCE_DIR / "exchange_d_stdout.txt"
    stdout_path.write_text(raw_stdout, encoding="utf-8")
    return {
        "git_diff_hash": git_hash,
        "raw_stdout": raw_stdout,
        "stdout_path": stdout_path.as_posix(),
        "raw_stdout_sha256": hashlib.sha256(raw_stdout.encode("utf-8")).hexdigest(),
    }


def _physical_failure_e() -> str:
    """Produce a genuine Python failure trace for Exchange E diagnostics."""
    proc = _run_host([sys.executable, "-c", "import json\njson.loads('{unterminated')"])
    assert proc.returncode != 0, "Expected the diagnostic probe to fail for real"
    return proc.stderr.strip()


# --------------------------------------------------------------------------- tests


def test_live_handoffs_all_exchanges():
    if os.environ.get("COCHEM_LIVE") != "1":
        raise RuntimeError("COCHEM_LIVE not set")

    # Deferred imports: guard above guarantees collection never depends on them.
    from llm_router import structured_call
    from v4_schemas import (
        AuditFeedback,
        ExecutionChunk,
        ImplementationDossier,
        PhysicsAutopsyReport,
        PlanDossier,
    )

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    providers_path = EVIDENCE_DIR / "providers.json"
    available = _available_providers()

    facts_d = _physical_inputs_d()
    failure_e = _physical_failure_e()

    exchanges: list[tuple[str, str, type, str, str]] = [
        (
            "A",
            "Plan Revision Loop",
            PlanDossier,
            "claude",
            "Produce a plan dossier for CoChem task 196.14: a pytest suite "
            "test_v4_live_handoffs.py that calls llm_router.structured_call for five "
            "handoff schemas and records producer/verifier providers to "
            ".evidence/live_handoffs/providers.json. research_mapping citations must "
            "be non-empty (cite files such as v4_schemas.py or llm_router.py). "
            "file_impact_matrix actions must be CREATE, MODIFY or DELETE.",
        ),
        (
            "B",
            "Audit Feedback Loop",
            AuditFeedback,
            "gemini",
            "Audit this implementation claim: 'test_v4_live_handoffs.py raises "
            "RuntimeError(\"COCHEM_LIVE not set\") when the env var is unset and "
            "never skips.' Acceptance criteria: AC-41 (live calls A-E, provider "
            "asymmetry), AC-50 (providers.json records provider_used and argv[0]). "
            "If verdict is FAIL or SPOOFING_RISK, violations must be non-empty.",
        ),
        (
            "C",
            "Task Dispatch",
            ExecutionChunk,
            "gemini",
            "Dispatch an execution chunk for task_id 196.14 targeting "
            "test_v4_live_handoffs.py and .evidence/live_handoffs/providers.json "
            "with acceptance criteria AC-41 and AC-50. test_spec MUST be an object "
            "containing exactly the keys test_file, physical_inputs, assertions.",
        ),
        (
            "D",
            "Verification Handoff",
            ImplementationDossier,
            "ollama",
            "Produce an implementation dossier using these REAL host values, copied "
            "verbatim (do not alter any character):\n"
            f"git_diff_hash = {facts_d['git_diff_hash']}\n"
            f"raw_stdout = {facts_d['raw_stdout']}\n"
            f"stdout_path = {facts_d['stdout_path']}\n"
            "stdout_truncated = false\n"
            f"raw_stdout_sha256 = {facts_d['raw_stdout_sha256']}\n"
            "fulfilled_ac_list = [\"AC-41\", \"AC-50\"]",
        ),
        (
            "E",
            "Diagnostics Loop",
            PhysicsAutopsyReport,
            "claude",
            "Diagnose this real Python failure trace. failure_stage must be one of "
            "SYNTAX, RUNTIME, TEST_FAIL, SPOOFING. failing_file_line must have the "
            "form '<file>:<line>' taken from the trace.\n\nTRACE:\n" + failure_e,
        ),
    ]

    records: dict[str, dict[str, Any]] = {}
    for index, (key, title, schema, preferred, task) in enumerate(exchanges):
        producer = preferred if preferred in available else available[index % len(available)]
        verifier = _select_verifier(producer, available)
        assert verifier != producer, f"Exchange {key}: verifier selection returned producer"

        producer_res = structured_call(
            schema, producer, _schema_prompt(schema, task), timeout=LIVE_TIMEOUT_S
        )
        produced = _validated_payload(producer_res, schema)

        verifier_res = structured_call(
            AuditFeedback,
            verifier,
            _verifier_prompt(key, schema, str(producer_res.provider_used), produced.model_dump_json()),
            timeout=LIVE_TIMEOUT_S,
        )
        audit = _validated_payload(verifier_res, AuditFeedback)

        producer_used = str(producer_res.provider_used)
        verifier_used = str(verifier_res.provider_used)
        assert producer_used, f"Exchange {key}: producer provider_used empty"
        assert verifier_used, f"Exchange {key}: verifier provider_used empty"
        assert producer_used != verifier_used, (
            f"Exchange {key}: producer and verifier ran on the same provider {producer_used}"
        )

        producer_endpoint, producer_src = _executed_endpoint(producer_res)
        verifier_endpoint, verifier_src = _executed_endpoint(verifier_res)
        assert producer_endpoint != verifier_endpoint, (
            f"Exchange {key}: identical execution endpoint {producer_endpoint}"
        )

        records[key] = {
            "exchange_name": title,
            "schema": schema.__name__,
            "producer_requested": producer,
            "producer_provider": producer_used,
            "producer_endpoint": producer_endpoint,
            "producer_endpoint_source": producer_src,
            "producer_attempts": len(getattr(producer_res, "attempts", None) or []),
            "verifier_requested": verifier,
            "verifier_provider": verifier_used,
            "verifier_endpoint": verifier_endpoint,
            "verifier_endpoint_source": verifier_src,
            "verifier_attempts": len(getattr(verifier_res, "attempts", None) or []),
            "verifier_verdict": audit.verdict,
            "verifier_score": audit.score,
            "asymmetric": producer_used != verifier_used,
        }

    assert set(records) == set(EXCHANGE_KEYS)
    document = {
        "timestamp_utc": time.time(),
        "available_providers": available,
        "exchanges": records,
    }
    tmp_path = providers_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    os.replace(tmp_path, providers_path)


def test_live_handoff_verifier_provider_used():
    if os.environ.get("COCHEM_LIVE") != "1":
        raise RuntimeError("COCHEM_LIVE not set")

    providers_path = EVIDENCE_DIR / "providers.json"
    assert providers_path.is_file(), f"providers.json missing: {providers_path}"
    document = json.loads(providers_path.read_text(encoding="utf-8"))
    assert isinstance(document, dict) and isinstance(document.get("exchanges"), dict)
    exchanges = document["exchanges"]

    for key in EXCHANGE_KEYS:
        assert key in exchanges, f"Exchange {key} missing from providers.json"
        rec = exchanges[key]
        producer_provider = rec.get("producer_provider")
        verifier_provider = rec.get("verifier_provider")
        assert producer_provider and verifier_provider, f"Exchange {key}: provider_used missing"
        assert producer_provider != verifier_provider, (
            f"Exchange {key}: no physical asymmetry ({producer_provider})"
        )
        assert rec.get("asymmetric") is True, f"Exchange {key}: asymmetric flag not true"

        for role in ("producer", "verifier"):
            endpoint = rec.get(f"{role}_endpoint")
            assert isinstance(endpoint, str) and endpoint, f"Exchange {key}: {role}_endpoint missing"
            if endpoint.startswith(("http://", "https://")):
                continue
            # argv[0]: must be a real executable on this host
            assert Path(endpoint).is_file() or shutil.which(endpoint), (
                f"Exchange {key}: {role} argv[0] {endpoint!r} is not a physical binary"
            )
        assert rec["producer_endpoint"] != rec["verifier_endpoint"], (
            f"Exchange {key}: producer/verifier endpoints identical"
        )
