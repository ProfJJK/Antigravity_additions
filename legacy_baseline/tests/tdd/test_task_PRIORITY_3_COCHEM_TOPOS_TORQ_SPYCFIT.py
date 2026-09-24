r"""TDD contract for PRIORITY_3_COCHEM_TOPOS_TORQ_SPYCFIT.

This file is written BEFORE the implementation.  Every test must fail today
(ModuleNotFoundError / assertion) and pass once the modules below exist and
honour the contract.  Nothing under test is mocked: synthetic source fixtures
are real files in ``tmp_path``, HDF5 fixtures are real h5py files, and the
docker CLI is replaced by a *real* stand-in executable (see docker_cli below).

Workspace root = Path(__file__).resolve().parents[2]; WORKSPACE/__agentic/v3 is
inserted at sys.path[0] (the directory has no __init__.py).  Modules are
imported *inside* each test so a missing module fails that test only.

Deliverables
============
  __agentic/v3/audit_engine.py   -> CodebaseAuditor, AuditViolation
  __agentic/v3/docker_runner.py  -> DockerSandboxRunner, DockerCommandConfig,
                                    DockerRunResult, SandboxExecutionError,
                                    SandboxTimeoutError
  __agentic/v3/Dockerfile        -> must additionally pip-install pytest,
                                    mendeleev and h5py (rdkit already present)
  __agentic/v3/docker-compose.yml

API CONTRACT -- audit_engine.py
===============================
@dataclass(frozen=True)
class AuditViolation:
    file_path: str   # path of the offending file
    line: int        # 1-based line number
    col: int         # 0-based column (ast col_offset)
    severity: str    # e.g. "CRITICAL"
    category: str    # e.g. "MOCK_IMPORT", "NOT_IMPLEMENTED_ERROR",
                     #      "EMPTY_PASS_STUB", "MENDELEEV_VIOLATION",
                     #      "SEMANTIC_SPOOF"
    symbol: str      # offending identifier / token
    message: str     # human readable explanation

class CodebaseAuditor(repo_root=None, amnesty_file=None):
  .resolve_repo_path(repo_name) -> Path
        Path(r"D:\__CoChem\GitHub-Repo") / repo_name if that directory
        exists, otherwise a workspace fallback whose .name == repo_name.
  .audit_anti_spoofing(target) -> List[AuditViolation]
        target is a file or a directory (directories are scanned recursively
        for *.py).  Flags with severity "CRITICAL":
          * imports of the stdlib mock package / its MagicMock / patch names
            (some category containing "MOCK"),
          * the patch decorator, MagicMock() instantiation,
          * use of pytest's monkeypatch fixture,
          * raising NotImplementedError (category "NOT_IMPLEMENTED_ERROR"),
          * functions whose body is only ``pass`` (category "EMPTY_PASS_STUB").
        MUST NOT flag: ``class X(Exception): pass`` (exception class bodies)
        or ordinary functions.  Must be AST based: banned tokens occurring in
        string constants are NOT violations (the production modules
        audit_engine.py / docker_runner.py must self-audit to []).
  .audit_mendeleev_mass_mandate(target) -> List[AuditViolation]
        Hard-coded atomic / isotopic masses (dict literals or scalar
        assignments whose name denotes a mass) -> category
        "MENDELEEV_VIOLATION".  Non-mass tables (COVALENT_RADII, VALENCE)
        and dynamic lookups via mendeleev.element(...) are fine.
  .verify_physical_fixture_provenance(path) -> Dict[str, Any]
        keys: "valid" (bool), "format" ("xyz" | "h5" | "py"),
              "violations" (list[AuditViolation]);
        xyz additionally: "atom_count" (int), "elements" (list[str]).
        xyz invalid when: atom-count header mismatch, unknown element symbol,
            degenerate geometry (all atoms coincident).
        h5 invalid when: coordinates are all zero, or provenance metadata
            (units / method / basis / source root attrs) missing.
        py invalid when: synthetic geometry / hessian generators (np.zeros /
            np.ones module-level fixture arrays, sin/cos-generated positions)
            -> category "SEMANTIC_SPOOF".  Scratch work arrays inside real
            numerical functions are NOT spoofs.
  .audit_repo_integrity(repo_name_or_Path) -> Dict
        keys: "repo_path" (Path|str), "has_pyproject" (bool),
              "has_conftest" (bool),
              "mock_intercept_files" (list of paths: sitecustomize.py,
                  usercustomize.py, mocks.py, mock_*.py; ignoring
                  .git/.venv/venv/env/site-packages/node_modules/.trash/
                  __pycache__/.tox/.mypy_cache/.pytest_cache; names that
                  merely contain "mock", e.g. test_*_zero_mock.py
                  enforcement tests, are NOT intercepts),
              "compliant" (bool: has_pyproject and no intercept files).
        Accepts a repo name (resolved via resolve_repo_path) or a Path.

API CONTRACT -- docker_runner.py
================================
@dataclass(frozen=True)
class DockerCommandConfig:
    image: str = "cochem-v3:latest"
    workdir: str = "/workspace"
    user: str = "1000:1000"
    cpus: str = "2.0"
    memory: str = "2g"
    read_only: bool = True
    volume_binding: str = "cochem-data:/workspace/data"
    docker_cli: Tuple[str, ...] = ("docker",)

    docker_cli -- CONTRACT ADDITION.  The argv prefix used to invoke the
    docker CLI.  build_run_command() output starts with list(docker_cli)
    followed by "run".  It exists so the telemetry path of execute() can be
    tested fully offline against a real stand-in CLI process (e.g.
    (sys.executable, "docker_cli_standin.py")) instead of a live daemon.

class DockerSandboxRunner(config=None):
  .build_run_command(command, workdir_override=None, env_vars=None) -> List[str]
        [*docker_cli, "run", "--rm", "--read-only", --user, --cpus,
         --memory, -v cochem-data:/workspace/data, -w <workdir>,
         -e K=V ..., <image>, *command]
        The image appears exactly once; the user command follows it as the
        final len(command) elements, unchanged.  Never --privileged, never
        docker.sock, never host network / host pid, never host bind mounts.
        The input command list is not mutated.
  .execute(command, timeout=300, env_vars=None, check=True) -> DockerRunResult
        Runs exactly build_run_command(...) via subprocess (with
        creationflags=CREATE_NO_WINDOW and encoding='utf-8'); stdout/stderr
        returned raw (no strip / reformat).
        DockerRunResult(exit_code, stdout, stderr, duration_s, passed,
                        timed_out=False)
        non-zero exit & check=True -> SandboxExecutionError(exit_code, stdout,
            stderr, command) exposing attributes of those names.
        timeout -> SandboxTimeoutError (subclass of SandboxExecutionError)
            with attributes timeout_s, stdout, stderr, command.
        check=False -> result returned with passed False.
  SandboxExecutionError subclasses RuntimeError.
"""
from __future__ import annotations

