"""Actual disposable SQLite on Windows; private SYSTEM boundary is explicit fixture.

No production/private ledger, scheduled task, credential or provider is opened.
"""
import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
sys.path.insert(0, str(REPO / 'src'))
SPEC = importlib.util.spec_from_file_location('pair_bootstrap', Path(__file__).with_name('bootstrap-unresolved-budget-pair.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


@pytest.fixture
def evidence():
    return {'schema': 'cochem-unresolved-budget-evidence/1',
            'reason': 'Disposable fixture: legacy spend is unknown, never a fresh allowance.',
            'sources': [{'kind': 'immutable_snapshot', 'sha256': '1' * 64}]}


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    def ordinary_user_fixture(path, *, directory=False, inherit=False):
        path = Path(path).absolute()
        assert path == tmp_path or tmp_path in path.parents
        M.ordinary(path, directory=directory)
    monkeypatch.setattr(M, 'private', ordinary_user_fixture)
    return tmp_path


def snapshot(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}


def test_real_pair_complete_reopens_without_spend_and_preserves_neighbors(boundary, evidence):
    old = boundary / 'old'; old.mkdir()
    # These opaque neighboring fixture bytes must never be opened/interpreted.
    for name in ('supervisor.db', 'supervisor.db-wal', 'job_board.db', 'secret'):
        (old / name).write_bytes(b'untouched-' + name.encode())
    before = snapshot(old)
    root = boundary / 'fresh'
    result = M.bootstrap_pair(root, evidence, now=100)
    assert result['status'] == M.STATUS and result['historical_allowance'] is None
    assert result['activation_ready'] is result['paid_repair_enabled'] is result['component_recovery_enabled'] is False
    original = snapshot(root)
    assert M.verify_pair(root, evidence) == result
    assert snapshot(root) == original and snapshot(old) == before
    assert all(item['authority']['blocked'] and not any(item['new_rows'].values()) for item in result['ledgers'].values())


@pytest.mark.parametrize('prior', ['empty_directory', 'file', 'partial_pair'])
def test_existing_namespace_never_retried_or_changed(boundary, evidence, prior):
    root = boundary / 'held'
    if prior == 'file': root.write_bytes(b'preserve')
    else:
        root.mkdir()
        if prior == 'partial_pair': (root / M.INTENT).write_bytes(b'preserve')
    before = root.read_bytes() if root.is_file() else snapshot(root)
    with pytest.raises(FileExistsError): M.bootstrap_pair(root, evidence)
    assert (root.read_bytes() if root.is_file() else snapshot(root)) == before


@pytest.mark.parametrize('stage', ['first_hold', 'second_constructor', 'second_hold', 'completion'])
def test_interrupted_pair_never_has_completion_or_automatic_resume(boundary, evidence, monkeypatch, stage):
    authority, state, component = M.dependencies()
    def fail(*args, **kwargs): raise RuntimeError('synthetic interruption')
    if stage == 'first_hold': monkeypatch.setattr(state.Ledger, 'hold_legacy_budget_authority', fail)
    elif stage == 'second_constructor': monkeypatch.setattr(component.RecoveryLedger, '__init__', fail)
    elif stage == 'second_hold': monkeypatch.setattr(component.RecoveryLedger, 'hold_legacy_budget_authority', fail)
    else:
        original = M.exclusive_json
        def reject_completion(path, value):
            if Path(path).name == M.RECEIPT: fail()
            return original(path, value)
        monkeypatch.setattr(M, 'exclusive_json', reject_completion)
    root = boundary / 'partial'
    with pytest.raises(RuntimeError, match='synthetic'): M.bootstrap_pair(root, evidence)
    assert (root / M.INTENT).is_file() and not (root / M.RECEIPT).exists()
    preserved = snapshot(root)
    with pytest.raises(M.BootstrapHeld, match='NAMESPACE'): M.verify_pair(root, evidence)
    with pytest.raises(FileExistsError): M.bootstrap_pair(root, evidence)
    assert snapshot(root) == preserved


@pytest.mark.parametrize('target,sql', [
    ('supervisor.db', "INSERT INTO supervisor_attempts VALUES('x','x','code','{}','RUNNING',1,NULL,2,'1970-01-01',2,4,1800,'{}')"),
    ('supervisor.db', "INSERT INTO supervisor_model_calls VALUES('call','attempt','fp','1970-01-01',1,'review','route',4)"),
    ('component-recovery.db', "INSERT INTO components VALUES('warden',3,1,31,1,31,NULL)"),
    ('supervisor.db', 'DELETE FROM unresolved_budget_authority'),
    ('component-recovery.db', 'UPDATE unresolved_budget_authority SET recorded_at=0'),
    ('component-recovery.db', 'INSERT OR REPLACE INTO unresolved_budget_authority SELECT * FROM unresolved_budget_authority'),
])
def test_actual_sql_guards_refuse_old_unguarded_spend_and_hold_reset(boundary, evidence, target, sql):
    root = boundary / 'fresh'; M.bootstrap_pair(root, evidence, now=100)
    original = snapshot(root)
    with closing(sqlite3.connect(root / target)) as db, db:
        with pytest.raises(sqlite3.IntegrityError): db.execute(sql)
    assert snapshot(root) == original
    M.verify_pair(root, evidence)


@pytest.mark.parametrize('target,trigger', [
    ('supervisor.db', 'unresolved_authority_attempt_insert'),
    ('component-recovery.db', 'unresolved_authority_component_increment'),
    ('component-recovery.db', 'recovery_no_delete'),
])
def test_guard_tampering_cannot_reuse_completion(boundary, evidence, target, trigger):
    root = boundary / 'fresh'; M.bootstrap_pair(root, evidence)
    with closing(sqlite3.connect(root / target)) as db, db: db.execute('DROP TRIGGER ' + trigger)
    with pytest.raises(M.BootstrapHeld, match='GUARD'): M.verify_pair(root, evidence)


def test_used_staging_ledgers_cannot_masquerade_as_fresh_pair(boundary, evidence):
    root = boundary / 'fresh'; M.bootstrap_pair(root, evidence)
    _, state, component = M.dependencies()
    ledger = state.Ledger(root / 'supervisor.db')
    ledger.observe('fixture', 'code', {}, now=200)
    assert ledger.reserve('fixture', now=200) is None
    recovery = component.RecoveryLedger(root / 'component-recovery.db')
    assert recovery.observe('warden', False, now=200)['state'] == 'authority_hold'
    with pytest.raises(M.BootstrapHeld, match='ALREADY_USED'): M.verify_pair(root, evidence)


@pytest.mark.parametrize('name', ['unexpected', 'supervisor.db-wal', 'current.json'])
def test_foreign_or_partial_artifacts_preserved_and_refused(boundary, evidence, name):
    root = boundary / 'fresh'; M.bootstrap_pair(root, evidence)
    (root / name).write_bytes(b'preserve')
    before = snapshot(root)
    with pytest.raises(M.BootstrapHeld, match='NAMESPACE'): M.verify_pair(root, evidence)
    assert snapshot(root) == before


def test_hardlink_state_is_rejected_without_modification(boundary, evidence):
    root = boundary / 'fresh'; M.bootstrap_pair(root, evidence)
    os.link(root / 'supervisor.db', boundary / 'alias')
    before = snapshot(root)
    with pytest.raises(M.BootstrapHeld, match='NONORDINARY'): M.verify_pair(root, evidence)
    assert snapshot(root) == before


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, True])
def test_invalid_timestamp_has_no_filesystem_mutation(boundary, evidence, value):
    with pytest.raises(M.BootstrapHeld, match='INVALID_TIME'): M.bootstrap_pair(boundary / 'new', evidence, now=value)
    assert not list(boundary.iterdir())


