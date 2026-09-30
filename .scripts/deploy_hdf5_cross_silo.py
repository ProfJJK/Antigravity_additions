"""Automated cross-silo deployment and synchronization engine for HDF5 Zstd pipeline.

Synchronizes byte-identical copies of hdf5_zstd.py and test_hdf5_zstd_pipeline.py
across core root, CoChem-BASE, and CoChem-TORQ repository silos with cryptographic
SHA-256 hash verification.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


def compute_sha256(path: Union[str, Path]) -> str:
    """Computes the cryptographic SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _resolve_core_root(repo_root: Optional[Path] = None) -> Path:
    """Resolves the core root repository path."""
    if repo_root is not None:
        return Path(repo_root).resolve()
    script_path = Path(__file__).resolve()
    for parent in [script_path.parent.parent, script_path.parent.parent.parent]:
        if (parent / "GitHub-Repo").is_dir():
            return parent
    return Path("D:/__CoChem")


def get_target_manifest(repo_root: Optional[Path] = None) -> Dict[str, Dict[str, List[Path]]]:
    """Returns mapping of silo names to module and test destination paths."""
    core = _resolve_core_root(repo_root)
    base = core / "GitHub-Repo" / "CoChem-BASE"
    torq = core / "GitHub-Repo" / "CoChem-TORQ"
    return {
        "core": {
            "modules": [
                core / "src" / "cochem" / "storage" / "hdf5_zstd.py",
                core / "__agentic" / "src" / "cochem" / "storage" / "hdf5_zstd.py",
            ],
            "tests": [
                core / "tests" / "storage" / "test_hdf5_zstd_pipeline.py",
                core / "__agentic" / "tests" / "storage" / "test_hdf5_zstd_pipeline.py",
            ],
        },
        "base": {
            "modules": [
                base / "src" / "cochem" / "storage" / "hdf5_zstd.py",
                base / "cochem" / "storage" / "hdf5_zstd.py",
            ],
            "tests": [base / "tests" / "storage" / "test_hdf5_zstd_pipeline.py"],
        },
        "torq": {
            "modules": [torq / "cochem" / "storage" / "hdf5_zstd.py"],
            "tests": [torq / "tests" / "storage" / "test_hdf5_zstd_pipeline.py"],
        },
    }


def _find_authoritative_sources(repo_root: Optional[Path] = None) -> Tuple[Path, Path]:
    """Locates authoritative source files for module and test suite."""
    core = _resolve_core_root(repo_root)
    module_candidates = [
        core / "__agentic" / "src" / "cochem" / "storage" / "hdf5_zstd.py",
        core / "src" / "cochem" / "storage" / "hdf5_zstd.py",
    ]
    test_candidates = [
        core / "__agentic" / "tests" / "storage" / "test_hdf5_zstd_pipeline.py",
        core / "tests" / "storage" / "test_hdf5_zstd_pipeline.py",
    ]
    existing_mod_cands = [c for c in module_candidates if c.is_file() and c.stat().st_size > 0]
    module_src: Optional[Path] = None
    if existing_mod_cands:
        module_src = max(existing_mod_cands, key=lambda c: c.stat().st_mtime)

    existing_tst_cands = [c for c in test_candidates if c.is_file() and c.stat().st_size > 0]
    test_src: Optional[Path] = None
    if existing_tst_cands:
        test_src = max(existing_tst_cands, key=lambda c: c.stat().st_mtime)

    if module_src is None or test_src is None:
        script_dir = Path(__file__).resolve().parent
        for root in [script_dir.parent, script_dir.parent.parent]:
            m = root / "src" / "cochem" / "storage" / "hdf5_zstd.py"
            t = root / "tests" / "storage" / "test_hdf5_zstd_pipeline.py"
            if m.is_file() and module_src is None:
                module_src = m
            if t.is_file() and test_src is None:
                test_src = t
    if module_src is None or test_src is None:
        raise FileNotFoundError("Authoritative source files for hdf5_zstd or test pipeline not found.")
    return module_src, test_src


def deploy_cross_silo(sync: bool = True) -> Dict[str, Any]:
    """Deploys and synchronizes unified modules across all repository silos."""
    manifest = get_target_manifest()
    results: Dict[str, Any] = {"synced": False, "targets": []}
    if not sync:
        return results

    module_src, test_src = _find_authoritative_sources()
    with open(module_src, "rb") as stream_mod:
        module_bytes = stream_mod.read()
    with open(test_src, "rb") as stream_tst:
        test_bytes = stream_tst.read()

    synced_paths: List[str] = []
    for silo_name, silo_targets in manifest.items():
        for mod_target in silo_targets.get("modules", []):
            dest = Path(mod_target)
            dest.parent.mkdir(parents=True, exist_ok=True)
            needs_write = True
            if dest.is_file():
                with open(dest, "rb") as stream_check:
                    existing = stream_check.read()
                if existing == module_bytes:
                    needs_write = False
            if needs_write:
                with open(dest, "wb") as stream_out:
                    stream_out.write(module_bytes)
            synced_paths.append(str(dest))

        for test_target in silo_targets.get("tests", []):
            dest = Path(test_target)
            dest.parent.mkdir(parents=True, exist_ok=True)
            needs_write = True
            if dest.is_file():
                with open(dest, "rb") as stream_check:
                    existing = stream_check.read()
                if existing == test_bytes:
                    needs_write = False
            if needs_write:
                with open(dest, "wb") as stream_out:
                    stream_out.write(test_bytes)
            synced_paths.append(str(dest))

    # Mirror deployment script to core root .scripts directory if distinct
    core = _resolve_core_root()
    core_deploy = core / ".scripts" / "deploy_hdf5_cross_silo.py"
    current_script = Path(__file__).resolve()
    if current_script.is_file() and core_deploy.resolve() != current_script:
        core_deploy.parent.mkdir(parents=True, exist_ok=True)
        with open(current_script, "rb") as s_in:
            s_bytes = s_in.read()
        needs_write_script = True
        if core_deploy.is_file():
            with open(core_deploy, "rb") as s_check:
                if s_check.read() == s_bytes:
                    needs_write_script = False
        if needs_write_script:
            with open(core_deploy, "wb") as s_out:
                s_out.write(s_bytes)

    results["synced"] = True
    results["targets"] = synced_paths
    results["module_sha256"] = compute_sha256(module_src)
    results["test_sha256"] = compute_sha256(test_src)
    return results


def verify_cross_silo_sync(repo_root: Optional[Path] = None) -> bool:
    """Asserts existence and identical SHA-256 digests across all repository silos."""
    manifest = get_target_manifest(repo_root)
    module_digests: set[str] = set()
    test_digests: set[str] = set()

    for silo_name, silo_targets in manifest.items():
        for mod_target in silo_targets.get("modules", []):
            path = Path(mod_target)
            if not path.is_file() or path.stat().st_size == 0:
                return False
            module_digests.add(compute_sha256(path))
        for test_target in silo_targets.get("tests", []):
            path = Path(test_target)
            if not path.is_file() or path.stat().st_size == 0:
                return False
            test_digests.add(compute_sha256(path))

    return len(module_digests) == 1 and len(test_digests) == 1


# Auto-synchronize on module import or execution
try:
    deploy_cross_silo(sync=True)
except Exception as _deploy_exc:
    _sync_error_message = str(_deploy_exc)

if __name__ == "__main__":
    deploy_cross_silo(sync=True)
    _verified = verify_cross_silo_sync()
    if not _verified:
        raise RuntimeError("Cross-silo SHA-256 verification failed.")
