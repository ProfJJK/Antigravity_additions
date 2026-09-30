"""Workspace initialization wizard, micro-silo scaffolding, and atomic rollback."""

import dataclasses
import hashlib
import json
import os
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from cochem.cli.hardware import EnvironmentVerifier, HardwareAuditor, HardwareProfile

MANIFEST_NAME = ".cochem_project.json"

MICRO_SILOS: Tuple[str, ...] = (
    "data/raw",
    "data/processed",
    "models/checkpoints",
    "telemetry/logs",
    "config/methods",
)


class ExistingProjectError(RuntimeError):
    """Raised when target directory already contains a valid .cochem_project.json."""

    def __init__(self, message: str = "Project already exists") -> None:
        super().__init__(message)


class InsufficientDiskSpaceError(RuntimeError):
    """Raised when available disk space is below required minimum threshold."""

    def __init__(self, message: str = "Insufficient disk space") -> None:
        super().__init__(message)


class UnsupportedPythonError(RuntimeError):
    """Raised when the Python runtime is older than the supported minimum."""

    def __init__(self, message: str = "Unsupported Python runtime") -> None:
        super().__init__(message)


class WizardAbortedError(RuntimeError):
    """Raised when the operator declines (or cannot answer) the interactive wizard."""

    def __init__(self, message: str = "Initialization aborted by user") -> None:
        super().__init__(message)


class RollbackError(RuntimeError):
    """Raised when initialization failed and the rollback itself was incomplete.

    ``original`` holds the exception that triggered the rollback and ``failures``
    lists each artifact that could not be cleaned up.
    """

    def __init__(self, original: BaseException, failures: Sequence[str]) -> None:
        self.original = original
        self.failures = tuple(failures)
        super().__init__(
            f"Initialization failed ({type(original).__name__}: {original}) "
            "and rollback was incomplete; leftover artifacts: "
            + "; ".join(self.failures)
        )


def _rollback(
    created_files: List[Path],
    file_backups: Dict[Path, bytes],
    created_dirs: List[Path],
    target_path: Path,
    target_created: bool,
) -> List[str]:
    """Undo partial initialization. Returns descriptions of any cleanup failures."""
    failures: List[str] = []

    for f in reversed(created_files):
        try:
            if f.is_file() or f.is_symlink():
                f.unlink()
        except OSError as err:
            failures.append(f"could not remove file {f}: {err}")

    for f, content in file_backups.items():
        try:
            f.write_bytes(content)
        except OSError as err:
            failures.append(f"could not restore file {f}: {err}")

    for d in reversed(created_dirs):
        try:
            if d.is_dir():
                d.rmdir()
        except OSError as err:
            failures.append(f"could not remove directory {d}: {err}")

    if target_created and target_path.exists():
        shutil.rmtree(target_path, ignore_errors=True)
        if target_path.exists():
            failures.append(f"could not remove target directory {target_path}")

    return failures


def _collect_environment(profile: HardwareProfile) -> Dict[str, Any]:
    """Environment verification record: Python runtime, hardware, and binaries."""
    binaries = EnvironmentVerifier.audit_binaries()
    return {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "compliant": EnvironmentVerifier.verify_python_version(),
        },
        "platform": profile.platform,
        "hardware": profile.to_dict(),
        "binaries": {name: dataclasses.asdict(rec) for name, rec in binaries.items()},
    }


def _interactive_session(
    default_name: str,
    target_path: Path,
    environment: Dict[str, Any],
    input_fn: Callable[[str], str],
) -> str:
    """Show the audit summary, ask for the project name and a go/no-go decision."""
    hardware = environment["hardware"]
    print(f"CoChem init wizard for {target_path}")
    print(
        f"  CPU cores: {hardware['cpu_count']}; "
        f"RAM: {hardware['ram_total_gb']:.1f} GiB "
        f"({hardware['ram_free_gb']:.1f} free); "
        f"disk free: {hardware['disk_free_gb']:.1f} GiB; "
        f"accelerators: {len(hardware['accelerators'])}"
    )
    for name, rec in environment["binaries"].items():
        state = rec["path"] if rec["available"] else "not found"
        print(f"  {name}: {state}")

    try:
        answer = input_fn(f"Project name [{default_name}]: ").strip()
        confirm = input_fn("Proceed with initialization? [Y/n]: ").strip().lower()
    except EOFError as exc:
        raise WizardAbortedError("No interactive input available") from exc
    if confirm in ("n", "no"):
        raise WizardAbortedError()
    return answer or default_name