def test_private_boundary_failure_precedes_any_ledger(boundary, evidence, monkeypatch):
    monkeypatch.setattr(M, 'private', lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError): M.bootstrap_pair(boundary / 'new', evidence)
    assert not list(boundary.iterdir())


def test_mismatched_evidence_cannot_reinterpret_existing_pair(boundary, evidence):
    root = boundary / 'fresh'; M.bootstrap_pair(root, evidence)
    before = snapshot(root)
    other = {**evidence, 'reason': 'Different provenance'}
    with pytest.raises(M.BootstrapHeld, match='INTENT'): M.verify_pair(root, other)
    assert snapshot(root) == before


def test_completed_receipt_cannot_claim_authority_or_activation(boundary, evidence):
    root = boundary / 'fresh'; M.bootstrap_pair(root, evidence)
    value, _ = M.control(root / M.RECEIPT)
    value['historical_allowance'] = 4; value['activation_ready'] = True
    (root / M.RECEIPT).write_bytes(M.canonical(value) + b'\n')
    with pytest.raises(M.BootstrapHeld, match='COMPLETION'): M.verify_pair(root, evidence)


def test_cli_only_plans_and_has_no_apply_switch(boundary, evidence, capsys):
    path = boundary / 'evidence.json'; path.write_text(json.dumps(evidence), encoding='utf-8')
    target = boundary / 'new'
    assert M.main(['--evidence', str(path), '--new-private-root', str(target)]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['mode'] == 'READ_ONLY_PLAN' and value['protected_launcher_required'] is True
    assert not target.exists()
    with pytest.raises(SystemExit): M.main(['--evidence', str(path), '--new-private-root', str(target), '--apply'])


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows ordinary-token boundary test')
def test_real_windows_private_boundary_rejects_non_system_before_creating(tmp_path, evidence):
    from cochem_pipeline import windows as win
    with pytest.raises(win.WindowsIsolationError): M.bootstrap_pair(tmp_path / 'new', evidence)
    assert not (tmp_path / 'new').exists()
