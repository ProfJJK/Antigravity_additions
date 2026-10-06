"""Bounded source snapshots and a journalled, independently verified release switch.

This module never executes a candidate. Its caller must terminate the repair job
and snapshot its files into SYSTEM-owned storage before running validation. The
release root, pointer and journal must be inaccessible to repair accounts. On
Windows the installer and ``protect_callback`` enforce that ACL boundary; chmod
alone is deliberately not treated as a Windows security boundary.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import threading
import uuid


MAX_FILES = 10_000
MAX_BYTES = 256 * 1024 * 1024
_HASH = re.compile(r"^[0-9a-f]{64}$")
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I)
_STATES = frozenset({"PREPARED", "ACTIVATING", "VERIFYING", "COMMITTED",
                     "ROLLING_BACK", "ROLLED_BACK", "ROLLBACK_FAILED"})
_TERMINAL = frozenset({"COMMITTED", "ROLLED_BACK"})


class ReleaseError(RuntimeError):
    """The source or release transaction did not satisfy its trust contract."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _safe_relative(name: str) -> str:
    if not isinstance(name, str) or not name or name == "." or "\\" in name or ":" in name:
        raise ReleaseError("Source names must be unambiguous relative Windows paths")
    value = PurePosixPath(name)
    if value.is_absolute() or value.as_posix() != name:
        raise ReleaseError("Source names must be canonical relative paths")
    for part in value.parts:
        if part in {".", ".."} or part.endswith((" ", ".")) or _RESERVED.fullmatch(part):
            raise ReleaseError("Source contains an unsafe Windows path component")
        if any(ord(char) < 32 or char in '<>"|?*' for char in part):
            raise ReleaseError("Source contains an unsafe Windows filename")
    return name


def _stat_plain(path: Path, *, directory: bool = False) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ReleaseError(f"Links and reparse points are forbidden: {path.name}")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ReleaseError(f"Expected a plain directory: {path.name}")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ReleaseError(f"Only regular, singly linked files are allowed: {path.name}")
    return info


def _plain_ancestors(path: Path) -> None:
    for ancestor in reversed(path.absolute().parents):
        _stat_plain(ancestor, directory=True)


def _excluded(relative: str, *, directory: bool) -> bool:
    return (directory and PurePosixPath(relative).name == "__pycache__") or (
        not directory and relative.endswith(".pyc"))


def _scan(root: Path, *, max_files: int, max_bytes: int,
          directory_names: list[str] | None = None) -> list[tuple[str, Path, os.stat_result]]:
    root = Path(root).absolute()
    _plain_ancestors(root)
    _stat_plain(root, directory=True)
    if max_files < 1 or max_bytes < 1:
        raise ValueError("Snapshot bounds must be positive")
    result: list[tuple[str, Path, os.stat_result]] = []
    total = 0
    seen: set[str] = set()
    pending = [(root, "")]
    directories = 0
    entries_seen = 0
    while pending:
        directory, prefix = pending.pop()
        _stat_plain(directory, directory=True)
        with os.scandir(directory) as entries:
            for entry in entries:
                entries_seen += 1
                if entries_seen > max_files * 2:
                    raise ReleaseError("Source exceeds its total directory entry limit")
                relative = _safe_relative(prefix + entry.name)
                folded = relative.casefold()
                if folded in seen:
                    raise ReleaseError("Source contains case-insensitive path collisions")
                seen.add(folded)
                path = Path(entry.path)
                info = path.lstat()
                is_directory = stat.S_ISDIR(info.st_mode)
                info = _stat_plain(path, directory=is_directory)
                if _excluded(relative, directory=is_directory):
                    continue
                if is_directory:
                    directories += 1
                    if directories > max_files:
                        raise ReleaseError("Source exceeds its directory count limit")
                    if directory_names is not None:
                        directory_names.append(relative)
                    pending.append((path, relative + "/"))
                else:
                    total += info.st_size
                    result.append((relative, path, info))
                    if len(result) > max_files or total > max_bytes:
                        raise ReleaseError("Source exceeds its snapshot size or file count limit")
    return sorted(result)


