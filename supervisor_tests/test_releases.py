"""Release tests use physical files and real process death, never model verdicts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cochem_supervisor.releases import (
    ReleaseError, ReleaseStore, manifest_digest, snapshot_tree, tree_manifest, validate_changes,
)


ALLOWED = ["src/cochem_pipeline/", "src/cochem_mcp/", "src/cochem/warden/ladder.py"]


def source_tree(path: Path, value: str = "old") -> Path:
    path.mkdir()
    for name, text in {
        "src/cochem_pipeline/runtime.py": f"VERSION = {value!r}\n",
        "src/cochem_mcp/server.py": "SERVER = True\n",
        "src/cochem_supervisor/engine.py": "GUARD = True\n",
        "pipeline_tests/test_guard.py": "def test_guard(): assert True\n",
        "pyproject.toml": '[project]\nname = "release-contract"\n',
    }.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return path


def repair_tree(tmp_path):
    baseline = source_tree(tmp_path / "baseline")
    candidate = tmp_path / "candidate"
    manifest = snapshot_tree(baseline, candidate)
    (candidate / "src/cochem_pipeline/runtime.py").write_text("VERSION = 'new'\n", encoding="utf-8")
    return baseline, candidate, manifest


def release_store(tmp_path):
    baseline, candidate, manifest = repair_tree(tmp_path)
    store = ReleaseStore(tmp_path / "releases", tmp_path / "current.json", tmp_path / "journal.json")
    original = store.bootstrap(baseline, manifest)
    checked = validate_changes(manifest, candidate, ALLOWED)
    release = store.prepare(candidate, checked["manifest"])
    return store, original, release


def test_snapshot_preserves_source_and_empty_directories_and_excludes_only_bytecode(tmp_path):
    source = source_tree(tmp_path / "source")
    (source / "empty").mkdir()
    (source / "hidden").mkdir()
    (source / "hidden/.important").write_text("preserve")
    (source / "__pycache__").mkdir()
    (source / "__pycache__/module.cpython.pyc").write_bytes(b"cache")
    (source / "src/old.pyc").write_bytes(b"cache")
    destination = tmp_path / "copied"
    manifest = snapshot_tree(source, destination)
    assert manifest == tree_manifest(source) == tree_manifest(destination)
    assert (destination / "empty").is_dir()
    assert (destination / "hidden/.important").read_text() == "preserve"
    assert not (destination / "__pycache__").exists()
    assert not (destination / "src/old.pyc").exists()
    assert manifest["hidden/.important"] == hashlib.sha256(b"preserve").hexdigest()


@pytest.mark.parametrize("kind", ["symlink_file", "symlink_directory", "hardlink"])
def test_snapshots_reject_links(tmp_path, kind):
    source = source_tree(tmp_path / "source")
    outside = tmp_path / "outside"
    outside.write_text("must not copy")
    target = source / "link"
    try:
        if kind == "hardlink":
            os.link(outside, target)
        elif kind == "symlink_directory":
            target.symlink_to(tmp_path, target_is_directory=True)
        else:
            target.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"OS cannot create test link: {error}")
    with pytest.raises(ReleaseError, match="link|reparse"):
        snapshot_tree(source, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()


def test_snapshot_rejects_root_or_ancestor_symlink(tmp_path):
    source = source_tree(tmp_path / "source")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(source, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"OS cannot create test symlink: {error}")
    for root in (alias, alias / "src"):
        with pytest.raises(ReleaseError, match="Links"):
            tree_manifest(root)


@pytest.mark.parametrize("name", ["bad:stream", "bad\\escape.py", "trailing.", "trailing ", "CON", "nul.txt", "question?", "a\nb"])
def test_source_rejects_names_that_alias_or_escape_on_windows(tmp_path, name):
    if os.name == "nt":
        pytest.skip("These names cannot be created through normal Windows file APIs")
    source = tmp_path / "source"
    source.mkdir()
    (source / name).write_text("value")
    with pytest.raises(ReleaseError):
        tree_manifest(source)


def test_snapshot_rejects_case_collisions(tmp_path):
    if os.name == "nt":
        pytest.skip("Normal Windows directories are case insensitive")
    source = tmp_path / "source"
    source.mkdir()
    (source / "A.py").write_text("one")
    (source / "a.py").write_text("two")
    with pytest.raises(ReleaseError, match="collisions"):
        tree_manifest(source)


@pytest.mark.parametrize("bounds", [{"max_files": 1}, {"max_bytes": 1}])
def test_snapshot_is_bounded_and_does_not_leave_partial_copy(tmp_path, bounds):
    source = source_tree(tmp_path / "source")
    with pytest.raises(ReleaseError, match="limit"):
        snapshot_tree(source, tmp_path / "destination", **bounds)
    assert not (tmp_path / "destination").exists()


def test_snapshot_refuses_overwrite_and_overlapping_roots(tmp_path):
    source = source_tree(tmp_path / "source")
    with pytest.raises(ReleaseError, match="disjoint"):
        snapshot_tree(source, source / "nested")
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "keep").write_text("keep")
    with pytest.raises(FileExistsError):
        snapshot_tree(source, destination)
    assert (destination / "keep").read_text() == "keep"


def test_manifest_is_independent_of_timestamps_and_creation_order(tmp_path):
    first = source_tree(tmp_path / "first")
    second = tmp_path / "second"
    snapshot_tree(first, second)
    for path in second.rglob("*.py"):
        os.utime(path, (1, 1))
    assert tree_manifest(first) == tree_manifest(second)
    assert manifest_digest(tree_manifest(first)) == manifest_digest(tree_manifest(second))


def test_validate_reports_actual_changed_paths_and_content_digest(tmp_path):
    _, candidate, baseline = repair_tree(tmp_path)
    result = validate_changes(baseline, candidate, ALLOWED)
    assert result["changed"] == ["src/cochem_pipeline/runtime.py"]
    assert result["manifest"] == tree_manifest(candidate)
    assert result["digest"] == manifest_digest(result["manifest"])


@pytest.mark.parametrize("relative", [
    "src/cochem_supervisor/engine.py", "pipeline_tests/test_guard.py", "pyproject.toml",
    "src/cochem_pipeline/test_verdict.py", "src/cochem_pipeline/conftest.py",
    "src/cochem_pipeline/config/override.py", "src/cochem_pipeline/config.json",
    "src/cochem_pipeline/requirements.py", "src/cochem_pipeline/setup.py",
    "src/cochem_pipeline/inject.pth", "src/other/runtime.py",
])
def test_protected_paths_cannot_be_changed_even_with_broad_allowlist(tmp_path, relative):
    _, candidate, baseline = repair_tree(tmp_path)
    target = candidate / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("UNREVIEWED = True\n")
    allowed = ["src/", "pipeline_tests/", "pyproject.toml"] if relative != "src/other/runtime.py" else ALLOWED
    with pytest.raises(ReleaseError, match="protected or unapproved"):
        validate_changes(baseline, candidate, allowed)


def test_empty_changes_and_empty_candidate_fail(tmp_path):
    source = source_tree(tmp_path / "source")
    manifest = tree_manifest(source)
    with pytest.raises(ReleaseError, match="no source change"):
        validate_changes(manifest, source, ALLOWED)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ReleaseError, match="must contain files"):
        validate_changes(manifest, empty, ALLOWED)


def test_deletion_is_checked_against_protected_paths(tmp_path):
    _, candidate, baseline = repair_tree(tmp_path)
    (candidate / "pipeline_tests/test_guard.py").unlink()
    with pytest.raises(ReleaseError, match="protected"):
        validate_changes(baseline, candidate, ["pipeline_tests/", *ALLOWED])


@pytest.mark.parametrize("name", [".", "../escape", "/absolute", "src//double", "src\\windows", "C:drive"])
def test_untrusted_manifest_names_fail(tmp_path, name):
    source = source_tree(tmp_path / "source")
    with pytest.raises(ReleaseError):
        validate_changes({name: "a" * 64}, source, ALLOWED)


def test_prepare_verifies_manifest_before_and_after_protection(tmp_path):
    _, candidate, baseline = repair_tree(tmp_path)
    store = ReleaseStore(tmp_path / "releases", tmp_path / "current.json", tmp_path / "journal.json")
    with pytest.raises(ReleaseError, match="changed after validation"):
        store.prepare(candidate, baseline)
    expected = tree_manifest(candidate)

    def mutate_during_protection(path):
        (path / "src/cochem_pipeline/runtime.py").write_text("TAMPERED = True\n")

    with pytest.raises(ReleaseError, match="changed during protection"):
        store.prepare(candidate, expected, mutate_during_protection)
    assert not list(store.root.iterdir())


def test_prepare_is_content_addressed_and_detects_existing_tamper(tmp_path):
    store, _, release = release_store(tmp_path)
    assert Path(release["source_root"]).name == "release-" + release["digest"]
    candidate = tmp_path / "candidate"
    assert store.prepare(candidate, tree_manifest(candidate)) == release
    (Path(release["source_root"]) / "src/cochem_pipeline/runtime.py").write_text("tamper")
    with pytest.raises(ReleaseError, match="differ"):
        store.prepare(candidate, tree_manifest(candidate))


def test_bootstrap_requires_reviewed_source_and_never_overwrites(tmp_path):
    store, original, _ = release_store(tmp_path)
    with pytest.raises(ReleaseError, match="already exists"):
        store.bootstrap(tmp_path / "candidate")
    assert store.current() == original


def test_commit_runs_external_callbacks_in_order_and_persists_journal(tmp_path):
    store, original, release = release_store(tmp_path)
    calls = []

    def stop():
        journal = json.loads(store.journal.read_text())
        assert journal["state"] == "ACTIVATING"
        assert store.current() == original
        calls.append("stop")
        return True

    def start(path):
        assert store.current() == release
        assert json.loads(store.journal.read_text())["state"] == "VERIFYING"
        calls.append(("start", path))
        return True

    def probe(path):
        calls.append(("probe", path))
        return (path / "src/cochem_pipeline/runtime.py").read_text() == "VERSION = 'new'\n"

    result = store.deploy(release, start, stop, probe)
    assert result["state"] == "COMMITTED"
    assert json.loads(store.journal.read_text()) == result
    assert calls == ["stop", ("start", Path(release["source_root"])), ("probe", Path(release["source_root"]))]
    assert store.current() == release
    assert store.recover(start, stop, probe) == result
    assert len(calls) == 3


@pytest.mark.parametrize("failure", ["start_false", "probe_false", "start_exception", "probe_untrusted_string"])
def test_failed_activation_restores_old_pointer_and_verifies_old_release(tmp_path, failure):
    store, original, release = release_store(tmp_path)
    started = []
    probed = []

    def start(path):
        started.append(path)
        if path == Path(release["source_root"]):
            if failure == "start_exception":
                raise OSError("real lifecycle callback failure")
            if failure == "start_false":
                return False
        return True

    def probe(path):
        probed.append(path)
        if path == Path(release["source_root"]):
            return "VERDICT: PERFECT" if failure == "probe_untrusted_string" else False
        return True

    result = store.deploy(release, start, lambda: True, probe)
    assert result["state"] == "ROLLED_BACK"
    assert store.current() == original
    assert started[-1] == Path(original["source_root"])
    assert probed[-1] == Path(original["source_root"])


def test_failed_rollback_is_durable_and_blocks_next_deployment(tmp_path):
    store, _, release = release_store(tmp_path)
    with pytest.raises(ReleaseError, match="Rollback"):
        store.deploy(release, lambda path: False, lambda: True, lambda path: True)
    assert json.loads(store.journal.read_text())["state"] == "ROLLBACK_FAILED"
    with pytest.raises(ReleaseError, match="Recover"):
        store.deploy(release, lambda path: True, lambda: True, lambda path: True)


def test_candidate_modified_after_prepare_never_starts(tmp_path):
    store, original, release = release_store(tmp_path)
    (Path(release["source_root"]) / "src/cochem_pipeline/runtime.py").write_text("changed")
    calls = []
    with pytest.raises(ReleaseError, match="differ"):
        store.deploy(release, lambda path: calls.append(path), lambda: True, lambda path: True)
    assert calls == []
    assert store.current() == original


def test_post_probe_source_mutation_triggers_rollback(tmp_path):
    store, original, release = release_store(tmp_path)

    def probe(path):
        if path == Path(release["source_root"]):
            (path / "src/cochem_pipeline/runtime.py").write_text("tampered after passing test")
        return True

    result = store.deploy(release, lambda path: True, lambda: True, probe)
    assert result["state"] == "ROLLED_BACK"
    assert store.current() == original


def test_post_probe_pointer_mutation_triggers_rollback(tmp_path):
    store, original, release = release_store(tmp_path)

    def probe(path):
        if path == Path(release["source_root"]):
            store.pointer.write_text(json.dumps(original))
        return True

    result = store.deploy(release, lambda path: True, lambda: True, probe)
    assert result["state"] == "ROLLED_BACK"
    assert store.current() == original


@pytest.mark.parametrize("boundary", ["stop", "start", "probe"])
def test_real_process_death_rolls_back_before_any_candidate_restart(tmp_path, boundary):
    store, original, release = release_store(tmp_path)
    child = r'''
import json, os, sys
from pathlib import Path
from cochem_supervisor.releases import ReleaseStore
root, pointer, journal, candidate, boundary = sys.argv[1:]
store = ReleaseStore(Path(root), Path(pointer), Path(journal))
def stop():
    if boundary == "stop": os._exit(73)
    return True
def start(path):
    if boundary == "start": os._exit(73)
    return True
def probe(path):
    if boundary == "probe": os._exit(73)
    return True
store.deploy(json.loads(candidate), start, stop, probe)
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    process = subprocess.run([sys.executable, "-c", child, str(store.root), str(store.pointer),
                              str(store.journal), json.dumps(release), boundary], env=env,
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 73, process.stderr
    assert json.loads(store.journal.read_text())["state"] in {"ACTIVATING", "VERIFYING"}
    restarted = []

    def restart(path):
        restarted.append(path)
        return True

    recovered = ReleaseStore(store.root, store.pointer, store.journal).recover(restart, lambda: True, lambda path: True)
    assert recovered["state"] == "ROLLED_BACK"
    assert recovered["recovered_after_interruption"] is True
    assert restarted == [Path(original["source_root"])]
    assert store.current() == original


@pytest.mark.parametrize("state", ["PREPARED", "ACTIVATING", "VERIFYING", "ROLLING_BACK", "ROLLBACK_FAILED"])
def test_every_uncommitted_state_is_recovered_to_previous_release(tmp_path, state):
    store, original, release = release_store(tmp_path)
    store.pointer.write_text(json.dumps(release))
    store.journal.write_text(json.dumps({"schema": 1, "transaction_id": "interrupted-transaction",
                                         "state": state, "previous": original, "candidate": release}))
    started = []

    def start(path):
        started.append(path)
        return True

    assert store.recover(start, lambda: True, lambda path: True)["state"] == "ROLLED_BACK"
    assert started == [Path(original["source_root"])]
    assert store.current() == original


def test_recovery_does_not_require_candidate_integrity_to_restore_previous(tmp_path):
    store, original, release = release_store(tmp_path)
    store.pointer.write_text(json.dumps(release))
    store.journal.write_text(json.dumps({"schema": 1, "transaction_id": "interrupted-transaction",
                                         "state": "VERIFYING", "previous": original, "candidate": release}))
    (Path(release["source_root"]) / "src/cochem_pipeline/runtime.py").unlink()
    assert store.recover(lambda path: True, lambda: True, lambda path: True)["state"] == "ROLLED_BACK"
    assert store.current() == original


def test_corrupt_journal_blocks_automatic_activation(tmp_path):
    store, original, _ = release_store(tmp_path)
    store.journal.write_text('{"schema":1,"state":"MODEL_SAID_PASSED"}')
    with pytest.raises(ReleaseError, match="Invalid release journal"):
        store.recover(lambda path: True, lambda: True, lambda path: True)
    assert store.current() == original


def test_arbitrary_unprepared_directory_cannot_be_deployed(tmp_path):
    store, _, _ = release_store(tmp_path)
    with pytest.raises(ReleaseError, match="Only a prepared"):
        store.deploy(tmp_path / "candidate", lambda path: True, lambda: True, lambda path: True)


@pytest.mark.parametrize("state,required", [
    ("PREPARED", True), ("ACTIVATING", True), ("VERIFYING", True),
    ("ROLLING_BACK", True), ("ROLLBACK_FAILED", True),
    ("COMMITTED", False), ("ROLLED_BACK", False),
])
def test_recovery_required_checks_journal_without_reading_candidate_source(tmp_path, state, required):
    store, original, release = release_store(tmp_path)
    assert store.recovery_required() is False
    store.journal.write_text(json.dumps({"schema": 1, "transaction_id": "physical-journal",
                                         "state": state, "previous": original, "candidate": release}))
    # The lightweight detection must work even when an interrupted candidate is
    # damaged. The actual recover() operation separately verifies previous code.
    (Path(release["source_root"]) / "src/cochem_pipeline/runtime.py").unlink()
    assert store.recovery_required() is required


@pytest.mark.parametrize("document", [
    "{", "[]", '{"schema":1,"state":"COMMITTED"}',
    '{"schema":1,"state":"MODEL_APPROVED","transaction_id":"x"}',
])
def test_recovery_required_rejects_corrupt_metadata_instead_of_allowing_paid_repair(tmp_path, document):
    store, _, _ = release_store(tmp_path)
    store.journal.write_text(document)
    with pytest.raises(ReleaseError):
        store.recovery_required()


def test_recovery_required_does_not_treat_dangling_link_as_absent_journal(tmp_path):
    store, _, _ = release_store(tmp_path)
    try:
        store.journal.symlink_to(tmp_path / "missing-journal.json")
    except OSError as error:
        pytest.skip(f"OS cannot create test symlink: {error}")
    with pytest.raises(ReleaseError):
        store.recovery_required()
