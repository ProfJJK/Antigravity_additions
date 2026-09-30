"""Contract suite for v4_evidence (AC-36, AC-37, AC-38, AC-47, AC-48).

Covers ``v4_evidence.write_evidence`` (physical evidence bundle under
``<root>/.evidence/<task_id>/`` with a 16 KiB inline stdout cap) and
``v4_evidence.verify_dossier`` (out-of-band verification through the real git
binary: ``git cat-file -e`` and
``git diff-tree --no-commit-id --name-only -r``).

Every repository is a real git repository created under pytest's ``tmp_path``.
Every payload is real text written to disk. Nothing is faked, patched or
skipped. If the git binary is absent the git-backed tests fail loudly (hard
abort) rather than pretending to pass.

Schema shape (v4_schemas.ExecutionChunk): ``task_id`` is a strict float,
``acceptance_criteria`` is ``list[str]`` and ``test_spec`` is a required
``dict`` with the keys ``test_file``, ``physical_inputs`` and ``assertions``.
The evidence directory name is therefore ``str(chunk.task_id)``.
"""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from v4_evidence import verify_dossier, write_evidence
from v4_schemas import ExecutionChunk, ImplementationDossier

INLINE_CAP = 16 * 1024  # 16384 bytes
BUNDLE_FILES = ("stdout.txt", "stdout.sha256", "git_hash.txt", "changed_files.txt", "dossier.json")
GIT_BINARY = shutil.which("git")

AC_COMPLETES = "process completes"
AC_CACHE = "cache is flushed"


