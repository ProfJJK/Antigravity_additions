"""v4_evidence: out-of-band physical evidence capture and verification.

Public API
----------
write_evidence(chunk, stdout_text, git_hash, root) -> dict
    Writes a tamper-evident bundle under ``<root>/.evidence/<task_id>/``:

        stdout.txt         full captured stdout, UTF-8 bytes, no newline translation
        stdout.sha256      lowercase hex SHA-256 of stdout.txt bytes, one line
        git_hash.txt       the git commit hash the evidence is bound to, one line
        changed_files.txt  output of ``git diff-tree --no-commit-id --name-only -r``
        dossier.json       task_id, raw_stdout, raw_stdout_sha256, stdout_truncated,
                           git_diff_hash, fulfilled_ac_list

    ``raw_stdout`` is the inline copy of stdout, capped at 16,384 UTF-8 bytes.
    It is cut on a character boundary, so it is always an exact prefix of
    stdout.txt. ``raw_stdout_sha256`` is the SHA-256 of ``raw_stdout``.

verify_dossier(dossier, chunk, repo) -> (bool, str)
    Re-derives every claim from the physical repository and evidence files.
    It returns ``(True, 'OK')`` or ``(False, <REASON>)``. The reasons are:

        UNSUPPORTED_AC        claimed AC not declared in chunk.acceptance_criteria,
                              or not physically matched in stdout.txt
        MALFORMED_HASH        git_diff_hash is not a hex object id
        MISSING_OBJECT        ``git cat-file -e <hash>`` fails
        NOT_A_COMMIT          object exists but is not a commit
        GIT_HASH_MISMATCH     dossier / chunk / git_hash.txt disagree on the commit
        DECOY_DIFF            diff-tree of the commit shares no path with target_files
        MISSING_EVIDENCE      a required bundle artifact is absent
        HASH_MISMATCH         stdout.txt, stdout.sha256, raw_stdout or
                              raw_stdout_sha256 are inconsistent
        CAP_VIOLATION         inline raw_stdout exceeds the 16 KiB cap
        TRUNCATION_MISMATCH   stdout_truncated flag contradicts stdout.txt size
        CHANGED_FILES_MISMATCH changed_files.txt differs from the live diff-tree
        TASK_MISMATCH         dossier.task_id != chunk.task_id

Hard-abort policy: the function raises instead of returning a verdict in
these cases. The first is a missing ``git`` binary. The second is a
nonexistent repo path or a repo that is not a git work tree. The third is a
dossier or chunk missing a mandatory field.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Sequence, Tuple, Union

STDOUT_INLINE_CAP_BYTES = 16384
EVIDENCE_DIRNAME = ".evidence"

STDOUT_FILE = "stdout.txt"
STDOUT_SHA_FILE = "stdout.sha256"
GIT_HASH_FILE = "git_hash.txt"
CHANGED_FILES_FILE = "changed_files.txt"
DOSSIER_FILE = "dossier.json"
BUNDLE_FILES = (STDOUT_FILE, STDOUT_SHA_FILE, GIT_HASH_FILE, CHANGED_FILES_FILE, DOSSIER_FILE)

OK = "OK"
UNSUPPORTED_AC = "UNSUPPORTED_AC"
MALFORMED_HASH = "MALFORMED_HASH"
MISSING_OBJECT = "MISSING_OBJECT"
NOT_A_COMMIT = "NOT_A_COMMIT"
GIT_HASH_MISMATCH = "GIT_HASH_MISMATCH"
DECOY_DIFF = "DECOY_DIFF"
MISSING_EVIDENCE = "MISSING_EVIDENCE"
HASH_MISMATCH = "HASH_MISMATCH"
CAP_VIOLATION = "CAP_VIOLATION"
TRUNCATION_MISMATCH = "TRUNCATION_MISMATCH"
CHANGED_FILES_MISMATCH = "CHANGED_FILES_MISMATCH"
TASK_MISMATCH = "TASK_MISMATCH"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_HEX_OBJECT_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_REGEX_PREFIXES = ("regex:", "re:")
_MISSING = object()

PathLike = Union[str, "os.PathLike[str]"]


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
def _field(obj: Any, name: str, default: Any = _MISSING) -> Any:
    """Read ``name`` from a mapping or an attribute-bearing object."""
    if isinstance(obj, Mapping):
        if name in obj:
            return obj[name]
    elif hasattr(obj, name):
        return getattr(obj, name)
    if default is _MISSING:
        raise KeyError(f"{type(obj).__name__} is missing required field {name!r}")
    return default


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_task_id(task_id: str) -> str:
    if not task_id or task_id in (".", "..") or not _TASK_ID_RE.match(task_id):
        raise ValueError(
            f"task_id {task_id!r} is not a safe directory name "
            "(allowed: letters, digits, '.', '_', '-')"
        )
    return task_id


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _declared_criteria(chunk: Any) -> Dict[str, str]:
    """Normalise chunk.acceptance_criteria to an ordered {ac_id: description} map."""
    raw = _field(chunk, "acceptance_criteria")
    declared: Dict[str, str] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            declared[str(key)] = "" if value is None else str(value)
        return declared
    if isinstance(raw, (str, bytes)):
        raise TypeError("chunk.acceptance_criteria must be a mapping or a sequence, not a string")
    for item in raw:
        if isinstance(item, str):
            declared[item] = item
            continue
        ac_id = _field(item, "id", None)
        if ac_id is None:
            ac_id = _field(item, "ac_id")
        desc = _field(item, "description", None)
        if desc is None:
            desc = _field(item, "text", "")
        declared[str(ac_id)] = "" if desc is None else str(desc)
    return declared


# --------------------------------------------------------------------------- #
# Stdout capping and acceptance-criteria matching
# --------------------------------------------------------------------------- #
def _cap_stdout(stdout_text: str) -> Tuple[str, bool]:
    """Return (inline_prefix, truncated), with the prefix capped in UTF-8 bytes.

    The cut happens on a character boundary. The prefix is therefore valid
    UTF-8 and an exact prefix of the full text.
    """
    encoded = stdout_text.encode("utf-8")
    if len(encoded) <= STDOUT_INLINE_CAP_BYTES:
        return stdout_text, False
    prefix = encoded[:STDOUT_INLINE_CAP_BYTES].decode("utf-8", errors="ignore")
    return prefix, True


def _bounded_search(needle: str, text: str) -> bool:
    """Case-sensitive literal search that refuses matches embedded in a longer word."""
    if not needle:
        return False
    lead = r"(?<![\w-])" if (needle[0].isalnum() or needle[0] == "_") else ""
    trail = r"(?![\w-])" if (needle[-1].isalnum() or needle[-1] == "_") else ""
    return re.search(lead + re.escape(needle) + trail, text) is not None


def _ac_matched(ac_id: str, description: str, text: str) -> bool:
    """Check whether an AC is physically evidenced in ``text``.

    The AC counts as matched if any of these holds:
      * the description has a ``regex:`` / ``re:`` prefix and its pattern matches;
      * the description occurs as a bounded literal substring;
      * the AC identifier occurs as a bounded token (``AC-1`` does not match ``AC-10``).
    """
    desc = (description or "").strip()
    if desc:
        lowered = desc.lower()
        for prefix in _REGEX_PREFIXES:
            if lowered.startswith(prefix):
                pattern = desc[len(prefix):].strip()
                try:
                    if pattern and re.search(pattern, text, re.MULTILINE):
                        return True
                except re.error:
                    pass
                break
        else:
            if _bounded_search(desc, text):
                return True
    return _bounded_search(str(ac_id).strip(), text)


# --------------------------------------------------------------------------- #
# Git plumbing
# --------------------------------------------------------------------------- #
def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "git executable not found on PATH; physical evidence cannot be produced or verified"
        ) from exc


def _git_toplevel(repo: Path) -> Path:
    if not repo.is_dir():
        raise FileNotFoundError(f"repository path does not exist or is not a directory: {repo}")
    result = _run_git(repo, "rev-parse", "--show-toplevel")
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"{repo} is not inside a git work tree: {result.stderr.strip()}")
    return Path(result.stdout.strip())


def _check_commit(repo: Path, git_hash: str) -> Tuple[str, str]:
    """Return (reason, full_sha). reason == OK when git_hash names a real commit."""
    if not _HEX_OBJECT_RE.match(git_hash):
        return MALFORMED_HASH, ""
    exists = _run_git(repo, "cat-file", "-e", git_hash)
    if exists.returncode != 0:
        return MISSING_OBJECT, ""
    obj_type = _run_git(repo, "cat-file", "-t", git_hash)
    if obj_type.returncode != 0:
        return MISSING_OBJECT, ""
    if obj_type.stdout.strip() != "commit":
        return NOT_A_COMMIT, ""
    full = _run_git(repo, "rev-parse", "--verify", "--quiet", git_hash + "^{commit}")
    if full.returncode != 0 or not full.stdout.strip():
        return MISSING_OBJECT, ""
    return OK, full.stdout.strip().lower()


def _diff_tree_paths(repo: Path, git_hash: str) -> List[str]:
    """Run ``git diff-tree --no-commit-id --name-only -r`` and return repo-relative paths.

    The ``-z`` flag gives unquoted, NUL-delimited output. The ``--root`` flag
    makes the initial commit report its files instead of an empty list.
    """
    result = _run_git(
        repo,
        "-c",
        "core.quotepath=off",
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "-r",
        "--root",
        "-z",
        git_hash,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git diff-tree failed for {git_hash} in {repo}: {result.stderr.strip()}"
        )
    return [p for p in result.stdout.split("\0") if p.strip()]


def _normalize_repo_path(path: str, toplevel: Path) -> str:
    text = str(path).strip().replace("\\", "/")
    if not text:
        return ""
    candidate = Path(text)
    if candidate.is_absolute():
        try:
            text = candidate.resolve().relative_to(toplevel.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()
    parts = [part for part in PurePosixPath(text).parts if part not in ("", ".")]
    return "/".join(parts)


def _intersects_targets(changed: Sequence[str], targets: Sequence[str], toplevel: Path) -> bool:
    norm_changed = {_normalize_repo_path(p, toplevel) for p in changed}
    norm_changed.discard("")
    norm_targets = {_normalize_repo_path(t, toplevel) for t in targets}
    norm_targets.discard("")
    for target in norm_targets:
        for path in norm_changed:
            if path == target or path.startswith(target + "/"):
                return True
    return False


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def evidence_dir(root: PathLike, task_id: str) -> Path:
    return Path(root) / EVIDENCE_DIRNAME / _validate_task_id(str(task_id))


def write_evidence(chunk: Any, stdout_text: str, git_hash: str, root: PathLike) -> Dict[str, Any]:
    """Capture a tamper-evident evidence bundle and return the dossier dict."""
    if not isinstance(stdout_text, str):
        raise TypeError("stdout_text must be str (captured, decoded process stdout)")
    task_id = _validate_task_id(str(_field(chunk, "task_id")))
    git_hash = str(git_hash).strip()
    root_path = Path(root)
    toplevel = _git_toplevel(root_path)

    reason, _full = _check_commit(root_path, git_hash)
    if reason != OK:
        raise RuntimeError(f"cannot bind evidence to git object {git_hash!r}: {reason}")
    changed = _diff_tree_paths(root_path, git_hash)

    stdout_bytes = stdout_text.encode("utf-8")
    raw_stdout, truncated = _cap_stdout(stdout_text)
    raw_sha = _sha256_hex(raw_stdout.encode("utf-8"))

    declared = _declared_criteria(chunk)
    fulfilled = [ac for ac, desc in declared.items() if _ac_matched(ac, desc, stdout_text)]

    dossier: Dict[str, Any] = {
        "task_id": task_id,
        "raw_stdout": raw_stdout,
        "raw_stdout_sha256": raw_sha,
        "stdout_truncated": truncated,
        "git_diff_hash": git_hash,
        "fulfilled_ac_list": fulfilled,
    }

    out_dir = root_path / EVIDENCE_DIRNAME / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(out_dir / STDOUT_FILE, stdout_bytes)
    _atomic_write_bytes(out_dir / STDOUT_SHA_FILE, (_sha256_hex(stdout_bytes) + "\n").encode("utf-8"))
    _atomic_write_bytes(out_dir / GIT_HASH_FILE, (git_hash + "\n").encode("utf-8"))
    changed_text = "".join(_normalize_repo_path(p, toplevel) + "\n" for p in changed)
    _atomic_write_bytes(out_dir / CHANGED_FILES_FILE, changed_text.encode("utf-8"))
    _atomic_write_bytes(
        out_dir / DOSSIER_FILE,
        json.dumps(dossier, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    return dossier


def verify_dossier(dossier: Any, chunk: Any, repo: PathLike) -> Tuple[bool, str]:
    """Out-of-band verification of a dossier against the physical repo and bundle."""
    repo_path = Path(repo)
    toplevel = _git_toplevel(repo_path)

    task_id = _validate_task_id(str(_field(dossier, "task_id")))
    chunk_task_id = _field(chunk, "task_id", None)
    if chunk_task_id is not None and str(chunk_task_id) != task_id:
        return False, TASK_MISMATCH

    # 1. Every claimed AC must be declared by the chunk.
    declared = _declared_criteria(chunk)
    fulfilled = _as_str_list(_field(dossier, "fulfilled_ac_list"))
    for ac in fulfilled:
        if ac not in declared:
            return False, UNSUPPORTED_AC

    # 2. The commit must physically exist (git cat-file -e).
    git_hash = str(_field(dossier, "git_diff_hash")).strip()
    reason, full_sha = _check_commit(repo_path, git_hash)
    if reason != OK:
        return False, reason
    chunk_hash = str(_field(chunk, "git_diff_hash", "") or "").strip()
    if chunk_hash:
        chunk_reason, chunk_full = _check_commit(repo_path, chunk_hash)
        if chunk_reason != OK or chunk_full != full_sha:
            return False, GIT_HASH_MISMATCH

    # 3. The commit must touch at least one declared target (git diff-tree).
    changed = _diff_tree_paths(repo_path, git_hash)
    targets = _as_str_list(_field(chunk, "target_files"))
    if not _intersects_targets(changed, targets, toplevel):
        return False, DECOY_DIFF

    # 4. Evidence artifacts must be present.
    ev_dir = repo_path / EVIDENCE_DIRNAME / task_id
    for name in (STDOUT_FILE, STDOUT_SHA_FILE, GIT_HASH_FILE, CHANGED_FILES_FILE):
        if not (ev_dir / name).is_file():
            return False, MISSING_EVIDENCE

    # 5. Independently recompute digests from the bytes on disk.
    stdout_bytes = (ev_dir / STDOUT_FILE).read_bytes()
    observed = _sha256_hex(stdout_bytes)
    with open(ev_dir / STDOUT_SHA_FILE, "r", encoding="utf-8") as fh:
        recorded = fh.read().strip().lower()
    if recorded != observed:
        return False, HASH_MISMATCH

    raw_stdout = _field(dossier, "raw_stdout")
    raw_sha = str(_field(dossier, "raw_stdout_sha256")).strip().lower()
    truncated = _field(dossier, "stdout_truncated")
    if not isinstance(raw_stdout, str):
        return False, HASH_MISMATCH
    raw_bytes = raw_stdout.encode("utf-8")
    if _sha256_hex(raw_bytes) != raw_sha:
        return False, HASH_MISMATCH
    if len(raw_bytes) > STDOUT_INLINE_CAP_BYTES:
        return False, CAP_VIOLATION
    # raw_stdout must be an exact byte prefix of stdout.txt.
    if _sha256_hex(stdout_bytes[: len(raw_bytes)]) != raw_sha:
        return False, HASH_MISMATCH
    if truncated is True:
        if len(stdout_bytes) <= STDOUT_INLINE_CAP_BYTES:
            return False, TRUNCATION_MISMATCH
    elif truncated is False:
        if len(stdout_bytes) != len(raw_bytes):
            return False, TRUNCATION_MISMATCH
    else:
        return False, TRUNCATION_MISMATCH

    # 6. Bundle bookkeeping must agree with the live repository.
    with open(ev_dir / GIT_HASH_FILE, "r", encoding="utf-8") as fh:
        recorded_hash = fh.read().strip()
    rec_reason, rec_full = _check_commit(repo_path, recorded_hash)
    if rec_reason != OK or rec_full != full_sha:
        return False, GIT_HASH_MISMATCH
    with open(ev_dir / CHANGED_FILES_FILE, "r", encoding="utf-8") as fh:
        recorded_changed = [line.strip() for line in fh.read().splitlines() if line.strip()]
    live_changed = [_normalize_repo_path(p, toplevel) for p in changed]
    if [_normalize_repo_path(p, toplevel) for p in recorded_changed] != live_changed:
        return False, CHANGED_FILES_MISMATCH

    # 7. Every claimed AC must be physically evidenced in the verified stdout.txt.
    stdout_text = stdout_bytes.decode("utf-8", errors="replace")
    for ac in fulfilled:
        if not _ac_matched(ac, declared[ac], stdout_text):
            return False, UNSUPPORTED_AC

    return True, OK
