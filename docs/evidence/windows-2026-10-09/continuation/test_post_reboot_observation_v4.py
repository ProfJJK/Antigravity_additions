"""Post-reboot observer source closure; ordinary Windows and inert fixtures only.

These checks never run Apply, providers, real scheduled tasks, protected writes,
private reports, paid repair, or controller startup. They are preparation tests,
not live SYSTEM or 48-hour acceptance evidence.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import types

import pytest

W = Path(__file__).resolve().parent
ROOT = W / 'resource-observer-r3-v4'
INSTALLER = W / 'install-resource-observer-r3-v4.ps1'
BATCH = W / 'run-post-commissioning-setup-r3-v2.ps1'
FIRST = W / 'commission-first-warden-r3-v5.py'
TOP = W / 'run-pipeline-commissioning-r3-v5.ps1'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, W / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


generator = load('post_reboot_observer_generator', 'prepare-post-reboot-observation-v4.py')
installer = load('post_reboot_observer_installer_fixture', 'test_resource_observer_installer_r3_v3.py')
installer.SCRIPT = INSTALLER
post = load('post_reboot_batch_fixture', 'test_post_commissioning_setup_r3_v1.py')
post.baseline.SCRIPT = BATCH

package_name = '_post_reboot_observer_v4_fixture'
package = types.ModuleType(package_name)
package.__path__ = [str(ROOT / 'cochem_supervisor')]
sys.modules[package_name] = package
M = importlib.import_module(package_name + '.resource_observation')
entry_spec = importlib.util.spec_from_file_location('_post_reboot_observer_entry_v4_fixture', ROOT / 'observe_resources.py')
E = importlib.util.module_from_spec(entry_spec)
entry_spec.loader.exec_module(E)


# Keep a targeted subset of the established refusal/durability fixtures. They
# execute the active new function definitions with disposable ordinary files.
test_registration_collision_refuses_before_any_copy = installer.test_existing_namespaces_refuse_before_any_copy_or_registration
test_wrong_controller_binding_refuses_before_registration = installer.test_binding_failure_prevents_registration_and_new_roots
test_one_observer_start_is_durable_and_cannot_repeat = installer.test_registration_then_single_start_has_durable_intent_and_refuses_repeat
test_existing_disposition_never_runs_first_start = post.test_exact_post_commissioning_disposition_never_launches_first_start
test_no_missing_commissioning_startup_authority = post.test_missing_commissioning_is_not_a_startup_authority
test_injected_mode_cannot_replay_startup = post.test_even_an_injected_execute_mode_cannot_replay_first_start
test_phase_failure_preserves_and_skips_later_actions = post.test_actual_monitoring_phase_flow_preserves_failure_and_skips_every_later_action
test_changed_controller_identity_prevents_later_phase = post.baseline.test_controller_identity_or_proof_failure_prevents_later_phase


def commissioning_receipt():
    # The rest of the fixture follows the actual producer shape already used by
    # both-language baseline tests; the helper pin comes from final producer
    # bytes, independently of either new validator's expected constant.
    value = installer.commissioning_receipt()
    value['helper_sha256'] = digest(FIRST)
    return value


def powershell_gate(tmp_path, receipt):
    body = installer.commissioning_gate_fixture(tmp_path, receipt)
    body += r'''
    $accepted=$false;$proof=$null
    try{$proof=Read-ObserverCommissioning;$accepted=$true}catch{}
    @{accepted=$accepted;pid=$(if($null -ne $proof){$proof.Value.controller.pid}else{0})}|ConvertTo-Json
    '''
    return installer.run_ps(body)


@pytest.mark.parametrize('helper', ['current', 'v3', 'v1', 'unreviewed'])
def test_both_actual_commissioning_gates_accept_only_the_new_producer(tmp_path, helper):
    receipt = commissioning_receipt()
    receipt['helper_sha256'] = {
        'current': digest(FIRST), 'v3': generator.OLD_HELPER,
        'v1': 'bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c',
        'unreviewed': 'f'*64,
    }[helper]
    if helper == 'current':
        assert M.startup_binding(receipt) == receipt['controller']
    else:
        with pytest.raises(M.win.BoundaryError, match='COMMISSIONING_BINDING'):
            M.startup_binding(receipt)
    assert powershell_gate(tmp_path, receipt) == {'accepted': helper == 'current', 'pid': 1234 if helper == 'current' else 0}


@pytest.mark.parametrize('field,value', [
    ('authentication_attempt', None), ('authentication_attempt', '0'*32),
    ('authentication_attempt', 'A'*32), ('authentication_attempt', '1'*32+'\n'),
    ('authentication_receipt_sha256', True), ('authentication_receipt_sha256', 'b'*64+'\n'),
    ('authenticated_profiles_verified', True), ('authenticated_profiles_verified', 12.0),
    ('authenticated_profiles_verified', 13),
])
def test_new_helper_pin_does_not_relax_same_attempt_or_twelve_profile_proof(tmp_path, field, value):
    receipt = commissioning_receipt()
    receipt[field] = value
    with pytest.raises(M.win.BoundaryError, match='AUTHENTICATION_'):
        M.startup_binding(receipt)
    assert powershell_gate(tmp_path, receipt)['accepted'] is False


@pytest.mark.parametrize('field', ['authentication_attempt', 'authentication_receipt_sha256', 'authenticated_profiles_verified'])
def test_each_authentication_field_remains_mandatory_in_both_gates(tmp_path, field):
    receipt = commissioning_receipt()
    del receipt[field]
    with pytest.raises(M.win.BoundaryError, match='AUTHENTICATION_'):
        M.startup_binding(receipt)
    assert powershell_gate(tmp_path, receipt)['accepted'] is False


def test_new_producer_still_writes_authentication_proof_before_first_start_intent():
    tree = ast.parse(FIRST.read_bytes())
    run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
    source = ast.get_source_segment(FIRST.read_text(), run)
    proof = source.index("report['authentication_attempt']=packet['auth_attempt']")
    assert proof < source.index("phase='start_intent'")
    assert "report['authentication_receipt_sha256']=auth['receipt_sha256']" in source
    assert "report['authenticated_profiles_verified']=len(auth['profiles'])" in source
    assert "'helper_sha256':support.digest_file(Path(__file__))" in source
    assert "require(auth['receipt_sha256']==packet.get('auth_receipt_sha256'),'AUTH_PACKET_PIN')" in source


def test_manifest_closes_exactly_seven_sources_and_changes_only_the_two_intended_payloads():
    manifest = json.loads((ROOT / 'source-manifest.json').read_bytes())
    assert manifest['schema'] == 'cochem-external-observer-source-manifest/1'
    assert [row['path'] for row in manifest['files']] == list(generator.FILES)
    changed = []
    for row in manifest['files']:
        raw = (ROOT / row['path']).read_bytes()
        assert len(raw) == row['size'] and hashlib.sha256(raw).hexdigest() == row['sha256']
        if raw != (W / 'resource-observer-r3-v3' / row['path']).read_bytes():
            changed.append(row['path'])
    assert changed == ['observe_resources.py', 'cochem_supervisor/resource_observation.py']
    assert len(E.verify_sources(ROOT, (ROOT / 'source-manifest.json').read_bytes())['files']) == 7


def test_readonly_generator_reproduces_every_final_byte_without_any_write():
    outputs = generator.render(digest(FIRST), digest(TOP))
    assert len(outputs) == 10
    for name, raw in outputs.items():
        assert (W / name).read_bytes() == raw, name


@pytest.mark.parametrize('first_sha,top_sha', [
    ('0'*63, 'a'*64), ('A'*64, 'a'*64), (None, 'a'*64),
    (generator.OLD_HELPER, 'a'*64), ('a'*64, generator.OLD_TOP),
])
def test_generator_refuses_missing_malformed_or_stale_release_pins(first_sha, top_sha):
    with pytest.raises(ValueError):
        generator.render(first_sha, top_sha)


def test_new_installer_and_batch_close_pins_namespaces_and_preserve_dependency_policy():
    source = INSTALLER.read_text()
    batch = BATCH.read_text()
    manifest_pin = digest(ROOT / 'source-manifest.json')
    assert digest(FIRST) in source and digest(FIRST) in (ROOT / 'cochem_supervisor/resource_observation.py').read_text()
    assert re.search(r"\$sourceManifestHash='([a-f0-9]{64})'", source)[1] == manifest_pin
    assert "'resource-observer-r3-v4'" in source
    assert 'ResourceObservation4.2.7-windows-20261008-r3-v4' in source
    assert 'ResourceObservationState4.2.7-windows-20261008-r3-v4' in source
    assert 'CoChem-4.2.7-ResourceObservation-20261008-r3-v4' in source
    assert digest(INSTALLER) in batch and INSTALLER.name in batch
    assert digest(TOP) in batch and TOP.name in batch
    assert manifest_pin in batch
    assert 'ResourceObservation4.2.7-windows-20261008-r3-v4\\observer-started.json' in batch
    assert 'PostCommissioningSetup4.2.7-windows-20261008-r3-v2' in batch
    assert 'ResourceObservation4.2.7-windows-20261008-r3-v3' not in batch
    assert E.DEPENDENCY_PIN == digest(W / 'resource-observer-r3-v2-dependencies.json') == generator.DEPENDENCIES
    assert 'resource-observer-r3-v2-dependencies.json' in source
    assert generator.DEPENDENCIES in source and generator.DEPENDENCIES in batch
    assert 'WardenCommissioning4.2.7-windows-20261007-r3-v1' in source
    assert "$scheduler.GetFolder('\\')" in batch and "$scheduler.GetFolder('')" not in batch


def test_unchanged_observation_functions_do_not_gain_recovery_or_inference_behavior():
    for relative in ('observe_resources.py', 'cochem_supervisor/resource_observation.py'):
        def functions(path):
            return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(path.read_bytes()).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert functions(ROOT / relative) == functions(W / 'resource-observer-r3-v3' / relative)
    assert E.CODE_ROOT.name.endswith('-r3-v4') and E.STATE_ROOT.name.endswith('-r3-v4')
    assert E.INSTALLATION_CONTRACT_RELEASED is True
    assert M.EXPECTED['automatic_repair_enabled'] is False
    assert M.EXPECTED['automatic_retry_allowed'] is False
    assert M.EXPECTED['full_srs_acceptance'] is False
    assert M.EXPECTED['monitoring_scope'] == 'heartbeat_and_queue_only'


@pytest.mark.parametrize('filename,pin', [
    ('install-resource-observer-r3-v3.ps1', generator.OLD_INSTALLER),
    ('run-post-commissioning-setup-r3-v1.ps1', generator.OLD_BATCH),
    ('resource-observer-r3-v3/source-manifest.json', generator.OLD_MANIFEST),
    ('resource-observer-r3-v2-dependencies.json', generator.DEPENDENCIES),
])
def test_historical_sources_and_dependencies_stay_preserved(filename, pin):
    assert digest(W / filename) == pin


def test_entry_default_remains_inert_and_does_not_claim_live_acceptance():
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(ROOT / 'observe_resources.py')], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['prepared_only'] is True and value['full_srs_acceptance'] is False
    assert value['duration_seconds'] == 172800
    assert value['no_database_opens'] and value['no_controller_calls']