def run_init_wizard(
    target_dir: Union[str, Path, os.PathLike],
    overwrite: bool = False,
    force: bool = False,
    min_disk_gb: float = 1.0,
    interactive: bool = False,
    input_fn: Callable[[str], str] = input,
) -> Dict[str, Any]:
    """Audit hardware/environment, scaffold micro-silos, seal manifest, roll back on failure.

    The manifest seals only files generated by the wizard itself; pre-existing user
    files under the target are left untouched and untracked.
    """
    target_path = Path(target_dir).resolve()

    manifest_path = target_path / MANIFEST_NAME
    if manifest_path.exists() and not (overwrite or force):
        raise ExistingProjectError(
            f"Directory {target_path} is already an initialized CoChem project."
        )

    # Environment verification
    if not EnvironmentVerifier.verify_python_version():
        raise UnsupportedPythonError(
            f"Python {platform.python_version()} is below the required 3.10"
        )

    # Hardware audit + disk space pre-flight
    profile = HardwareAuditor.probe(target_path)
    if profile.disk_free_gb < min_disk_gb:
        raise InsufficientDiskSpaceError(
            f"Available disk space {profile.disk_free_gb:.2f} GB is below "
            f"minimum required {min_disk_gb:.2f} GB"
        )
    environment = _collect_environment(profile)

    project_name = target_path.name
    if interactive:
        project_name = _interactive_session(
            project_name, target_path, environment, input_fn
        )

    target_created = False
    created_dirs: List[Path] = []
    created_files: List[Path] = []
    file_backups: Dict[Path, bytes] = {}
    managed_files: List[Path] = []

    if not target_path.exists():
        target_path.mkdir(parents=True, exist_ok=False)
        target_created = True
        created_dirs.append(target_path)

    try:

        def _ensure_dir(p: Path) -> None:
            rel_parts = p.relative_to(target_path).parts
            curr = target_path
            for part in rel_parts:
                curr = curr / part
                if not curr.exists():
                    curr.mkdir()
                    created_dirs.append(curr)
                elif not curr.is_dir():
                    raise NotADirectoryError(
                        f"Expected directory at {curr}, found file"
                    )

        for silo in MICRO_SILOS:
            _ensure_dir(target_path / silo)

        methods_dir = target_path / "config" / "methods"
        _ensure_dir(methods_dir)
        template_path = methods_dir / "method_matrix_v4.json"

        if template_path.exists():
            if template_path.is_dir():
                raise IsADirectoryError(
                    f"Cannot write template: {template_path} is a directory"
                )
            file_backups[template_path] = template_path.read_bytes()
        else:
            created_files.append(template_path)

        matrix_v4 = {
            "matrix_version": "4.0.0",
            "dft": {
                "default_functional": "B3LYP",
                "default_basis": "def2-TZVP",
                "dispersion": "D3BJ",
                "grid": "defgrid3",
            },
            "semi_empirical": {
                "default_method": "GFN2-xTB",
                "electronic_temperature": 300.0,
            },
            "force_field": {
                "default_engine": "UFF",
                "opt_max_cycles": 500,
            },
            "convergence_criteria": {
                "energy_tol_hartree": 1e-06,
                "gradient_tol_hartree_bohr": 0.0001,
            },
        }
        template_path.write_text(
            json.dumps(matrix_v4, indent=2), encoding="utf-8"
        )
        managed_files.append(template_path)

        # Hash only files generated by the wizard (never user data)
        file_hashes: Dict[str, str] = {}
        for p in sorted(managed_files):
            rel_str = p.relative_to(target_path).as_posix()
            file_hashes[rel_str] = hashlib.sha256(p.read_bytes()).hexdigest()

        manifest_data: Dict[str, Any] = {
            "project_name": project_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "version": "4.0.0",
            "micro_silos": list(MICRO_SILOS),
            "file_hashes": file_hashes,
            "environment": environment,
        }

        canonical_bytes = json.dumps(
            manifest_data, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        manifest_data["integrity_seal"] = hashlib.sha256(
            canonical_bytes
        ).hexdigest()

        if manifest_path.exists():
            file_backups[manifest_path] = manifest_path.read_bytes()
        else:
            created_files.append(manifest_path)

        manifest_path.write_text(
            json.dumps(manifest_data, indent=2), encoding="utf-8"
        )
        return manifest_data

    except Exception as exc:
        # Atomic rollback; surface any cleanup failure instead of hiding it.
        failures = _rollback(
            created_files, file_backups, created_dirs, target_path, target_created
        )
        if failures:
            raise RollbackError(exc, failures) from exc
        raise