import ast
import dataclasses
import fnmatch
import importlib
import json
import os
import re
import sys
import time
import tomllib
from pathlib import Path

import h5py
import numpy as np
import pytest
import yaml

# ── Path setup ────────────────────────────────────────────────────────────────
WORKSPACE = Path(__file__).resolve().parents[2]
V3_DIR = WORKSPACE / "__agentic" / "v3"
if str(V3_DIR) in sys.path:
    sys.path.remove(str(V3_DIR))
sys.path.insert(0, str(V3_DIR))

EXTERNAL_REPO_ROOT = Path(r"D:\__CoChem\GitHub-Repo")
DEFAULT_IMAGE = "cochem-v3:latest"
NAMED_VOLUME = "cochem-data:/workspace/data"
SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}
BANNED_NETWORK_MODULES = (
    "docker", "requests", "httpx", "urllib.request", "urllib3",
    "http.client", "socket", "aiohttp", "paramiko",
)
PRUNE_DIRS = {
    ".git", ".venv", "venv", "env", "site-packages", "node_modules", ".trash",
    "__pycache__", ".tox", ".mypy_cache", ".pytest_cache",
}
INTERCEPT_PATTERNS = ("sitecustomize.py", "usercustomize.py", "mocks.py",
                      "mock_*.py")


# ── Helpers ───────────────────────────────────────────────────────────────────
def _load(name: str):
    """Import a module under test lazily so failures are per-test."""
    return importlib.import_module(name)


def _flag_values(argv, *flags):
    """Return every value given for any of ``flags`` in argv.

    Supports "--flag value" / "-f value" and "--flag=value" forms, so repeated
    flags (-v/--volume, -e/--env) yield all their values in order.  Callers
    pass only the docker-option slice (before the image) so flags inside the
    user command (e.g. ``python -m``) are never misread.
    """
    values = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        for flag in flags:
            if tok == flag:
                if i + 1 < len(argv):
                    values.append(argv[i + 1])
                    i += 1
                break
            if flag.startswith("--") and tok.startswith(flag + "="):
                values.append(tok[len(flag) + 1:])
                break
        i += 1
    return values


