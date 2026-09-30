"""Task 20.1085 ratification gate: sync CoChem-BASE/CoChem-TORQ, audit, stress, receipt.

Steps (all real, all on disk):
  1. Mirror the storage modules and the ratified suite into every target
     location in CoChem-BASE, CoChem-TORQ and <CoChem root>/tests/storage.
     Every storage package directory that exists in a repository
     (``src/cochem/storage`` and/or the legacy ``cochem/storage``) receives all
     three modules. This prevents a split package layout, and every import root
     resolves ``cochem.storage.pes_store``.
     Existing storage package directories directly under the CoChem root
     (``<CoChem root>/src/cochem/storage`` and ``<CoChem root>/cochem/storage``)
     are refreshed too, so no stale copy of the package can shadow the
     canonical one on ``sys.path``.
     Module source: <workspace>/src/cochem/storage. If the workspace has no
     copy, the copy already present in CoChem-BASE is used.
     Suite source: <workspace>/tests/storage.
  2. Verify byte-identical SHA-256 digests across all copies.
  3. Run anti_spoof_linter.py and ci_tools/mendeleev_ast_linter.py on every copy.
  4. Run the ratified suite with pytest (JUnit XML): 0 failures, 0 errors, 0 skips.
  5. Run an independent 5-process SWMR stress test (1 writer, 4 readers, 500 frames).
  6. Only if every check passes, write
     COCHEM-DELIVERABLE-RECEIPT-TASK-20-108-PRODUCTION-SWMR-HDF5-STORAGE.json
     into the workspace root. On any failure, a stale receipt is removed and the
     exit code is 1.

Repository discovery (same order as the TDD acceptance gate):
  $COCHEM_BASE_ROOT / $COCHEM_TORQ_ROOT, <workspace>/<name>, <CoChem root>/<name>,
  <CoChem root parent>/<name>, D:/__CoChem/<name>, D:/<name>, and finally
  <CoChem root>/GitHub-Repo/<name>.

Usage:  python ci_tools/ratify_swmr_storage_task_20_1085.py [--sync-only]
"""
from __future__ import annotations

import os

os.environ["HDF5_USE_FILE_LOCKING"] = "FALSE"

import argparse  # noqa: E402
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import traceback  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Dict, List, Tuple  # noqa: E402

AGENTIC_DIR = Path(__file__).resolve().parents[1]
COCHEM_ROOT = AGENTIC_DIR.parent


