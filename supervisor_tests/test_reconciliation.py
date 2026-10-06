"""Protected real file captures; receipt dictionaries are protocol fixtures only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from cochem_supervisor.releases import ReleaseError
from cochem_supervisor.reconciliation import build_review_manifest, review_output_contract, validate_review, digest


@pytest.fixture
def captured(tmp_path):
    repository=Path(__file__).resolve().parents[1]
    specification=tmp_path/'specification';specification.mkdir()
    for source,name in [('4.2.7_SRS.md','4.2.7_SRS.md'),
                        ('docs/SRS_ADDENDUM_4.2.7.md','SRS_ADDENDUM_4.2.7.md'),
                        ('docs/requirements_4.2.7.json','requirements_4.2.7.json')]:
        shutil.copyfile(repository/source,specification/name)
    baseline=tmp_path/'baseline';candidate=tmp_path/'candidate'
    target='src/cochem_pipeline/example.py'
    for directory,value in ((baseline,1),(candidate,2)):
        path=directory/target;path.parent.mkdir(parents=True)
        path.write_text(f'# Source is read as data, never imported\nvalue = {value}\n')
    arguments={'incident':{'fingerprint':'independent-incident','category':'code'},
        'allowed_paths':['src/cochem_pipeline/'],
        'test_evidence':{'passed':2,'failures':0,'errors':0,'skipped':0,'tests':2},
        'outer_evidence':{'passed':True,'contracts':['protected-files']}}
    manifest=build_review_manifest(specification,baseline,candidate,**arguments)
    return specification,baseline,candidate,arguments,manifest


def native(provider,model):
    return {'provider':provider,'requested_model':model,'reported_model':model,
        'subscription_verified':True,'terminal_success':True,'exit_code':0,'pid':123,
        'session_id':'explicit-protocol-fixture','stdout_sha256':'a'*64}


def test_capture_binds_real_canonical_clauses_file_bytes_diff_wbs_and_independent_tests(captured):
    specification,baseline,candidate,arguments,manifest=captured
    assert manifest['artifact_hashes']['4.2.7_SRS.md']==hashlib.sha256((specification/'4.2.7_SRS.md').read_bytes()).hexdigest()
    ledger=json.loads((specification/'requirements_4.2.7.json').read_text())
    assert {row['id'] for row in manifest['requirements']}=={row['id'] for row in ledger['requirements']}
    assert manifest['artifact_hashes']['repair_wbs']==digest(manifest['wbs'])
    assert manifest['artifact_hashes']['regression_evidence']==digest(arguments['test_evidence'])
    assert manifest['files'][0]['after_text'].endswith('value = 2\n')
    output=review_output_contract(manifest)
    receipt=native('claude','claude-sonnet-5-5');receipt['review_output_sha256']=digest(output)
    result=validate_review(output,manifest,receipt,native('codex','gpt-6-sol'))
    assert result['approved'] is True
    assert result['manifest_sha256']==digest(manifest)
    assert (candidate/'src/cochem_pipeline/example.py').read_text().endswith('value = 2\n')


@pytest.mark.parametrize('mutation',['manifest','artifact','coverage','duplicate','chapter','wbs','findings','self','receipt'])
def test_generic_pass_or_partial_wrong_self_review_cannot_approve_repair(captured,mutation):
    manifest=captured[-1]
    output=review_output_contract(manifest)
    receipt=native('claude','claude-sonnet-5-5');producer=native('codex','gpt-6-sol')
    if mutation=='manifest':output['manifest_sha256']='b'*64
    elif mutation=='artifact':output['artifact_hashes']['candidate_tree']='b'*64
    elif mutation=='coverage':output['requirements_checked'].pop()
    elif mutation=='duplicate':output['requirements_checked'][-1]=deepcopy(output['requirements_checked'][0])
    elif mutation=='chapter':output['requirements_checked'][0]['chapter']='Obsolete chapter'
    elif mutation=='wbs':output['requirements_checked'][0]['wbs_ids']=['undeclared-task']
    elif mutation=='findings':output['findings']=['The proposed code weakens a protected execution boundary.']
    elif mutation=='self':receipt['provider']=producer['provider']
    receipt['review_output_sha256']=digest(output)
    if mutation=='receipt':receipt['review_output_sha256']='b'*64
    with pytest.raises(ValueError):validate_review(output,manifest,receipt,producer)


def test_exact_same_id_canonical_wording_drift_invalidates_catalog(captured):
    specification,baseline,candidate,arguments,_=captured
    path=specification/'4.2.7_SRS.md'
    path.write_text(path.read_text().replace('Current explicit owner decisions','Altered explicit owner decisions',1))
    with pytest.raises(ValueError,match='hashes/coverage'):
        build_review_manifest(specification,baseline,candidate,**arguments)
    catalog=specification/'requirements_4.2.7.json'
    ledger=json.loads(catalog.read_text());ledger['specification_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    catalog.write_text(json.dumps(ledger))
    with pytest.raises(ValueError,match='wording differs'):
        build_review_manifest(specification,baseline,candidate,**arguments)


def test_failed_outer_evidence_and_unapproved_paths_prevent_review_capture(captured):
    specification,baseline,candidate,arguments,_=captured
    with pytest.raises(ValueError,match='passing protected'):
        build_review_manifest(specification,baseline,candidate,**{**arguments,'outer_evidence':{'passed':False}})
    unauthorized=candidate/'src/cochem_supervisor/engine.py';unauthorized.parent.mkdir()
    unauthorized.write_text('pass\n')
    with pytest.raises(ReleaseError,match='protected or unapproved'):
        build_review_manifest(specification,baseline,candidate,**arguments)


def test_candidate_python_is_read_without_import_or_execution(captured):
    specification,baseline,candidate,arguments,_=captured
    marker=candidate/'executed-by-accident'
    (candidate/'src/cochem_pipeline/example.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n')
    manifest=build_review_manifest(specification,baseline,candidate,**arguments)
    assert 'write_text' in manifest['files'][0]['after_text']
    assert not marker.exists()


def test_negative_model_reconciliation_has_actionable_findings_and_no_approval(captured):
    manifest=captured[-1];output=review_output_contract(manifest)
    output['verdict']='FAIL';output['requirements_checked'][0]['status']='DIVERGED'
    output['findings']=['The proposed diff changes documented failure isolation into global cancellation.']
    receipt=native('claude','claude-sonnet-5-5');receipt['review_output_sha256']=digest(output)
    assert validate_review(output,manifest,receipt,native('codex','gpt-6-sol'))['approved'] is False