def _read_file(path: Path, before: os.stat_result, destination: Path | None = None) -> str:
    _plain_ancestors(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_nlink) != (
                before.st_dev, before.st_ino, before.st_size, 1):
            raise ReleaseError("Source changed while its snapshot was being read")
        digest = hashlib.sha256()
        count = 0
        output = destination.open("xb") if destination is not None else None
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                while chunk := source.read(1024 * 1024):
                    count += len(chunk)
                    if count > before.st_size:
                        raise ReleaseError("Source grew while its snapshot was being read")
                    digest.update(chunk)
                    if output is not None:
                        output.write(chunk)
            if output is not None:
                output.flush()
                os.fsync(output.fileno())
        finally:
            if output is not None:
                output.close()
        after = _stat_plain(path)
        if count != before.st_size or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise ReleaseError("Source changed while its snapshot was being read")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def tree_manifest(path: Path, *, max_files: int = MAX_FILES, max_bytes: int = MAX_BYTES) -> dict[str, str]:
    """Hash every source file, excluding only Python's generated bytecode caches."""
    return {relative: _read_file(source, info) for relative, source, info in
            _scan(Path(path), max_files=max_files, max_bytes=max_bytes)}


def snapshot_tree(source: Path, destination: Path, *, max_files: int = MAX_FILES,
                  max_bytes: int = MAX_BYTES) -> dict[str, str]:
    """Copy bounded, plain files to a new directory; never follow links or overwrite."""
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if destination == source or source in destination.parents or destination in source.parents:
        raise ReleaseError("Snapshot source and destination must be disjoint")
    directories: list[str] = []
    files = _scan(source, max_files=max_files, max_bytes=max_bytes, directory_names=directories)
    _plain_ancestors(destination)
    destination.mkdir(mode=0o700)
    try:
        manifest: dict[str, str] = {}
        for relative in sorted(directories):
            (destination / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
        for relative, path, info in files:
            target = destination / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            manifest[relative] = _read_file(path, info, target)
        # A second complete scan catches new files and mutations during copying.
        if tree_manifest(source, max_files=max_files, max_bytes=max_bytes) != manifest:
            raise ReleaseError("Source changed during its snapshot")
        _sync_directory(destination)
        return manifest
    except BaseException:
        shutil.rmtree(destination)
        raise


def _checked_manifest(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise ReleaseError("A source manifest must contain files")
    checked: dict[str, str] = {}
    seen: set[str] = set()
    for key, digest in value.items():
        key = _safe_relative(key)
        if not isinstance(digest, str) or not _HASH.fullmatch(digest) or key.casefold() in seen:
            raise ReleaseError("Manifest contains an invalid hash or duplicate path")
        seen.add(key.casefold())
        checked[key] = digest
    return dict(sorted(checked.items()))


def manifest_digest(manifest: Mapping[str, str]) -> str:
    return hashlib.sha256(_canonical(_checked_manifest(manifest))).hexdigest()


def _always_protected(relative: str) -> bool:
    path = PurePosixPath(relative)
    parts = tuple(part.casefold() for part in path.parts)
    name = parts[-1]
    protected_directories = {"cochem_supervisor", "config", "configs", "configuration", ".git",
                             "tests", "test", "pipeline_tests", "mcp_tests", "supervisor_tests"}
    return (bool(set(parts) & protected_directories) or any(part.endswith("_tests") for part in parts)
            or name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
            or name in {"setup.py", "setup.cfg", "pyproject.toml", "tox.ini", "makefile",
                        "dockerfile", "cmakelists.txt", "manifest.in"}
            or name.startswith("requirements") or name.endswith((".lock", ".pth"))
            or path.suffix.casefold() != ".py")


def validate_changes(baseline_manifest: Mapping[str, str], candidate: Path,
                     allowed_paths: Sequence[str]) -> dict[str, object]:
    """Reject model edits outside explicit Python repair paths and immutable guards."""
    baseline = _checked_manifest(baseline_manifest)
    manifest = _checked_manifest(tree_manifest(candidate))
    if isinstance(allowed_paths, (str, bytes)) or not allowed_paths:
        raise ReleaseError("At least one explicit repair path is required")
    allowed = [(_safe_relative(path.rstrip("/")), path.endswith("/")) for path in allowed_paths]
    changed = sorted(key for key in baseline.keys() | manifest.keys()
                     if baseline.get(key) != manifest.get(key))
    if not changed:
        raise ReleaseError("The repair produced no source change")
    for relative in changed:
        if _always_protected(relative) or not any(
            relative == value or (directory and relative.startswith(value + "/"))
            for value, directory in allowed
        ):
            raise ReleaseError(f"Repair changed a protected or unapproved path: {relative}")
    return {"changed": changed, "manifest": manifest, "digest": manifest_digest(manifest)}


def _sync_directory(path: Path) -> None:
    # Windows os.replace is atomic, but Python cannot open a directory for fsync.
    # Each file is flushed before replacement. A power-loss durability guarantee
    # additionally depends on the filesystem and Windows storage write policy.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _atomic_json(path: Path, value: object) -> None:
    _plain_ancestors(path)
    if path.exists():
        _stat_plain(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict:
    info = _stat_plain(path)
    if info.st_size > 1024 * 1024:
        raise ReleaseError("Release metadata exceeds its size limit")
    with path.open("r", encoding="utf-8") as handle:
        result = json.load(handle)
    if not isinstance(result, dict):
        raise ReleaseError("Release metadata must be an object")
    return result


def _release_record(source_root: Path, manifest: Mapping[str, str]) -> dict:
    digest = manifest_digest(manifest)
    return {"schema": 1, "source_root": str(Path(source_root).absolute()),
            "digest": digest, "release_id": digest}


def _check_record(record: object, *, verify: bool = True) -> dict:
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise ReleaseError("Invalid release pointer schema")
    digest, path = record.get("digest"), record.get("source_root")
    if (not isinstance(digest, str) or not _HASH.fullmatch(digest)
            or record.get("release_id") != digest or not isinstance(path, str)
            or not Path(path).is_absolute()):
        raise ReleaseError("Invalid release pointer identity")
    if verify and manifest_digest(tree_manifest(Path(path))) != digest:
        raise ReleaseError("Release contents differ from the verified release hash")
    return {"schema": 1, "source_root": path, "digest": digest, "release_id": digest}


class ReleaseStore:
    """A single-controller release store; the daemon owns a separate process lock.

    All lifecycle callbacks must return exactly True. ``probe`` must perform an
    external health/workflow check, not inspect a model's claimed test results.
    The caller bounds callback execution using its process and health deadlines.
    """

    def __init__(self, root: Path, pointer: Path, journal: Path):
        self.root = Path(root).absolute()
        self.pointer = Path(pointer).absolute()
        self.journal = Path(journal).absolute()
        if self.pointer == self.journal:
            raise ReleaseError("Pointer and journal must be separate files")
        for path in (self.root, self.pointer.parent, self.journal.parent):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            _plain_ancestors(path)
            _stat_plain(path, directory=True)
        self._lock = threading.RLock()

    def current(self) -> dict | None:
        with self._lock:
            if not self.pointer.exists():
                return None
            return _check_record(_read_json(self.pointer))

    def recovery_required(self) -> bool:
        """Read the journal without hashing source or invoking lifecycle callbacks.

        Call before reserving another paid repair attempt. Invalid metadata is
        an error, never evidence that deployment recovery can be skipped. The
        daemon still performs full ``recover`` validation once at startup.
        """
        with self._lock:
            if not self.journal.exists() and not self.journal.is_symlink():
                return False
            try:
                transaction = _read_json(self.journal)
                if (type(transaction.get("schema")) is not int or transaction.get("schema") != 1
                        or transaction.get("state") not in _STATES
                        or not isinstance(transaction.get("transaction_id"), str)
                        or not transaction["transaction_id"]):
                    raise ReleaseError("Invalid release journal; automatic repair is blocked")
                _check_record(transaction.get("previous"), verify=False)
                _check_record(transaction.get("candidate"), verify=False)
            except (OSError, ValueError, TypeError) as error:
                raise ReleaseError("Invalid release journal; automatic repair is blocked") from error
            return transaction["state"] not in _TERMINAL

    def bootstrap(self, source_root: Path, expected_manifest: Mapping[str, str] | None = None) -> dict:
        """Record the installer's existing protected source, never replace a pointer."""
        with self._lock:
            if self.pointer.exists():
                raise ReleaseError("A release pointer already exists")
            manifest = tree_manifest(source_root)
            if expected_manifest is not None and manifest != _checked_manifest(expected_manifest):
                raise ReleaseError("Bootstrap source differs from its expected manifest")
            record = _release_record(source_root, manifest)
            _atomic_json(self.pointer, record)
            return record

    def prepare(self, candidate: Path, manifest: Mapping[str, str],
                protect_callback: Callable[[Path], object] | None = None) -> dict:
        """Snapshot a validated candidate and verify it again after ACL protection."""
        with self._lock:
            manifest = _checked_manifest(manifest)
            if tree_manifest(candidate) != manifest:
                raise ReleaseError("Candidate changed after validation")
            digest = manifest_digest(manifest)
            release = self.root / ("release-" + digest)
            record = _release_record(release, manifest)
            if release.exists():
                _check_record(record)
                if protect_callback is not None and protect_callback(release) is False:
                    raise ReleaseError("Release protection was rejected")
                _check_record(record)
                return record
            staging = self.root / (".preparing-" + uuid.uuid4().hex)
            try:
                if snapshot_tree(candidate, staging) != manifest:
                    raise ReleaseError("Candidate changed while preparing the release")
                if protect_callback is not None and protect_callback(staging) is False:
                    raise ReleaseError("Release protection was rejected")
                if tree_manifest(staging) != manifest:
                    raise ReleaseError("Release changed during protection")
                os.replace(staging, release)
                _sync_directory(self.root)
                return _check_record(record)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def _state(self, transaction: dict, state: str, **updates: object) -> dict:
        if state not in _STATES:
            raise ReleaseError("Invalid release transaction state")
        transaction.update(updates)
        transaction["state"] = state
        _atomic_json(self.journal, transaction)
        return transaction

    @staticmethod
    def _call(callback: Callable, *arguments: object) -> None:
        if callback(*arguments) is not True:
            raise ReleaseError("Release lifecycle callback did not return True")

    def _rollback(self, transaction: dict, start: Callable, stop: Callable, probe: Callable) -> dict:
        self._state(transaction, "ROLLING_BACK")
        try:
            self._call(stop)
            previous = _check_record(transaction.get("previous"))
            _atomic_json(self.pointer, previous)
            self._call(start, Path(previous["source_root"]))
            self._call(probe, Path(previous["source_root"]))
            # The process is untrusted even after a passing probe; check disk again.
            _check_record(previous)
            if self.current() != previous:
                raise ReleaseError("Rollback health check changed the active release pointer")
            return self._state(transaction, "ROLLED_BACK")
        except Exception as error:
            self._state(transaction, "ROLLBACK_FAILED", rollback_error=type(error).__name__)
            raise ReleaseError("Rollback could not restore and verify the previous release") from error

    def deploy(self, release: Mapping[str, object] | Path, start: Callable, stop: Callable,
               probe: Callable) -> dict:
        """Switch only a prepared release; return COMMITTED or successfully ROLLED_BACK."""
        with self._lock:
            if self.journal.exists():
                pending = _read_json(self.journal)
                if pending.get("state") not in _TERMINAL:
                    raise ReleaseError("Recover the previous release transaction before deploying")
            if isinstance(release, Path):
                release = _release_record(release, tree_manifest(release))
            candidate = _check_record(dict(release))
            expected_path = self.root / ("release-" + candidate["digest"])
            if Path(candidate["source_root"]) != expected_path:
                raise ReleaseError("Only a prepared release can be deployed")
            previous = self.current()
            if previous is None:
                raise ReleaseError("Bootstrap a known previous release before deployment")
            if previous["digest"] == candidate["digest"]:
                raise ReleaseError("Candidate is already the active release")
            transaction = {"schema": 1, "transaction_id": uuid.uuid4().hex,
                           "previous": previous, "candidate": candidate}
            self._state(transaction, "PREPARED")
            try:
                self._state(transaction, "ACTIVATING")
                self._call(stop)
                _check_record(candidate)
                _atomic_json(self.pointer, candidate)
                self._state(transaction, "VERIFYING")
                self._call(start, Path(candidate["source_root"]))
                self._call(probe, Path(candidate["source_root"]))
                _check_record(candidate)
                if self.current() != candidate:
                    raise ReleaseError("Activation health check changed the active release pointer")
                return self._state(transaction, "COMMITTED")
            except Exception as error:
                transaction["activation_error"] = type(error).__name__
                return self._rollback(transaction, start, stop, probe)

    def recover(self, start: Callable, stop: Callable, probe: Callable) -> dict | None:
        """After a crash, roll back unfinished transactions before starting any candidate."""
        with self._lock:
            if not self.journal.exists():
                return None
            transaction = _read_json(self.journal)
            if (transaction.get("schema") != 1 or transaction.get("state") not in _STATES
                    or not isinstance(transaction.get("transaction_id"), str)):
                raise ReleaseError("Invalid release journal; automatic activation is blocked")
            _check_record(transaction.get("previous"), verify=False)
            _check_record(transaction.get("candidate"), verify=False)
            if transaction["state"] in _TERMINAL:
                expected = transaction["candidate" if transaction["state"] == "COMMITTED" else "previous"]
                if self.current() != expected:
                    raise ReleaseError("Active release does not match the terminal release journal")
                return transaction
            transaction["recovered_after_interruption"] = True
            return self._rollback(transaction, start, stop, probe)