def resolve_repo(dirname: str, env_var: str) -> Path:
    """Locate an external repository using the same order as the TDD gate."""
    candidates: List[Path] = []
    override = os.environ.get(env_var, "").strip()
    if override:
        candidates.append(Path(override))
    candidates.extend(
        [
            AGENTIC_DIR / dirname,
            COCHEM_ROOT / dirname,
            COCHEM_ROOT.parent / dirname,
            Path("D:/__CoChem") / dirname,
            Path("D:/") / dirname,
            COCHEM_ROOT / "GitHub-Repo" / dirname,
        ]
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return COCHEM_ROOT / "GitHub-Repo" / dirname


BASE_REPO = resolve_repo("CoChem-BASE", "COCHEM_BASE_ROOT")
TORQ_REPO = resolve_repo("CoChem-TORQ", "COCHEM_TORQ_ROOT")

SUITE_NAME = "test_swmr_pes_store_production.py"
STORAGE_MODULE_NAMES = ("hdf5_zstd.py", "pes_store.py", "__init__.py")
HASH_KEYS = {
    "hdf5_zstd.py": "hdf5_zstd_sha256",
    "pes_store.py": "pes_store_sha256",
    "__init__.py": "storage_init_sha256",
    SUITE_NAME: "test_swmr_pes_store_production_sha256",
}
RECEIPT_NAME = "COCHEM-DELIVERABLE-RECEIPT-TASK-20-108-PRODUCTION-SWMR-HDF5-STORAGE.json"
ANTI_SPOOF_LINTER = AGENTIC_DIR / "anti_spoof_linter.py"
MENDELEEV_LINTER = AGENTIC_DIR / "ci_tools" / "mendeleev_ast_linter.py"
EXPECTED_FRAMES = 500
EXPECTED_READERS = 4
STRESS_TIMEOUT_S = 120.0


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def storage_candidates(repo: Path, name: str) -> List[Path]:
    return [repo / "src" / "cochem" / "storage" / name, repo / "cochem" / "storage" / name]


def default_storage_dir(repo: Path) -> Path:
    if (repo / "src" / "cochem").is_dir():
        return repo / "src" / "cochem" / "storage"
    if (repo / "cochem").is_dir():
        return repo / "cochem" / "storage"
    return repo / "src" / "cochem" / "storage"


def storage_dirs(repo: Path) -> List[Path]:
    """Return every storage package directory of ``repo`` that already holds a storage module.

    Both import roots (the ``src`` layout and the legacy flat layout) are kept
    in lock-step, so whichever ``cochem`` package Python resolves also contains
    ``pes_store.py``. If no storage directory exists yet, the canonical
    default location is used.
    """
    found = [
        directory
        for directory in (repo / "src" / "cochem" / "storage", repo / "cochem" / "storage")
        if any((directory / name).is_file() for name in STORAGE_MODULE_NAMES)
    ]
    return found if found else [default_storage_dir(repo)]


def workspace_peer_dirs(storage_source: Path) -> List[Path]:
    """Existing storage package directories directly under the CoChem root.

    A stale copy here can shadow the canonical package on ``sys.path``, so it is
    refreshed with the canonical modules. Directories are never created, and the
    canonical source directory itself is excluded.
    """
    peers: List[Path] = []
    source_resolved = storage_source.resolve()
    for directory in (COCHEM_ROOT / "src" / "cochem" / "storage", COCHEM_ROOT / "cochem" / "storage"):
        if directory.is_dir() and directory.resolve() != source_resolved:
            peers.append(directory)
    return peers


def resolve_targets(repo: Path, name: str) -> List[Path]:
    """Return every location in ``repo`` that must hold a copy of storage module ``name``."""
    return [directory / name for directory in storage_dirs(repo)]


def resolve_source(storage_source: Path, name: str) -> Path:
    primary = storage_source / name
    if primary.is_file():
        return primary
    for candidate in storage_candidates(BASE_REPO, name):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("storage source module missing: %s" % primary)


def mirror(source: Path, targets: List[Path], log: List[Dict[str, str]]) -> None:
    src_digest = sha256_of(source)
    for target in targets:
        if target.exists() and target.resolve() == source.resolve():
            continue
        if target.is_file() and sha256_of(target) == src_digest:
            log.append({"source": str(source), "target": str(target), "action": "unchanged"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        log.append({"source": str(source), "target": str(target), "action": "copied"})


def _unique_paths(paths: List[Path]) -> List[Path]:
    seen = set()
    unique: List[Path] = []
    for path in paths:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def synchronize(storage_source: Path, suite_source: Path) -> Tuple[List[Dict[str, str]], Dict[str, List[Path]]]:
    log: List[Dict[str, str]] = []
    copies: Dict[str, List[Path]] = {}
    # Fix the directory sets up front so the layout stays stable during the run.
    base_dirs = storage_dirs(BASE_REPO)
    torq_dirs = storage_dirs(TORQ_REPO)
    peer_dirs = workspace_peer_dirs(storage_source)
    for name in STORAGE_MODULE_NAMES:
        source = resolve_source(storage_source, name)
        targets = _unique_paths([d / name for d in base_dirs + torq_dirs + peer_dirs])
        mirror(source, targets, log)
        copies[name] = [source] + [t for t in targets if not (t.exists() and t.resolve() == source.resolve())]

    if not suite_source.is_file():
        raise FileNotFoundError("ratified suite missing: %s" % suite_source)
    suite_targets = [
        COCHEM_ROOT / "tests" / "storage" / SUITE_NAME,
        BASE_REPO / "tests" / "storage" / SUITE_NAME,
        TORQ_REPO / "tests" / "storage" / SUITE_NAME,
    ]
    for repo in (BASE_REPO, TORQ_REPO):
        legacy = repo / "tests" / SUITE_NAME
        if legacy.is_file():
            suite_targets.append(legacy)
    suite_targets = [t for t in suite_targets if not (t.exists() and t.resolve() == suite_source.resolve())]
    suite_targets = _unique_paths(suite_targets)
    mirror(suite_source, suite_targets, log)
    copies[SUITE_NAME] = [suite_source] + suite_targets
    return log, copies


def verify_digests(copies: Dict[str, List[Path]]) -> Tuple[Dict[str, str], List[str]]:
    failures: List[str] = []
    digests: Dict[str, str] = {}
    for name, paths in copies.items():
        found = sorted({sha256_of(p) for p in paths if p.is_file()})
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            failures.append("%s missing copies: %s" % (name, missing))
        if len(found) != 1:
            failures.append("%s digest divergence: %s" % (name, found))
        else:
            digests[name] = found[0]
    return digests, failures


def run_cmd(cmd: List[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HDF5_USE_FILE_LOCKING"] = "FALSE"
    env["PYTHONIOENCODING"] = "utf-8"
    parts = [str(BASE_REPO / "src"), str(BASE_REPO), str(COCHEM_ROOT)]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def run_linters(files: List[Path]) -> Tuple[Dict[str, int], List[str]]:
    failures: List[str] = []
    codes: Dict[str, int] = {}
    for label, script in (("anti_spoof_linter", ANTI_SPOOF_LINTER), ("mendeleev_ast_linter", MENDELEEV_LINTER)):
        if not script.is_file():
            failures.append("%s missing at %s" % (label, script))
            codes[label] = -1
            continue
        proc = run_cmd([sys.executable, str(script)] + [str(f) for f in files], AGENTIC_DIR, 300)
        codes[label] = proc.returncode
        if proc.returncode != 0:
            failures.append("%s exit %d:\n%s\n%s" % (label, proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:]))
    return codes, failures


def run_suite(suite: Path) -> Tuple[Dict[str, int], List[str]]:
    failures: List[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "junit.xml"
        proc = run_cmd(
            [sys.executable, "-m", "pytest", str(suite), "-q", "-rs", "-p", "no:cacheprovider", "--junitxml=%s" % xml_path],
            suite.parent,
            1200,
        )
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "returncode": proc.returncode}
        if xml_path.is_file():
            root = ET.parse(xml_path).getroot()
            suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
            for node in suites:
                for key in ("tests", "failures", "errors", "skipped"):
                    counts[key] += int(node.get(key, "0"))
        else:
            failures.append("pytest produced no JUnit XML")
    if proc.returncode != 0 or counts["failures"] or counts["errors"] or counts["skipped"] or counts["tests"] == 0:
        failures.append("ratified suite not clean %s:\n%s\n%s" % (counts, proc.stdout[-4000:], proc.stderr[-2000:]))
    return counts, failures


def run_stress(suite: Path) -> Tuple[Dict[str, object], List[str]]:
    spec = importlib.util.spec_from_file_location("cochem_swmr_production_suite", suite)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load ratified suite from %s" % suite)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    work = Path(tempfile.mkdtemp(prefix="swmr_ratify_"))
    try:
        run = module.run_five_process_stress(
            work,
            n_frames=EXPECTED_FRAMES,
            n_readers=EXPECTED_READERS,
            timeout_s=STRESS_TIMEOUT_S,
        )
        metrics = module.summarize_stress(run)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    failures: List[str] = []
    if metrics["total_writer_frames"] != EXPECTED_FRAMES:
        failures.append("writer frames %r" % metrics["total_writer_frames"])
    if metrics["writer_store_frame_count"] != EXPECTED_FRAMES:
        failures.append("writer store frame count %r" % metrics["writer_store_frame_count"])
    if metrics["reader_frames_observed"] != [EXPECTED_FRAMES] * EXPECTED_READERS:
        failures.append("reader frames %r" % metrics["reader_frames_observed"])
    if metrics["reader_final_counts"] != [EXPECTED_FRAMES] * EXPECTED_READERS:
        failures.append("reader final counts %r" % metrics["reader_final_counts"])
    for key in ("file_lock_collisions", "deadlocks", "corrupted_frames", "errors"):
        if metrics[key] != 0:
            failures.append("%s = %r" % (key, metrics[key]))
    if not metrics["all_returncodes_zero"]:
        failures.append("non-zero child return codes")
    if not 0.0 < float(metrics["elapsed_s"]) <= STRESS_TIMEOUT_S:
        failures.append("elapsed %r outside the %.0f s budget" % (metrics["elapsed_s"], STRESS_TIMEOUT_S))
    return metrics, failures


def rel(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(COCHEM_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def workspace_path(path: Path) -> str:
    """Return the path relative to the workspace root when possible, otherwise the absolute POSIX path."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(AGENTIC_DIR.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def artifact_digest_records(copies: Dict[str, List[Path]]) -> List[Dict[str, str]]:
    """Build one path/SHA-256 record for every physical copy, measured from disk now."""
    records: List[Dict[str, str]] = []
    for name in STORAGE_MODULE_NAMES + (SUITE_NAME,):
        paths = copies.get(name, [])
        for index, path in enumerate(paths):
            if not path.is_file():
                continue
            records.append(
                {
                    "artifact": name,
                    "role": "canonical" if index == 0 else "mirror",
                    "path": workspace_path(path) if index == 0 else path.resolve().as_posix(),
                    "sha256": sha256_of(path),
                }
            )
    return records


def ratify(args: argparse.Namespace) -> int:
    receipt_path = AGENTIC_DIR / RECEIPT_NAME
    if receipt_path.is_file():
        receipt_path.unlink()

    log, copies = synchronize(args.storage_source, args.suite_source)
    digests, failures = verify_digests(copies)
    print(json.dumps({"base_repo": str(BASE_REPO), "torq_repo": str(TORQ_REPO), "sync": log, "digests": digests}, indent=2))
    if args.sync_only:
        if failures:
            print("SYNC FAILED:\n" + "\n".join(failures))
        return 1 if failures else 0

    all_files = sorted({p.resolve() for paths in copies.values() for p in paths if p.is_file()})
    linter_codes, lint_failures = run_linters(all_files)
    failures.extend(lint_failures)

    base_suite = BASE_REPO / "tests" / "storage" / SUITE_NAME
    suite_counts, suite_failures = run_suite(base_suite)
    failures.extend(suite_failures)

    metrics, stress_failures = run_stress(base_suite)
    failures.extend(stress_failures)

    if failures:
        print("RATIFICATION FAILED:\n" + "\n\n".join(failures))
        return 1

    # Re-verify digests after the suite and stress runs to confirm nothing changed on disk.
    final_digests, drift = verify_digests(copies)
    if drift or final_digests != digests:
        print("RATIFICATION FAILED: artifacts changed during ratification\n" + "\n".join(drift))
        return 1

    readers_measured = len(metrics["reader_frames_observed"])
    base_modules = [resolve_targets(BASE_REPO, n)[0] for n in STORAGE_MODULE_NAMES]
    receipt = {
        "task_id": "20.108",
        "subtask_id": "20.1085",
        "task_title": "Production SWMR HDF5 Storage Layer & 5-Process IPC Concurrency Stress Suite [M]",
        "status": "COMPLETED",
        "verification_status": "VERIFIED_ZERO_MOCK",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repositories": {
            "workspace": AGENTIC_DIR.resolve().as_posix(),
            "cochem_base": BASE_REPO.resolve().as_posix(),
            "cochem_torq": TORQ_REPO.resolve().as_posix(),
        },
        "architectural_constraints_verified": {
            "constraint_1_5_process_concurrency": "1 exclusive writer + %d concurrent readers (%d ASE EMT H2O frames) completed in %.2fs, all return codes 0"
            % (readers_measured, metrics["total_writer_frames"], metrics["elapsed_s"]),
            "constraint_2_zero_lock_collisions_and_zero_frame_loss": "%d lock collisions, %d corrupted frames, N_written=%d, N_read=%s"
            % (metrics["file_lock_collisions"], metrics["corrupted_frames"], metrics["total_writer_frames"], metrics["reader_frames_observed"]),
            "constraint_3_subprocess_protocol": "every subprocess call passes creationflags=subprocess.CREATE_NO_WINDOW and encoding='utf-8'",
            "constraint_4_multirepo_synchronization": "Identical SHA-256 digests across CoChem-BASE, CoChem-TORQ and canonical sources (%d files)"
            % len(all_files),
            "constraint_5_zero_mock_and_mendeleev_ast": "anti_spoof_linter exit %d, mendeleev_ast_linter exit %d, pytest %d passed / %d skipped"
            % (linter_codes["anti_spoof_linter"], linter_codes["mendeleev_ast_linter"], suite_counts["tests"], suite_counts["skipped"]),
            "constraint_6_two_tier_flush_and_zstd_filters": "Zstd(clevel=3) + shuffle + fletcher32 verified on trajectory tensor, lossless bit-exact roundtrip, chunk <= 256 kB",
        },
        "stress_configuration": {
            "n_frames": EXPECTED_FRAMES,
            "n_readers": readers_measured,
            "n_writers": 1,
            "timeout_budget_s": STRESS_TIMEOUT_S,
        },
        "modules_produced": [rel(p) for p in base_modules],
        "test_suites_produced": [rel(base_suite)],
        "cryptographic_hashes": {HASH_KEYS[name]: digest for name, digest in final_digests.items()},
        "artifact_digests": artifact_digest_records(copies),
        "concurrency_stress_metrics": {
            "total_writer_frames": metrics["total_writer_frames"],
            "writer_store_frame_count": metrics["writer_store_frame_count"],
            "concurrent_readers": readers_measured,
            "reader_frames_observed": metrics["reader_frames_observed"],
            "reader_final_counts": metrics["reader_final_counts"],
            "file_lock_collisions": metrics["file_lock_collisions"],
            "deadlocks": metrics["deadlocks"],
            "corrupted_frames": metrics["corrupted_frames"],
            "errors": metrics["errors"],
            "all_returncodes_zero": metrics["all_returncodes_zero"],
            "elapsed_s": metrics["elapsed_s"],
        },
        "linter_exit_codes": linter_codes,
        "pytest_summary": suite_counts,
        "synchronization_log": log,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print("receipt written: %s" % receipt_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-source", type=Path, default=AGENTIC_DIR / "src" / "cochem" / "storage")
    parser.add_argument("--suite-source", type=Path, default=AGENTIC_DIR / "tests" / "storage" / SUITE_NAME)
    parser.add_argument("--sync-only", action="store_true")
    args = parser.parse_args()
    try:
        return ratify(args)
    except Exception:
        stale = AGENTIC_DIR / RECEIPT_NAME
        if stale.is_file():
            stale.unlink()
        print("RATIFICATION FAILED: unhandled exception\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