def _write(path: Path, lines) -> Path:
    """Write ``lines`` (joined with LF) to ``path`` as UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _line_of(lines, text, start=1):
    """1-based number of the first line (>= start) whose stripped text
    starts with ``text``."""
    for idx in range(start - 1, len(lines)):
        if lines[idx].strip().startswith(text):
            return idx + 1
    raise AssertionError(f"fixture line not found: {text!r}")


def _same_file(a, b: Path) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def _critical_on(violations, file_path: Path, accepted_lines) -> bool:
    return any(
        v.severity == "CRITICAL"
        and v.line in accepted_lines
        and _same_file(v.file_path, file_path)
        for v in violations
    )


def _is_host_path(source: str) -> bool:
    return (source.startswith(("/", "\\", ".", "~"))
            or re.match(r"^[A-Za-z]:", source) is not None)


# ══════════════════════════════════════════════════════════════════════════════
# T1 — anti-spoofing scanner
# ══════════════════════════════════════════════════════════════════════════════
def test_audit_engine_anti_spoofing_scanner(tmp_path):
    audit_engine = _load("audit_engine")
    auditor = audit_engine.CodebaseAuditor()

    # Synthetic spoofed module. Banned tokens live only in these literals.
    spoof_lines = [
        "import os",
        "import unittest.mock",
        "from unittest.mock import MagicMock, patch",
        "",
        "",
        "class SandboxFault(Exception):",
        "    pass",
        "",
        "",
        "def add(a, b):",
        "    return a + b",
        "",
        "",
        '@patch("os.getcwd")',
        "def patched_cwd(fake_getcwd):",
        "    return fake_getcwd()",
        "",
        "",
        "fake = MagicMock()",
        "",
        "",
        "def test_uses_monkeypatch(monkeypatch):",
        '    monkeypatch.setattr("os.sep", "/")',
        '    assert os.sep == "/"',
        "",
        "",
        "def compute_energy(x):",
        '    raise NotImplementedError("todo")',
        "",
        "",
        "def placeholder():",
        "    pass",
    ]
    spoof = _write(tmp_path / "spoofed_test_module.py", spoof_lines)

    # Line numbers derived programmatically from the written fixture.
    ln_mock_import = _line_of(spoof_lines, "import unittest.mock")
    ln_magic_import = _line_of(spoof_lines, "from unittest.mock import MagicMock")
    ln_patch_deco = _line_of(spoof_lines, "@patch(")
    ln_patch_def = _line_of(spoof_lines, "def patched_cwd")
    ln_magic_call = _line_of(spoof_lines, "fake = MagicMock()")
    ln_mp_def = _line_of(spoof_lines, "def test_uses_monkeypatch")
    ln_mp_set = _line_of(spoof_lines, "monkeypatch.setattr")
    ln_nie_def = _line_of(spoof_lines, "def compute_energy")
    ln_nie_raise = _line_of(spoof_lines, "raise NotImplementedError")
    ln_stub_def = _line_of(spoof_lines, "def placeholder")
    ln_stub_pass = _line_of(spoof_lines, "pass", start=ln_stub_def)
    ln_fault_cls = _line_of(spoof_lines, "class SandboxFault")
    ln_fault_pass = ln_fault_cls + 1
    assert spoof_lines[ln_fault_pass - 1].strip() == "pass"
    ln_add_def = _line_of(spoof_lines, "def add(")
    ln_add_ret = ln_add_def + 1

    violations = auditor.audit_anti_spoofing(spoof)
    assert isinstance(violations, list) and violations, "spoofed module not flagged"
    for v in violations:
        for attr in ("file_path", "line", "col", "severity", "category",
                     "symbol", "message"):
            assert hasattr(v, attr), f"AuditViolation missing {attr}"
        assert isinstance(v, audit_engine.AuditViolation)
        assert 1 <= v.line <= len(spoof_lines), f"line out of range: {v}"

    expectations = {
        "unittest.mock import": {ln_mock_import},
        "MagicMock import": {ln_magic_import},
        "@patch decorator": {ln_patch_deco, ln_patch_def},
        "MagicMock() call": {ln_magic_call},
        "monkeypatch fixture": {ln_mp_def, ln_mp_set},
        "NotImplementedError raise": {ln_nie_raise, ln_nie_def},
        "empty pass stub": {ln_stub_pass, ln_stub_def},
    }
    for label, accepted in expectations.items():
        assert _critical_on(violations, spoof, accepted), (
            f"no CRITICAL violation for {label} on lines {sorted(accepted)}; "
            f"got {[(v.line, v.category) for v in violations]}"
        )

    # Legitimate code must not be flagged.
    flagged_lines = {v.line for v in violations}
    for legit in (ln_fault_cls, ln_fault_pass, ln_add_def, ln_add_ret):
        assert legit not in flagged_lines, f"false positive on line {legit}"

    categories = {v.category for v in violations}
    assert any("MOCK" in c for c in categories), categories
    assert "NOT_IMPLEMENTED_ERROR" in categories, categories
    assert "EMPTY_PASS_STUB" in categories, categories

    # Clean file -> no violations.
    clean = _write(tmp_path / "clean_module.py",
                   ["def real(x):", "    return x * 2.0"])
    assert auditor.audit_anti_spoofing(clean) == []

    # Recursive directory scan (spoofed file nested in a subfolder).
    scan_root = tmp_path / "scan_root"
    nested = _write(scan_root / "sub" / "deeper" / "spoofed_test_module.py",
                    spoof_lines)
    ok_file = _write(scan_root / "ok.py", ["def ok():", "    return 1"])
    dir_violations = auditor.audit_anti_spoofing(scan_root)
    assert any(_same_file(v.file_path, nested) and v.severity == "CRITICAL"
               for v in dir_violations), "directory scan missed nested file"
    assert not any(_same_file(v.file_path, ok_file) for v in dir_violations)

    # Self-audit: production modules must be zero-stub / zero-mock.
    for mod in ("audit_engine.py", "docker_runner.py"):
        target = V3_DIR / mod
        assert target.is_file(), f"missing deliverable {target}"
        assert auditor.audit_anti_spoofing(target) == [], f"{mod} self-audit failed"


# ══════════════════════════════════════════════════════════════════════════════
# T2 — mendeleev mass mandate
# ══════════════════════════════════════════════════════════════════════════════
def test_audit_engine_mendeleev_mass_mandate(tmp_path):
    audit_engine = _load("audit_engine")
    auditor = audit_engine.CodebaseAuditor()

    hard_lines = [
        '"""Hard-coded mass tables (forbidden by the mendeleev mandate)."""',
        'ATOMIC_MASSES = {"H": 1.008, "C": 12.011, "N": 14.007, "O": 15.999}',
        'ISOTOPIC_MASS = {"12C": 12.0, "13C": 13.00335}',
        "CARBON_MASS = 12.011",
        'COVALENT_RADII = {"H": 0.31, "C": 0.76, "O": 0.66}',
        'VALENCE = {"H": 1, "C": 4}',
    ]
    hard = _write(tmp_path / "hardcoded_masses.py", hard_lines)
    violations = auditor.audit_mendeleev_mass_mandate(hard)
    assert isinstance(violations, list)
    mv_lines = {v.line for v in violations if v.category == "MENDELEEV_VIOLATION"}
    for prefix in ("ATOMIC_MASSES", "ISOTOPIC_MASS", "CARBON_MASS"):
        ln = _line_of(hard_lines, prefix)
        assert ln in mv_lines, f"{prefix} (line {ln}) not flagged: {mv_lines}"
    all_lines = {v.line for v in violations}
    for prefix in ("COVALENT_RADII", "VALENCE"):
        ln = _line_of(hard_lines, prefix)
        assert ln not in all_lines, f"false positive on {prefix} (line {ln})"
    for v in violations:
        assert _same_file(v.file_path, hard)

    dynamic = _write(tmp_path / "dynamic_masses.py", [
        "from mendeleev import element",
        "",
        "",
        "def molar_mass(symbols):",
        "    return sum(element(s).atomic_weight for s in symbols)",
        "",
        "",
        'CARBON = element("C").mass',
    ])
    assert auditor.audit_mendeleev_mass_mandate(dynamic) == []

    module_style = _write(tmp_path / "module_style.py", [
        "import mendeleev",
        "",
        'm = mendeleev.element("O").mass',
    ])
    assert auditor.audit_mendeleev_mass_mandate(module_style) == []

    # Cross-check against the real mendeleev database.
    from mendeleev import element
    assert abs(float(element("C").atomic_weight) - 12.011) < 0.01


# ══════════════════════════════════════════════════════════════════════════════
# T3 — physical fixture provenance
# ══════════════════════════════════════════════════════════════════════════════
WATER = [("O", 0.0, 0.0, 0.1173), ("H", 0.0, 0.7572, -0.4692),
         ("H", 0.0, -0.7572, -0.4692)]


def _xyz_lines(n_header, comment, atoms):
    return [str(n_header), comment] + [
        f"{s} {x:.6f} {y:.6f} {z:.6f}" for s, x, y, z in atoms
    ]


def _write_h5(path: Path, coords, with_meta: bool) -> Path:
    with h5py.File(path, "w") as fh:
        ds = fh.create_dataset("coordinates",
                               data=np.asarray(coords, dtype=np.float64))
        ds.attrs.create("elements", ["O", "H", "H"], dtype=h5py.string_dtype())
        if with_meta:
            fh.attrs["units"] = "angstrom"
            fh.attrs["method"] = "B3LYP"
            fh.attrs["basis"] = "def2-TZVP"
            fh.attrs["source"] = "ab-initio"
    return path


def test_audit_engine_physical_fixture_provenance(tmp_path):
    audit_engine = _load("audit_engine")
    auditor = audit_engine.CodebaseAuditor()

    # ── Authentic xyz fixtures ──
    he2 = _write(tmp_path / "he2.xyz", _xyz_lines(
        2, "He-He van der Waals complex R_e = 2.970 Angstrom",
        [("He", 0.0, 0.0, 0.0), ("He", 0.0, 0.0, 2.970)]))
    water = _write(tmp_path / "water.xyz", _xyz_lines(
        3, "H2O B3LYP/def2-TZVP optimized geometry", WATER))
    for path, n, elems in ((he2, 2, ["He", "He"]), (water, 3, ["O", "H", "H"])):
        res = auditor.verify_physical_fixture_provenance(path)
        assert res["format"] == "xyz"
        assert res["valid"] is True, res
        assert res["atom_count"] == n
        assert list(res["elements"]) == elems
        assert res["violations"] == []

    real_he = Path(r"D:\__CoChem\GitHub-Repo\CoChem-TORQ\he_he_dimer.xyz")
    if real_he.exists():
        res = auditor.verify_physical_fixture_provenance(real_he)
        assert res["valid"] is True, res
        assert res["atom_count"] == 2
        assert list(res["elements"]) == ["He", "He"]

    # ── Invalid xyz fixtures ──
    count_mismatch = _write(tmp_path / "mismatch.xyz", _xyz_lines(
        3, "header claims 3 atoms", WATER[:2]))
    unknown_elem = _write(tmp_path / "unknown.xyz", _xyz_lines(
        2, "unknown element", [("Xx", 0.0, 0.0, 0.0), ("H", 0.0, 0.0, 0.97)]))
    degenerate = _write(tmp_path / "degenerate.xyz", _xyz_lines(
        3, "all atoms at origin", [("O", 0.0, 0.0, 0.0), ("H", 0.0, 0.0, 0.0),
                                   ("H", 0.0, 0.0, 0.0)]))
    for bad in (count_mismatch, unknown_elem, degenerate):
        res = auditor.verify_physical_fixture_provenance(bad)
        assert res["format"] == "xyz"
        assert res["valid"] is False, f"{bad.name} wrongly accepted: {res}"
        assert isinstance(res["violations"], list) and res["violations"]

    # ── HDF5 fixtures ──
    coords = [[x, y, z] for _, x, y, z in WATER]
    good_h5 = _write_h5(tmp_path / "water.h5", coords, with_meta=True)
    res = auditor.verify_physical_fixture_provenance(good_h5)
    assert res["format"] == "h5"
    assert res["valid"] is True, res
    assert res["violations"] == []

    zero_h5 = _write_h5(tmp_path / "zeros.h5", np.zeros((3, 3)), with_meta=True)
    res = auditor.verify_physical_fixture_provenance(zero_h5)
    assert res["format"] == "h5"
    assert res["valid"] is False
    assert res["violations"]

    nometa_h5 = _write_h5(tmp_path / "nometa.h5", coords, with_meta=False)
    res = auditor.verify_physical_fixture_provenance(nometa_h5)
    assert res["format"] == "h5"
    assert res["valid"] is False
    assert res["violations"]

    # ── Spoofed generator .py ──
    gen_lines = [
        "import math",
        "import numpy as np",
        "",
        "coords = np.zeros((10, 3))",
        "positions = []",
        "for i in range(10):",
        "    positions.append([math.sin(i), math.cos(i), 0.0])",
        "hessian = np.ones((9, 9))",
    ]
    gen = _write(tmp_path / "spoof_generator.py", gen_lines)
    res = auditor.verify_physical_fixture_provenance(gen)
    assert res["format"] == "py"
    assert res["valid"] is False
    spoof_lines = {v.line for v in res["violations"]
                   if v.category == "SEMANTIC_SPOOF"}
    assert _line_of(gen_lines, "coords = np.zeros") in spoof_lines, spoof_lines
    assert _line_of(gen_lines, "hessian = np.ones") in spoof_lines, spoof_lines
    assert spoof_lines & {_line_of(gen_lines, "positions.append"),
                          _line_of(gen_lines, "for i in range")}, spoof_lines

    # ── Legit numerical scratch work ──
    legit = _write(tmp_path / "legit_scratch.py", [
        "import numpy as np",
        "",
        "",
        "def diag(n):",
        "    work = np.zeros((n, n))",
        "    for i in range(n):",
        "        work[i, i] = float(i + 1)",
        "    return np.linalg.eigvalsh(work)",
    ])
    res = auditor.verify_physical_fixture_provenance(legit)
    assert res["format"] == "py"
    assert not [v for v in res["violations"] if v.category == "SEMANTIC_SPOOF"]
    assert res["valid"] is True, res


# ══════════════════════════════════════════════════════════════════════════════
# T4 — docker run command generation
# ══════════════════════════════════════════════════════════════════════════════
def test_docker_runner_command_generation_and_security_flags():
    dr = _load("docker_runner")
    runner = dr.DockerSandboxRunner()
    user_cmd = ["python", "-m", "pytest", "-q"]
    original = list(user_cmd)
    cmd = runner.build_run_command(user_cmd)

    assert user_cmd == original, "input command list was mutated"
    assert isinstance(cmd, list) and all(isinstance(t, str) for t in cmd)
    assert cmd[:2] == ["docker", "run"]
    assert cmd.count(DEFAULT_IMAGE) == 1
    assert cmd.index(DEFAULT_IMAGE) == len(cmd) - 5
    assert cmd[-4:] == user_cmd

    opts = cmd[:cmd.index(DEFAULT_IMAGE)]  # docker options only
    assert "--rm" in opts
    assert "--read-only" in opts
    assert _flag_values(opts, "--user", "-u") == ["1000:1000"]
    assert _flag_values(opts, "--cpus") == ["2.0"]
    assert _flag_values(opts, "--memory", "-m") == ["2g"]
    volumes = _flag_values(opts, "--volume", "-v")
    assert NAMED_VOLUME in volumes
    for vol in volumes:
        source = vol.split(":", 1)[0]
        assert source == "cochem-data", f"non-named volume source {vol!r}"
        assert not _is_host_path(source)
    for mount in _flag_values(opts, "--mount"):
        assert "type=bind" not in mount
    assert not any(t.startswith("--privileged") for t in cmd)
    assert not any("docker.sock" in t for t in cmd)
    assert "host" not in _flag_values(opts, "--network", "--net")
    assert "host" not in _flag_values(opts, "--pid")
    assert _flag_values(opts, "--workdir", "-w") == ["/workspace"]

    # env vars + workdir override
    cmd2 = runner.build_run_command(
        user_cmd, workdir_override="/workspace/data",
        env_vars={"COCHEM_DISABLE_SANDBOX_CHECK": "1"})
    opts2 = cmd2[:cmd2.index(DEFAULT_IMAGE)]
    assert "COCHEM_DISABLE_SANDBOX_CHECK=1" in _flag_values(opts2, "--env", "-e")
    assert _flag_values(opts2, "--workdir", "-w") == ["/workspace/data"]
    assert cmd2[-4:] == user_cmd

    # custom config reflected
    custom = dr.DockerCommandConfig(cpus="1.5", memory="1g", image="cochem-v3:test")
    cmd3 = dr.DockerSandboxRunner(custom).build_run_command(user_cmd)
    assert cmd3.count("cochem-v3:test") == 1
    assert DEFAULT_IMAGE not in cmd3
    opts3 = cmd3[:cmd3.index("cochem-v3:test")]
    assert _flag_values(opts3, "--cpus") == ["1.5"]
    assert _flag_values(opts3, "--memory", "-m") == ["1g"]
    assert cmd3[-4:] == user_cmd

    # config defaults + frozen
    cfg = dr.DockerCommandConfig()
    assert cfg.image == DEFAULT_IMAGE
    assert cfg.workdir == "/workspace"
    assert cfg.user == "1000:1000"
    assert cfg.cpus == "2.0"
    assert cfg.memory == "2g"
    assert cfg.read_only is True
    assert cfg.volume_binding == NAMED_VOLUME
    assert cfg.docker_cli == ("docker",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(cfg, "image", "evil:latest")


# ══════════════════════════════════════════════════════════════════════════════
# T5 — execute() telemetry against a real docker-CLI stand-in process
# ══════════════════════════════════════════════════════════════════════════════
# Real stand-in for the external docker CLI (the container runtime), not a
# replacement of code under test.  It records its argv and runs
# `python -c CODE` in-process so a timeout kill terminates exactly one process
# and no orphaned grandchild keeps the pipes open.
STANDIN_SOURCE = "\n".join([
    "import json",
    "import sys",
    "from pathlib import Path",
    "",
    "argv = sys.argv[1:]",
    'Path(__file__).with_suffix(".argv.json").write_text(json.dumps(argv), encoding="utf-8")',
    'IMAGE = "cochem-v3:latest"',
    "if IMAGE not in argv:",
    '    sys.stderr.write("standin: image not found in argv" + chr(10))',
    "    sys.exit(125)",
    "inner = argv[argv.index(IMAGE) + 1:]",
    'if inner[:2] == ["python", "-c"] and len(inner) >= 3:',
    '    exec(compile(inner[2], "<container>", "exec"), {"__name__": "__main__"})',
    "    sys.exit(0)",
    'sys.stderr.write("standin: unsupported container command " + repr(inner) + chr(10))',
    "sys.exit(127)",
    "",
])


def test_docker_runner_telemetry_and_exit_handling(tmp_path):
    dr = _load("docker_runner")
    assert issubclass(dr.SandboxTimeoutError, dr.SandboxExecutionError)
    assert issubclass(dr.SandboxExecutionError, RuntimeError)

    standin = tmp_path / "docker_cli_standin.py"
    with open(standin, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(STANDIN_SOURCE)
    argv_record = standin.with_suffix(".argv.json")
    config = dr.DockerCommandConfig(docker_cli=(sys.executable, str(standin)))
    runner = dr.DockerSandboxRunner(config)

    # (a) success -- raw telemetry preserved byte-for-byte.  ascii() keeps the
    # argv pure ASCII (control chars / non-ASCII passed as escape sequences).
    expected_out = "  raw line\twith  spacing\n\x1b[31mANSI\x1b[0m \u00c5 \u2713\n"
    expected_err = "warning: raw stderr\n"
    code_ok = (
        "import sys; "
        f"sys.stdout.buffer.write({ascii(expected_out)}.encode('utf-8')); "
        f"sys.stderr.buffer.write({ascii(expected_err.encode('utf-8'))}); "
        "sys.stdout.buffer.flush(); sys.stderr.buffer.flush(); sys.exit(0)"
    )
    cmd_ok = ["python", "-c", code_ok]
    result = runner.execute(cmd_ok, timeout=60)
    assert result.exit_code == 0
    assert result.passed is True
    assert result.timed_out is False
    assert result.stdout == expected_out
    assert result.stderr == expected_err
    assert isinstance(result.duration_s, float) and result.duration_s >= 0.0
    with open(argv_record, "r", encoding="utf-8") as fh:
        recorded = json.load(fh)
    assert recorded == runner.build_run_command(cmd_ok)[2:]

    # (b) failure -- exit code 3
    code_fail = (
        f"import sys; sys.stdout.write({ascii('partial output' + chr(10))}); "
        f"sys.stderr.write({ascii('fatal: boom' + chr(10))}); "
        "sys.stdout.flush(); sys.stderr.flush(); sys.exit(3)"
    )
    cmd_fail = ["python", "-c", code_fail]
    built_fail = runner.build_run_command(cmd_fail)
    with pytest.raises(dr.SandboxExecutionError) as exc_info:
        runner.execute(cmd_fail, timeout=60, check=True)
    exc = exc_info.value
    assert not isinstance(exc, dr.SandboxTimeoutError)
    assert exc.exit_code == 3
    assert exc.stdout == "partial output\n"
    assert exc.stderr == "fatal: boom\n"
    assert exc.command == built_fail

    res_fail = runner.execute(cmd_fail, timeout=60, check=False)
    assert res_fail.exit_code == 3
    assert res_fail.passed is False
    assert res_fail.stdout == "partial output\n"
    assert res_fail.stderr == "fatal: boom\n"

    # (c) timeout
    code_hang = (
        f"import sys, time; sys.stdout.write({ascii('started' + chr(10))}); "
        "sys.stdout.flush(); time.sleep(60)"
    )
    cmd_hang = ["python", "-c", code_hang]
    built_hang = runner.build_run_command(cmd_hang)
    t0 = time.monotonic()
    with pytest.raises(dr.SandboxTimeoutError) as to_info:
        runner.execute(cmd_hang, timeout=2)
    elapsed = time.monotonic() - t0
    to_exc = to_info.value
    assert isinstance(to_exc, dr.SandboxExecutionError)
    assert isinstance(to_exc, RuntimeError)
    assert to_exc.timeout_s == 2
    assert to_exc.command == built_hang
    assert hasattr(to_exc, "stdout") and hasattr(to_exc, "stderr")
    assert elapsed < 30.0, f"timeout not enforced ({elapsed:.1f}s)"


# ══════════════════════════════════════════════════════════════════════════════
# T6 — Dockerfile / docker-compose sandbox specification
# ══════════════════════════════════════════════════════════════════════════════
def _dockerfile_instructions(text):
    """Return [(INSTRUCTION, args)] with continuations joined, comments and
    blank lines (including blank lines inside continuations) dropped."""
    logical, buf = [], ""
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.endswith("\\"):
            buf += s[:-1] + " "
            continue
        buf += s
        logical.append(buf.strip())
        buf = ""
    if buf.strip():
        logical.append(buf.strip())
    out = []
    for line in logical:
        parts = line.split(None, 1)
        out.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
    return out


def _pip_packages(run_args):
    """Package names installed by ``pip install`` segments of a RUN body."""
    pkgs = set()
    for segment in re.split(r"&&|\|\||;", run_args):
        toks = [t.strip("'\"") for t in segment.split()]
        for j, tok in enumerate(toks):
            if tok == "install" and j > 0 and re.search(r"pip3?$", toks[j - 1]):
                for pkg in toks[j + 1:]:
                    if not pkg or pkg.startswith("-"):
                        continue
                    name = re.split(r"[<>=!~\[]", pkg)[0].lower()
                    if name:
                        pkgs.add(name)
    return pkgs


def _compose_volume_source(entry):
    if isinstance(entry, dict):
        return str(entry.get("source", ""))
    return str(entry).split(":", 1)[0]


def test_dockerfile_and_compose_sandbox_specifications():
    dr = _load("docker_runner")

    with open(V3_DIR / "Dockerfile", "r", encoding="utf-8") as fh:
        instructions = _dockerfile_instructions(fh.read())
    froms = [f"FROM {a}" for i, a in instructions if i == "FROM"]
    assert froms and re.match(r"^FROM\s+python:3\.12\S*", froms[0]), froms
    users = [a.strip() for i, a in instructions if i == "USER"]
    assert users and users[-1] in {"1000", "1000:1000"}, users
    installed = set()
    for inst, args in instructions:
        if inst == "RUN":
            installed |= _pip_packages(args)
    for required in ("rdkit", "pytest", "mendeleev", "h5py"):
        assert required in installed, \
            f"Dockerfile does not pip install {required}: {sorted(installed)}"

    with open(V3_DIR / "docker-compose.yml", "r", encoding="utf-8") as fh:
        compose = yaml.safe_load(fh)
    services = compose.get("services") or {}
    assert services, "no services defined"
    for name, svc in services.items():
        assert svc.get("read_only") is True, f"{name} not read_only"
        limits = ((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}
        assert "cpus" in limits and float(limits["cpus"]) > 0, name
        assert re.match(r"^\d+(\.\d+)?[kKmMgG][bB]?$",
                        str(limits.get("memory", ""))), name
        vols = svc.get("volumes") or []
        assert NAMED_VOLUME in vols, f"{name} missing {NAMED_VOLUME}"
        for entry in vols:
            assert "docker.sock" not in str(entry), name
            assert not _is_host_path(_compose_volume_source(entry)), \
                f"{name} host bind {entry!r}"
        assert svc.get("privileged") is not True, name
    assert "cochem-data" in (compose.get("volumes") or {})

    worker_limits = services["kanban-worker"]["deploy"]["resources"]["limits"]
    cfg = dr.DockerCommandConfig()
    assert float(worker_limits["cpus"]) == float(cfg.cpus)
    assert str(worker_limits["memory"]).lower() == cfg.memory


# ══════════════════════════════════════════════════════════════════════════════
# T7 — subprocess flags, no network deps, router fallback invariants
# ══════════════════════════════════════════════════════════════════════════════
def _subprocess_calls(tree):
    """Yield ast.Call nodes invoking subprocess run/Popen/call/check_*."""
    module_aliases = {"subprocess"}
    bare_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "subprocess":
                    module_aliases.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for a in node.names:
                if a.name in SUBPROCESS_FUNCS:
                    bare_names.add(a.asname or a.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr in SUBPROCESS_FUNCS
                and isinstance(f.value, ast.Name) and f.value.id in module_aliases):
            yield node
        elif isinstance(f, ast.Name) and f.id in bare_names:
            yield node


def _creationflags_ok(kw_value) -> bool:
    if "CREATE_NO_WINDOW" in ast.unparse(kw_value):
        return True
    return any(isinstance(n, ast.Constant) and n.value == 0x08000000
               for n in ast.walk(kw_value))


def _banned_import(name: str) -> bool:
    return any(name == b or name.startswith(b + ".") for b in BANNED_NETWORK_MODULES)


def test_subprocess_security_flags_and_router_invariants():
    assert (V3_DIR / "audit_engine.py").is_file()
    assert (V3_DIR / "docker_runner.py").is_file()

    runner_call_count = 0
    for py in sorted(V3_DIR.glob("*.py")):
        with open(py, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=str(py))
        for call in _subprocess_calls(tree):
            kws = {k.arg: k.value for k in call.keywords if k.arg}
            where = f"{py.name}:{call.lineno}"
            assert "creationflags" in kws and _creationflags_ok(kws["creationflags"]), where
            enc = kws.get("encoding")
            assert isinstance(enc, ast.Constant) and enc.value == "utf-8", where
            if py.name == "docker_runner.py":
                runner_call_count += 1
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert not _banned_import(a.name), f"{py.name} imports {a.name}"
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                assert not _banned_import(node.module), f"{py.name} imports {node.module}"
                for a in node.names:
                    assert not _banned_import(f"{node.module}.{a.name}"), \
                        f"{py.name} imports {node.module}.{a.name}"
        if py.name == "docker_runner.py":
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert "docker.sock" not in node.value
                    assert "tcp://" not in node.value
    assert runner_call_count >= 1, "docker_runner.py has no subprocess call"

    # Runtime: importing must not pull in the docker SDK / network clients.
    # Drop cached copies so the import really executes under observation.
    for mod in ("docker_runner", "audit_engine"):
        sys.modules.pop(mod, None)
    docker_preloaded = "docker" in sys.modules
    before = set(sys.modules)
    dr = _load("docker_runner")
    _load("audit_engine")
    added = set(sys.modules) - before
    for name in added:
        assert not name.startswith(("docker.", "requests", "httpx", "urllib3",
                                    "aiohttp")), f"network module imported: {name}"
    if not docker_preloaded:
        assert "docker" not in sys.modules
    built = dr.DockerSandboxRunner().build_run_command(["python", "-V"])
    assert built[-2:] == ["python", "-V"]

    # Router invariants (AST only -- importing the router has side effects).
    candidates = [Path(r"D:\__CoChem\__agentic\llm_router.py"),
                  WORKSPACE / "__agentic" / "llm_router.py",
                  WORKSPACE / "llm_router.py"]
    existing = [c for c in candidates if c.is_file()]
    assert existing, "no llm_router.py found"
    for router in existing:
        with open(router, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=str(router))
        registry = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "MODEL_REGISTRY"
                    for t in node.targets):
                registry = node.value
            elif (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                  and node.target.id == "MODEL_REGISTRY" and node.value is not None):
                registry = node.value
            if registry is not None:
                break
        assert registry is not None, f"MODEL_REGISTRY not found in {router}"
        found = 0
        for sub in ast.walk(registry):
            if not isinstance(sub, ast.Dict):
                continue
            for key, value in zip(sub.keys, sub.values):
                if isinstance(key, ast.Constant) and key.value == "fallbacks":
                    chain = [tuple(e) for e in ast.literal_eval(value)]
                    assert chain, f"empty fallbacks in {router}"
                    assert chain[-1] == ("halt", "graceful"), f"{router}: {chain}"
                    assert chain.count(("halt", "graceful")) == 1, f"{router}: {chain}"
                    found += 1
        assert found >= 1, f"no fallbacks entries in {router}"


# ══════════════════════════════════════════════════════════════════════════════
# T8 — repo integrity across the CoChem sibling repositories
# ══════════════════════════════════════════════════════════════════════════════
REPOS = ["CoChem-BASE", "CoChem-TOPOS", "CoChem-TORQ", "CoChem-SpycFit"]


def _independent_intercepts(root: Path):
    """Independent os.walk scan for import-intercept / mock files."""
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        for fn in filenames:
            if any(fnmatch.fnmatch(fn, pat) for pat in INTERCEPT_PATTERNS):
                hits.append(Path(dirpath) / fn)
    return hits


def test_repo_codebase_integrity_and_anti_spoof_compliance(tmp_path):
    audit_engine = _load("audit_engine")
    auditor = audit_engine.CodebaseAuditor()

    for name in REPOS:
        p = Path(auditor.resolve_repo_path(name))
        assert p.name == name
        external = EXTERNAL_REPO_ROOT / name
        if external.is_dir():
            assert p.resolve() == external.resolve()
        assert p.is_dir(), f"repo {name} not found at {p}"

        report = auditor.audit_repo_integrity(name)
        assert Path(report["repo_path"]).resolve() == p.resolve()
        assert report["has_pyproject"] is True
        assert (p / "pyproject.toml").is_file()
        with open(p / "pyproject.toml", "rb") as fh:
            manifest = tomllib.load(fh)
        assert {"project", "tool", "build-system"} & set(manifest), name
        # conftest.py is optional (BASE and SpycFit legitimately lack one).
        assert report["has_conftest"] == (p / "conftest.py").is_file()
        assert isinstance(report["mock_intercept_files"], list)
        assert report["mock_intercept_files"] == [], report["mock_intercept_files"]
        assert _independent_intercepts(p) == [], name
        assert report["compliant"] is True, report

    # ── Synthetic clean repo ──
    clean = tmp_path / "clean_repo"
    _write(clean / "pyproject.toml", ["[project]", 'name = "clean"', 'version = "0.1"'])
    _write(clean / "conftest.py", [
        "import pytest",
        "",
        "",
        "@pytest.fixture",
        "def bond_length():",
        "    return 0.9572",
    ])
    _write(clean / "src" / "pkg" / "core.py", [
        "def reduced_mass(m1, m2):",
        "    return m1 * m2 / (m1 + m2)",
    ])
    rep = auditor.audit_repo_integrity(clean)
    assert Path(rep["repo_path"]).resolve() == clean.resolve()
    assert rep["has_pyproject"] is True
    assert rep["has_conftest"] is True
    assert rep["mock_intercept_files"] == []
    assert rep["compliant"] is True

    # ── Synthetic dirty repo ──
    dirty = tmp_path / "dirty_repo"
    _write(dirty / "pyproject.toml", ["[project]", 'name = "dirty"', 'version = "0.1"'])
    _write(dirty / "conftest.py", [
        "import pytest",
        "",
        "",
        "@pytest.fixture",
        "def temperature():",
        "    return 298.15",
    ])
    mock_xtb = _write(dirty / "tests" / "mock_xtb.py",
                      ["def energy():", "    return -5.07"])
    sitecust = _write(dirty / "sitecustomize.py",
                      ["import sys", "sys.dont_write_bytecode = True"])
    # Zero-mock ENFORCEMENT test: name contains "mock" but is not an intercept.
    zero_mock = _write(dirty / "tests" / "test_zero_mock_enforcement.py", [
        "import sys",
        "",
        "",
        "def no_mock_modules_loaded():",
        "    return 'unittest.mock' not in sys.modules",
    ])
    # Inside a pruned virtualenv: must be ignored.
    _write(dirty / ".venv" / "Lib" / "site-packages" / "mock_ignored.py",
           ["VALUE = 1"])
    rep = auditor.audit_repo_integrity(dirty)
    assert {Path(x).resolve() for x in rep["mock_intercept_files"]} == {
        mock_xtb.resolve(), sitecust.resolve()}
    assert zero_mock.resolve() not in {
        Path(x).resolve() for x in rep["mock_intercept_files"]}
    assert rep["compliant"] is False

    # ── Synthetic repo without a manifest ──
    bare = tmp_path / "no_manifest_repo"
    _write(bare / "main.py", ["def main():", "    return 0"])
    rep = auditor.audit_repo_integrity(bare)
    assert rep["has_pyproject"] is False
    assert rep["compliant"] is False
