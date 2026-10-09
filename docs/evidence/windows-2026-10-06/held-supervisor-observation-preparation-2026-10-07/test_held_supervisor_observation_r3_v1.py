"""Disposable ordinary-user fixtures; no account, SYSTEM, token or live service.

Actual paired SQLite guards, production config parser, Supervisor constructor
and held tick run with explicit inert native/health/sterile-child boundaries.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import sys

import pytest

WORK=Path(__file__).absolute().parent
REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
sys.path.insert(0,str(REPO/'src'))
spec=importlib.util.spec_from_file_location('held_observation',WORK/'held-supervisor-observation-r3-v1.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
STAGE=runpy.run_path(str(WORK/'stage-independent-supervisor-holds-r3-v1.py'),run_name='fixture_stage')
PIPELINE_CONFIG=Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\pipeline.json')


def snap(root): return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def staging_receipt():
    return {'schema':'cochem-independent-supervisor-staging/1','status':'INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED',
        'root':str(M.STAGING),'helper_sha256':M.STAGING_HELPER_SHA,'manifest_sha256':M.MANIFEST_SHA,'phase':'complete',
        'paired_evidence_sha256':M.EVIDENCE_SHA,'pair_creation_started':True,'activation_ready':False,'paid_repair_enabled':False,
        'component_recovery_enabled':False,'old_ledgers_opened':False,'active_warden_changed':False,'pipeline_configuration_changed':False,
        'provider_or_model_calls':0,'ledger_sha256':{'supervisor.db':'a'*64,'component-recovery.db':'b'*64},'paired_receipt_sha256':'c'*64}


@pytest.mark.parametrize('key,value',[('status','HELD'),('activation_ready',True),('paid_repair_enabled',True),('provider_or_model_calls',False),('manifest_sha256','a'*64),('paired_evidence_sha256','f'*64),('ledger_sha256',{}),('phase','copying')])
def test_staging_success_requires_exact_scope(key,value):
    report=staging_receipt();M.validate_staging_receipt(report);report[key]=value
    with pytest.raises(M.ObservationHeld):M.validate_staging_receipt(report)


def test_actual_config_parser_preserves_limits_routing_and_provider_hold(tmp_path):
    raw=json.loads(PIPELINE_CONFIG.read_bytes());manifest=json.loads((WORK/'independent-supervisor-staging-r3-v1.manifest.json').read_bytes())
    config=M.make_config(raw,manifest['selected_regression_targets'])
    path=tmp_path/'config.json';path.write_text(json.dumps(config),encoding='utf-8')
    from cochem_supervisor.config import load_config
    actual=load_config(path)
    assert (actual['max_per_incident'],actual['max_per_day'],actual['cooldown_seconds'])==(2,4,1800)
    assert actual['auto_deploy'] is False and actual['repair_worker']==M.IDENTITY
    assert actual['pipeline_providers']==raw['providers']
    assert {k:v for k,v in actual['pipeline_routing'].items() if k!='model_limits'}=={k:v for k,v in raw['routing'].items() if k!='model_limits'}
    assert raw['routing']['model_limits']=={} and all(value is None for value in actual['pipeline_routing']['model_limits'].values())
    assert 'gemini' in actual['provider_integration_holds']


@pytest.fixture
def native_boundary(tmp_path,monkeypatch):
    from cochem_pipeline import windows as win
    code=tmp_path/'code';code.mkdir();(code/'releases').mkdir()
    staging=tmp_path/'staging';(staging/'controls').mkdir(parents=True);(staging/'unpublished-private').mkdir()
    acceptance=staging/'acceptance';acceptance.mkdir();(acceptance/'fixture.py').write_text('# independent immutable fixture\n')
    pipeline=tmp_path/'pipeline';(pipeline/'source').mkdir(parents=True);(pipeline/'source/current.py').write_text('# preserved pipeline fixture\n')
    shutil.copyfile(PIPELINE_CONFIG,pipeline/'pipeline.json')
    data=tmp_path/'freshdata'
    for key,value in [('CODE',code),('STAGING',staging),('PIPELINE',pipeline),('DATA',data),('PRIVATE',data/'private'),('WORKSPACE',data/'workers/repair'),('COMMISSION',tmp_path/'commission.json')]:monkeypatch.setattr(M,key,value)
    events=[]
    def private(path,*args,**kwargs):
        path=Path(path).absolute();assert tmp_path in path.parents or tmp_path==path
        M.ordinary(path,directory=path.is_dir())
    monkeypatch.setattr(win,'require_system',lambda:None)
    for name in ('validate_code_path','validate_private_path','validate_private_directory'):monkeypatch.setattr(win,name,private)
    # Running helper source is an ordinary test file outside tmp; permission
    # fixture permits only this exact source plus the temporary namespace.
    monkeypatch.setattr(win,'validate_code_path',lambda path:None if Path(path)==Path(M.__file__) else private(path))
    def layout(private_root,workspaces,identities,add_defender):
        assert private_root==M.PRIVATE and workspaces=={'repair':M.WORKSPACE}
        assert identities['repair'].name==M.IDENTITY['name'] and add_defender is True
        M.PRIVATE.mkdir(parents=True);M.WORKSPACE.mkdir(parents=True);events.append('scoped_layout')
        return {'slots':{'repair':{'sid':'S-1-5-21-ordinary-fixture'}}}
    monkeypatch.setattr(win,'provision_layout',layout)
    monkeypatch.setattr(M,'repair_identity_presence',lambda:{'account_exists':False,'credential_exists':False,'credential_username_matches':False})
    pair=runpy.run_path(str(WORK/'bootstrap-unresolved-budget-pair.py'),run_name='fixture_bootstrap')
    pair['bootstrap_pair'].__globals__['private']=private
    evidence=json.loads((WORK/'unresolved-budget-evidence.proposed.json').read_bytes())
    source=staging/'unpublished-private/unresolved-pair';actual=pair['bootstrap_pair'](source,evidence,now=100)
    report=staging_receipt();report['paired_receipt_sha256']=M.sha((source/'paired-hold-complete.json').read_bytes())
    report['ledger_sha256']={k:v['sha256'] for k,v in actual['ledgers'].items()}
    (staging/'controls/staging-receipt.json').write_text(json.dumps(report),encoding='utf-8')
    shutil.copyfile(WORK/'unresolved-budget-evidence.proposed.json',staging/'controls/unresolved-budget-evidence.json')
    commission={'schema':'cochem-warden-commissioning/1','status':'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED','runtime_root':str(pipeline),
        'install_receipt_sha256':M.INSTALL_SHA,'config_sha256':M.CONFIG_SHA,'model_jobs_submitted':0,'full_srs_acceptance':False}
    M.COMMISSION.write_text(json.dumps(commission),encoding='utf-8')
    inputs={'schema':'cochem-held-supervisor-inputs/1','staging_receipt_sha256':M.sha((staging/'controls/staging-receipt.json').read_bytes()),'commissioning_receipt_sha256':M.sha(M.COMMISSION.read_bytes())}
    (code/'inputs.json').write_text(json.dumps(inputs),encoding='utf-8')
    manifest=json.loads((WORK/'independent-supervisor-staging-r3-v1.manifest.json').read_bytes())
    monkeypatch.setattr(M,'definitions',lambda:(STAGE,pair,manifest,{'verified':True,'files':109}))
    from cochem_supervisor import windows as supervisory_win
    monkeypatch.setattr(supervisory_win,'require_supervisor',lambda config:events.append('constructor_boundary'))
    from cochem_supervisor.engine import Supervisor
    monkeypatch.setattr(Supervisor,'_read_observation',lambda self:{'health':{'heartbeat':'missing','state':'unknown'},'incidents':[]})
    from cochem_supervisor.probes import ControllerClient
    def health(self,operation,data=None):
        assert operation=='/health' and data is None;events.append('health')
        return {'service_identity':'SYSTEM','pid':12345,'instance_id':'a'*32}
    monkeypatch.setattr(ControllerClient,'call',health)
    return {'root':tmp_path,'source':source,'pair':pair,'evidence':evidence,'events':events,'inputs_sha':M.sha((code/'inputs.json').read_bytes())}


def test_full_prepare_real_constructor_tick_and_copy_preserve_originals(native_boundary):
    fixture=native_boundary;before=snap(fixture['source']);pipeline=snap(M.PIPELINE)
    result=M.prepare('a'*32,fixture['inputs_sha'])
    assert result['status']=='HELD_OBSERVATION_PROVISIONED',result
    assert fixture['events']==['scoped_layout','constructor_boundary','health']
    assert result['daemon_running'] is False and result['paid_repair_enabled'] is False and result['component_recovery_enabled'] is False
    assert result['observed_controller_state']=='healthy' and result['held_observation_phase_entered'] is True
    assert snap(fixture['source'])==before and snap(M.PIPELINE)==pipeline
    assert M.require_paired_holds(M.PRIVATE)[0]['remaining_legacy_allowance'] is None
    status=json.loads((M.PRIVATE/'supervisor-status.json').read_bytes())
    assert status['observation']['health']['budget_authority_hold'] is True
    retained=snap(M.DATA)
    with pytest.raises(FileExistsError):M.prepare('a'*32,fixture['inputs_sha'])
    assert snap(M.DATA)==retained


@pytest.mark.parametrize('case',['existing_data','changed_source_receipt','copy_failure','constructor_failure','observation_failure'])
def test_failure_preserves_staged_pair_and_never_starts_daemon(native_boundary,monkeypatch,case):
    fixture=native_boundary;before=snap(fixture['source'])
    def fail(*args,**kwargs):raise RuntimeError('secret private failure text')
    if case=='existing_data':M.DATA.mkdir();(M.DATA/'preserved').write_bytes(b'preserve')
    if case=='changed_source_receipt':(fixture['source']/'paired-hold-complete.json').write_bytes(b'{}');before=snap(fixture['source'])
    if case=='copy_failure':monkeypatch.setattr(M,'closed_pair_copy',fail)
    if case=='constructor_failure':
        from cochem_supervisor import windows
        monkeypatch.setattr(windows,'require_supervisor',fail)
    if case=='observation_failure':
        from cochem_supervisor.engine import Supervisor
        monkeypatch.setattr(Supervisor,'_read_observation',fail)
    result=M.prepare('a'*32,fixture['inputs_sha'])
    assert result['status']=='OBSERVATION_PROVISION_HELD' and result['daemon_running'] is False
    assert result['partial_outputs_preserved'] is True and 'secret private' not in json.dumps(result)
    assert snap(fixture['source'])==before
    if case=='existing_data':assert (M.DATA/'preserved').read_bytes()==b'preserve' and not fixture['events']


def test_removing_or_changing_hold_cannot_enable_observation_entrypoint(native_boundary,monkeypatch):
    result=M.prepare('a'*32,native_boundary['inputs_sha']);assert result['status']=='HELD_OBSERVATION_PROVISIONED'
    from cochem_supervisor.config import load_config
    instance=M.held_supervisor_class()(load_config(M.CODE/'supervisor.json'))
    from cochem_supervisor import budget_authority
    monkeypatch.setattr(budget_authority,'read_status',lambda path:{'state':'UNRECORDED','blocked':False})
    with pytest.raises(M.ObservationHeld,match='REQUIRES_UNRESOLVED'):instance.tick(ignore_startup_grace=True)
    for method in ('_start','_stop','_restart_once','_recover_components'):
        with pytest.raises(M.ObservationHeld,match='ACTUATION_FORBIDDEN'):getattr(instance,method)()


def test_default_preview_has_no_io(monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',[M.__file__]);monkeypatch.setattr(M,'definitions',lambda:pytest.fail('default touched runtime'))
    assert M.main()==0 and json.loads(capsys.readouterr().out)['status']=='DRAFT_PREVIEW_NO_IO'


@pytest.mark.parametrize('account,credential,username,valid',[(False,False,False,True),(False,True,True,False),(True,False,False,False),(True,True,False,False),(True,True,True,True)])
def test_identity_mixed_state_is_held_before_any_provision(monkeypatch,account,credential,username,valid):
    from cochem_pipeline import windows as win
    monkeypatch.setattr(M,'repair_identity_presence',lambda:{'account_exists':account,'credential_exists':credential,'credential_username_matches':username})
    calls=[]
    monkeypatch.setattr(win,'_worker_token',lambda identity:calls.append('token') or 123)
    monkeypatch.setattr(win,'_close',lambda token:calls.append(('closed',token)))
    if valid:
        result=M.precheck_repair_identity()
        assert result['existing_system_credential_used_for_token_validation']==account
    else:
        with pytest.raises(M.ObservationHeld):M.precheck_repair_identity()
    assert calls==(['token',('closed',123)] if account and credential and username else [])


def test_unverified_existing_identity_never_reaches_layout(native_boundary,monkeypatch):
    from cochem_pipeline import windows as win
    monkeypatch.setattr(M,'repair_identity_presence',lambda:{'account_exists':True,'credential_exists':True,'credential_username_matches':True})
    monkeypatch.setattr(win,'_worker_token',lambda identity:(_ for _ in ()).throw(PermissionError()))
    result=M.prepare('a'*32,native_boundary['inputs_sha'])
    assert result['failure']['phase']=='repair_identity_precheck' and result['data_provisioning_started'] is False
    assert not M.DATA.exists() and not native_boundary['events']


def test_fixed_failure_code_only_and_bounded_timing_projection(tmp_path,monkeypatch):
    assert M.safe_failure(M.ObservationHeld('REPAIR_IDENTITY_MIXED_STATE'),'repair_identity_precheck')['code']=='REPAIR_IDENTITY_MIXED_STATE'
    assert M.safe_failure(M.ObservationHeld('secret private text'),'bindings')['code'] is None
    monkeypatch.setattr(M,'CODE',tmp_path)
    class Observer:
        def tick(self,**kwargs):return {'health':{'budget_authority_hold':True,'observation_seconds':1.25,'components':{'controller':{'state':'healthy'}}},'incidents':[{'private':'not published'}]}
    runtime={'sequence':0,'observation_timing':{'count':0,'max_seconds':0.0,'over_one_second_count':0,'full_srs_acceptance':False}}
    M.publish_cycle(Observer(),runtime)
    stored=json.loads((tmp_path/'observation-runtime.json').read_bytes())
    assert stored['observation_timing']=={'count':1,'max_seconds':1.25,'over_one_second_count':1,'full_srs_acceptance':False}
    assert 'private' not in json.dumps(stored)
