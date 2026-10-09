"""Ordinary Windows inert ACL/journal fixtures; no controller or model calls.

Native directory ACL creation is limited to new pytest workspace directories.
Only ancestors outside that fixture boundary have synthetic trusted ACLs.
"""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).parent
SOURCE = HERE / 'run-live-commissioning-r3-v2.py'
ORIGINAL = HERE / 'run-live-commissioning-r3.py'
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
EXTRA_SID = 'S-1-5-21-4108184938-3023548017-2507294638-1003'


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = load(SOURCE)
base = load(HERE / 'test_live_commissioning_r3.py')


@pytest.fixture(autouse=True)
def use_v2_in_reused_inert_flows(monkeypatch):
    monkeypatch.setattr(base, 'm', m)


def set_fixture_acl(path, sid, mask, flags='OICI', additional=''):
    assert 'pytest' in str(path).lower() or 'live-v2-fixtures' in str(path).lower()
    sddl = (f'O:{sid}G:{sid}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)'
            f'(A;OICI;FA;;;{sid})(A;{flags};0x{mask:x};;;{EXTRA_SID})' + additional)
    escaped = str(path).replace("'", "''")
    script = ("Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1');"
              "$acl=[Security.AccessControl.DirectorySecurity]::new();"
              f"$acl.SetSecurityDescriptorSddlForm('{sddl}',[Security.AccessControl.AccessControlSections]14);"
              f"[IO.Directory]::SetAccessControl('{escaped}',$acl)")
    result = subprocess.run([PS, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def native(tmp_path):
    sys.path.insert(0, str(m.PACKAGES))
    try:
        from cochem_pipeline import windows as win
    finally:
        sys.path.pop(0)
    sid = win._sid_text(win._account_sid(r'AETHERDESK\ansac'))
    parent = tmp_path / 'ordinary-parent'
    parent.mkdir()

    class ScopedWindows:
        def __getattr__(self, name):
            return getattr(win, name)

        def _acl(self, path):
            path = Path(path)
            if path == parent or path.is_relative_to(parent):
                return win._acl(path)
            # Codex/test workspace ancestors have legitimate extra trustees.
            # Substitute only their metadata. Target parent and child ACLs,
            # native directory creation and subsequent journal ACLs stay real.
            return sid, True, [(sid, win.FULL_CONTROL, 0),
                               (win.SYSTEM_SID, win.FULL_CONTROL, 0),
                               (win.ADMIN_SID, win.FULL_CONTROL, 0)]

    scoped = ScopedWindows()
    return parent, sid, win, scoped, m.WindowsPrivate(scoped, sid)


def test_real_read_only_parent_grant_allowed_and_never_inherited_by_private_child(native):
    parent, sid, win, scoped, private = native
    set_fixture_acl(parent, sid, 0x1200A9)
    original_parent = win._acl(parent)
    # Original driver's stricter parent check would fail on this exact shape.
    with pytest.raises(m.Held, match='private_extra_trustee'):
        private.validate(parent)
    child = parent / 'new-evidence'
    private.create_directory(child)
    owner, protected, rules = win._acl(child)
    assert owner == sid and protected
    assert set(rules) == {(sid, win.FULL_CONTROL, 3),
                          (win.SYSTEM_SID, win.FULL_CONTROL, 3),
                          (win.ADMIN_SID, win.FULL_CONTROL, 3)}
    assert EXTRA_SID not in {trustee for trustee, _, _ in rules}
    journal = m.Journal(child, private)
    with journal.locked():
        journal.write('intent.json', {'fixture': True, 'no_model_jobs': True})
    assert journal.read('intent.json') == {'fixture': True, 'no_model_jobs': True}
    assert EXTRA_SID not in {trustee for trustee, _, _ in win._acl(child / 'intent.json')[2]}
    assert win._acl(parent) == original_parent
    before = win._acl(child), (child / 'intent.json').read_bytes()
    with pytest.raises(m.Held, match='private_root_already_exists'):
        private.create_directory(child)
    assert (win._acl(child), (child / 'intent.json').read_bytes()) == before


@pytest.mark.parametrize('mask', [0x2, 0x4, 0x10, 0x100, 0x40, 0x10000, 0x40000, 0x80000, 0x40000000, 0x10000000])
def test_real_external_parent_write_or_replace_rights_refused_before_native_create(native, mask):
    parent, sid, win, scoped, private = native
    set_fixture_acl(parent, sid, mask)
    before = win._acl(parent)
    child = parent / 'never-created'
    with pytest.raises(m.Held, match='operator_parent_replaceable|evidence_parent_external_write'):
        private.create_directory(child)
    assert not child.exists() and win._acl(parent) == before


def test_inherit_only_write_grant_cannot_reach_explicitly_protected_child(native):
    parent, sid, win, scoped, private = native
    set_fixture_acl(parent, sid, 0x120116, flags='OICIIO')
    before = win._acl(parent)
    child = parent / 'no-inherited-write'
    private.create_directory(child)
    assert win._acl(child)[1] is True
    assert EXTRA_SID not in {trustee for trustee, _, _ in win._acl(child)[2]}
    assert win._acl(parent) == before


def test_evidence_child_itself_remains_exactly_private_after_parent_relaxation(native):
    parent, sid, win, scoped, private = native
    set_fixture_acl(parent, sid, 0x1200A9)
    child = parent / 'private-child'
    private.create_directory(child)
    set_fixture_acl(child, sid, 0x1200A9)
    with pytest.raises(m.Held, match='private_extra_trustee'):
        private.validate(child)


@pytest.mark.parametrize('mask', [0x40, 0x10000, 0x40000, 0x80000])
def test_ordinary_ancestor_replacement_guard_is_preserved(native, mask):
    parent, sid, win, scoped, private = native
    set_fixture_acl(parent, sid, 0x1200A9)
    original_acl = scoped._acl
    ancestor = parent.parent

    def malicious_ancestor(path):
        owner, protected, rows = original_acl(path)
        return (owner, protected, [*rows, (EXTRA_SID, mask, 0)]) if Path(path) == ancestor else (owner, protected, rows)

    scoped._acl = malicious_ancestor
    with pytest.raises(m.Held, match='operator_parent_replaceable'):
        private.create_directory(parent / 'never-created')
    assert not (parent / 'never-created').exists()


def test_original_source_and_all_protocol_functions_unchanged():
    raw = ORIGINAL.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '930467a019b8e91ac19ba877fa34890dc64fa2966af3df5c2806e9e186e467a4'
    old_tree, new_tree = ast.parse(raw), ast.parse(SOURCE.read_bytes())
    old = {n.name: ast.dump(n, include_attributes=False) for n in old_tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    new = {n.name: ast.dump(n, include_attributes=False) for n in new_tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    assert old.keys() == new.keys()
    assert {name for name in old if old[name] != new[name]} == {'requests', 'WindowsPrivate', 'prepare_runtime'}
    old_private = next(n for n in old_tree.body if isinstance(n, ast.ClassDef) and n.name == 'WindowsPrivate')
    new_private = next(n for n in new_tree.body if isinstance(n, ast.ClassDef) and n.name == 'WindowsPrivate')
    methods = lambda node: {n.name: ast.dump(n, include_attributes=False) for n in node.body if isinstance(n, ast.FunctionDef)}
    old_methods, new_methods = methods(old_private), methods(new_private)
    assert {name for name in old_methods if old_methods[name] != new_methods[name]} == {'create_directory'}
    assert new_methods.keys() - old_methods.keys() == {'validate_evidence_parent'}
    assert m.PINS == base.load(ORIGINAL).PINS
    assert m.ROOT.name == 'live-commissioning-r3-v2'
    assert m.requests() != base.load(ORIGINAL).requests()
    assert m.requests() == m.requests()


def test_two_exact_requests_once_and_resume_only_observes():
    base.test_success_submits_two_exact_requests_once_and_resume_only_gets()


@pytest.mark.parametrize('when', ['before_marker', 'after_marker', 'before_post_delivery', 'after_post_delivery'])
def test_ambiguous_planning_attempt_never_resubmits(when):
    base.test_crashes_never_resubmit_planning_even_when_get_reports400(when)


@pytest.mark.parametrize('when', ['before_marker', 'after_marker', 'after_post_delivery'])
def test_ambiguous_coding_attempt_never_resubmits(when):
    base.test_coding_submit_gate_and_ambiguous_attempt_never_repeated(when)


def test_planning_validation_is_required_before_coding_post():
    base.test_planning_validation_failure_cannot_release_coding()


def test_timeout_does_not_cancel_or_resubmit_work():
    base.test_observation_timeout_is_pending_without_cancellation_or_budget_mutation()


@pytest.mark.parametrize('change', ['instance', 'capacity', 'source', 'required_empty', 'knowledge_missing', 'docker_missing', 'paused'])
def test_same_required_runtime_readiness_gates_remain(change):
    base.test_current_controller_identity_and_nonvacuous_phase_readiness(change)


def test_default_preview_has_no_file_network_token_or_job_access(monkeypatch, capsys):
    base.test_default_preview_has_no_file_network_token_or_job_access(monkeypatch, capsys)


def test_no_cancel_resume_or_budget_policy_change():
    source = SOURCE.read_text()
    assert "client.call('/submit' if phase == 'planning' else '/coding/submit', request)" in source
    assert "'/cancel'" not in source and "'/coding/resume'" not in source and "'/routing/resume'" not in source
    assert 'max_attempts' not in m.PLANNING and 'max_dispatches' not in m.PLANNING