# --------------------------------------------------------------------------
# helpers (real git, real files)
# --------------------------------------------------------------------------
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _git(repo: Path, *args: str) -> str:
    assert GIT_BINARY is not None, "HARD ABORT: git binary not found on PATH"
    result = subprocess.run(
        [GIT_BINARY, *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout


def _init_repo(repo: Path) -> Path:
    """Create a real git repository with a base commit (so later commits are
    never root commits, which diff-tree would otherwise report as empty)."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "evidence@example.com")
    _git(repo, "config", "user.name", "Evidence Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base commit")
    return repo


def _commit_file(repo: Path, rel_path: str, content: str, message: str) -> str:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", rel_path)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _changed_paths(repo: Path, commit_hash: str) -> list:
    out = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash)
    return [line.strip() for line in out.splitlines() if line.strip()]


def _chunk(task_id: float, target_files=None, acceptance_criteria=None) -> ExecutionChunk:
    return ExecutionChunk(
        task_id=task_id,
        target_files=list(target_files) if target_files is not None else ["src/target.py"],
        acceptance_criteria=(
            list(acceptance_criteria)
            if acceptance_criteria is not None
            else [AC_COMPLETES]
        ),
        test_spec={
            "test_file": "test_v4_evidence.py",
            "physical_inputs": [
                "real temporary git repository under tmp_path",
                "real stdout text written to .evidence/<task_id>/stdout.txt",
            ],
            "assertions": [
                "write_evidence writes the five-file evidence bundle",
                "verify_dossier verifies git objects, diffs, hashes and ACs out-of-band",
            ],
        },
    )


def _evidence_dir(root: Path, chunk: ExecutionChunk) -> Path:
    return root / ".evidence" / str(chunk.task_id)


def _as_dossier(result) -> ImplementationDossier:
    """Return write_evidence's result as an ImplementationDossier.

    Accepts either the model itself or a plain mapping carrying the same
    fields (validated through the real schema, so malformed output still
    fails loudly).
    """
    if isinstance(result, ImplementationDossier):
        return result
    assert isinstance(result, dict), (
        f"write_evidence must return an ImplementationDossier or its field mapping, got {type(result)!r}"
    )
    return ImplementationDossier.model_validate(result)


def _dossier_for(chunk, stdout_text, git_hash, repo, fulfilled) -> ImplementationDossier:
    dossier = _as_dossier(write_evidence(chunk, stdout_text, git_hash, root=repo))
    return dossier.model_copy(
        update={"fulfilled_ac_list": list(fulfilled), "git_diff_hash": git_hash}
    )


# --------------------------------------------------------------------------
# AC-36
# --------------------------------------------------------------------------
def test_evidence_bundle(tmp_path):
    git_hash = "a" * 40

    # ---- short payload: below the cap, not truncated ----
    short_root = tmp_path / "short"
    short_root.mkdir()
    short_chunk = _chunk(196.31)
    short_stdout = "process completes; " * 50  # ~950 bytes of ASCII, no newlines
    assert 0 < len(short_stdout.encode("utf-8")) < INLINE_CAP

    short_dossier = _as_dossier(write_evidence(short_chunk, short_stdout, git_hash, root=short_root))

    ev_dir = _evidence_dir(short_root, short_chunk)
    for name in BUNDLE_FILES:
        assert (ev_dir / name).is_file(), f"missing evidence file {name}"
    assert (ev_dir / "stdout.txt").is_file()
    assert (ev_dir / "stdout.sha256").is_file()
    assert (ev_dir / "git_hash.txt").is_file()
    assert (ev_dir / "changed_files.txt").is_file()
    assert (ev_dir / "dossier.json").is_file()

    on_disk_bytes = (ev_dir / "stdout.txt").read_bytes()
    assert on_disk_bytes == short_stdout.encode("utf-8")
    on_disk = on_disk_bytes.decode("utf-8")
    full_sha = _sha256_bytes(on_disk_bytes)
    assert (ev_dir / "stdout.sha256").read_text(encoding="utf-8").strip().lower() == full_sha
    assert short_dossier.raw_stdout_sha256 == full_sha
    assert (ev_dir / "git_hash.txt").read_text(encoding="utf-8").strip() == git_hash

    dossier_json = json.loads((ev_dir / "dossier.json").read_text(encoding="utf-8"))
    assert dossier_json["raw_stdout_sha256"] == short_dossier.raw_stdout_sha256
    assert dossier_json["stdout_truncated"] is False

    assert short_dossier.stdout_truncated is False
    assert short_dossier.raw_stdout == short_stdout
    assert on_disk.startswith(short_dossier.raw_stdout)
    # AC-36: when stdout is shorter than the cap, sha256(raw_stdout) == raw_stdout_sha256.
    assert _sha256(short_dossier.raw_stdout) == short_dossier.raw_stdout_sha256

    # ---- exactly at the cap: still not truncated ----
    cap_root = tmp_path / "cap"
    cap_root.mkdir()
    cap_chunk = _chunk(196.32)
    cap_stdout = "c" * INLINE_CAP
    cap_dossier = _as_dossier(write_evidence(cap_chunk, cap_stdout, git_hash, root=cap_root))
    assert cap_dossier.stdout_truncated is False
    assert cap_dossier.raw_stdout == cap_stdout
    assert _sha256(cap_dossier.raw_stdout) == cap_dossier.raw_stdout_sha256

    # ---- one byte over the cap: truncated ----
    over_root = tmp_path / "over"
    over_root.mkdir()
    over_chunk = _chunk(196.33)
    over_stdout = "o" * (INLINE_CAP + 1)
    over_dossier = _as_dossier(write_evidence(over_chunk, over_stdout, git_hash, root=over_root))
    assert over_dossier.stdout_truncated is True
    assert len(over_dossier.raw_stdout.encode("utf-8")) <= INLINE_CAP
    assert over_stdout.startswith(over_dossier.raw_stdout)
    # raw_stdout_sha256 always covers the complete stdout.txt (AC-36 / RSK-07).
    assert over_dossier.raw_stdout_sha256 == _sha256(over_stdout)

    # ---- long payload (~20 KB): truncated inline, full on disk ----
    long_root = tmp_path / "long"
    long_root.mkdir()
    long_chunk = _chunk(196.34)
    long_stdout = "".join(chr(ord("a") + (i % 26)) for i in range(20000))
    assert len(long_stdout.encode("utf-8")) > INLINE_CAP

    long_dossier = _as_dossier(write_evidence(long_chunk, long_stdout, git_hash, root=long_root))
    long_dir = _evidence_dir(long_root, long_chunk)
    for name in BUNDLE_FILES:
        assert (long_dir / name).is_file(), f"missing evidence file {name}"
    long_on_disk_bytes = (long_dir / "stdout.txt").read_bytes()
    long_on_disk = long_on_disk_bytes.decode("utf-8")

    assert long_on_disk == long_stdout  # disk copy is never truncated
    # AC-36: when stdout is longer than the cap, stdout_truncated is True.
    assert long_dossier.stdout_truncated is True
    assert long_dossier.raw_stdout == long_stdout[:INLINE_CAP]
    assert long_on_disk.startswith(long_dossier.raw_stdout)
    assert len(long_dossier.raw_stdout) < len(long_on_disk)

    full_long_sha = _sha256_bytes(long_on_disk_bytes)
    assert full_long_sha == _sha256(long_stdout)
    assert (long_dir / "stdout.sha256").read_text(encoding="utf-8").strip().lower() == full_long_sha
    # The dossier hash is the hash of the COMPLETE stdout (matches stdout.sha256).
    assert long_dossier.raw_stdout_sha256 == full_long_sha

    long_json = json.loads((long_dir / "dossier.json").read_text(encoding="utf-8"))
    assert long_json["stdout_truncated"] is True
    assert long_json["raw_stdout_sha256"] == full_long_sha


# --------------------------------------------------------------------------
# AC-37
# --------------------------------------------------------------------------
def test_evidence_verify_git_hash(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    commit_hash = _commit_file(repo, "src/target.py", "print('hello')\n", "touch target")
    # Independent ground truth: the commit really touches the declared target.
    assert _changed_paths(repo, commit_hash) == ["src/target.py"]

    chunk = _chunk(196.35)
    stdout_text = "run finished: process completes successfully"
    dossier = _dossier_for(chunk, stdout_text, commit_hash, repo, [AC_COMPLETES])

    assert verify_dossier(dossier, chunk, repo=repo) == (True, "OK")

    # Well-formed (same length/format as a real object id) but nonexistent.
    missing_hash = "0" * len(commit_hash)
    probe = subprocess.run(
        [GIT_BINARY, "cat-file", "-e", missing_hash],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert probe.returncode != 0, "fixture error: all-zero object unexpectedly exists"

    missing = dossier.model_copy(update={"git_diff_hash": missing_hash})
    assert verify_dossier(missing, chunk, repo=repo) == (False, "MISSING_OBJECT")


# --------------------------------------------------------------------------
# AC-38
# --------------------------------------------------------------------------
def test_anti_spoof_rejects_decoy_diff(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    decoy_hash = _commit_file(repo, "docs/lessons.md", "# lessons\n- decoy\n", "decoy docs only")
    assert _changed_paths(repo, decoy_hash) == ["docs/lessons.md"]

    chunk = _chunk(196.36, target_files=["src/target.py"])
    assert chunk.target_files == ["src/target.py"]
    stdout_text = "run finished: process completes successfully"

    decoy_dossier = _dossier_for(chunk, stdout_text, decoy_hash, repo, [AC_COMPLETES])
    assert verify_dossier(decoy_dossier, chunk, repo=repo) == (False, "DECOY_DIFF")

    # Control: a genuine commit touching the target passes.
    real_hash = _commit_file(repo, "src/target.py", "print('real')\n", "real change")
    assert _changed_paths(repo, real_hash) == ["src/target.py"]
    ok_dossier = _dossier_for(chunk, stdout_text, real_hash, repo, [AC_COMPLETES])
    assert verify_dossier(ok_dossier, chunk, repo=repo) == (True, "OK")

    # Fabricated AC: appears in stdout, but the chunk never declared it.
    fabricated = "invented criterion satisfied"
    assert fabricated not in chunk.acceptance_criteria
    fabricated_stdout = stdout_text + "\n" + fabricated
    undeclared = _dossier_for(chunk, fabricated_stdout, real_hash, repo, [fabricated])
    assert verify_dossier(undeclared, chunk, repo=repo) == (False, "UNSUPPORTED_AC")


# --------------------------------------------------------------------------
# AC-47
# --------------------------------------------------------------------------
def test_evidence_verify_hash_mismatch(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    commit_hash = _commit_file(repo, "src/target.py", "print('hello')\n", "touch target")

    chunk = _chunk(196.37)
    stdout_text = "run finished: process completes successfully" + " filler" * 20
    dossier = _dossier_for(chunk, stdout_text, commit_hash, repo, [AC_COMPLETES])

    # Control: untampered evidence verifies.
    assert verify_dossier(dossier, chunk, repo=repo) == (True, "OK")

    stdout_path = _evidence_dir(repo, chunk) / "stdout.txt"
    original = stdout_path.read_bytes()
    idx = len(original) - 2  # a byte inside the trailing filler text
    flipped = b"Z" if original[idx:idx + 1] != b"Z" else b"Y"
    tampered = original[:idx] + flipped + original[idx + 1:]
    stdout_path.write_bytes(tampered)

    on_disk = stdout_path.read_bytes()
    assert len(on_disk) == len(original)
    assert sum(1 for a, b in zip(original, on_disk) if a != b) == 1

    assert verify_dossier(dossier, chunk, repo=repo) == (False, "HASH_MISMATCH")


# --------------------------------------------------------------------------
# AC-48
# --------------------------------------------------------------------------
def test_evidence_unsupported_ac_stdout(tmp_path):
    repo = _init_repo(tmp_path / "repo")
    commit_hash = _commit_file(repo, "src/target.py", "print('hello')\n", "touch target")

    chunk = _chunk(196.38, acceptance_criteria=[AC_COMPLETES, AC_CACHE])
    stdout_text = "run finished: process completes with exit code 0"
    # AC_CACHE is declared by the chunk but does not occur in the real stdout.
    assert AC_CACHE in chunk.acceptance_criteria
    assert AC_CACHE not in stdout_text

    unsupported = _dossier_for(chunk, stdout_text, commit_hash, repo, [AC_CACHE])
    captured = (_evidence_dir(repo, chunk) / "stdout.txt").read_bytes().decode("utf-8")
    assert captured == stdout_text
    assert unsupported.fulfilled_ac_list == [AC_CACHE]
    assert verify_dossier(unsupported, chunk, repo=repo) == (False, "UNSUPPORTED_AC")

    # Control: the AC that IS evidenced in stdout verifies.
    supported = _dossier_for(chunk, stdout_text, commit_hash, repo, [AC_COMPLETES])
    assert verify_dossier(supported, chunk, repo=repo) == (True, "OK")
