"""DRAFT: independently installed observation with every actuator permanently held.

Default preview has no filesystem or native I/O. A separately reviewed SYSTEM
wrapper must establish custody and stage immutable inputs before --prepare or
--observe. This file is not yet an owner command. No generic installer is used.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import signal
import stat
import sys
import time
import uuid

CODE = Path(r'C:\Program Files\CoChem\SupervisorObservation4.2.7-windows-20261007-r3-v1')
STAGING = Path(r'C:\Program Files\CoChem\Supervisor4.2.7-windows-20261007-staging-v1')
DATA = Path(r'C:\ProgramData\CoChemSupervisor427-observation-20261007-r3-v1')
PRIVATE = DATA / 'private'
WORKSPACE = DATA / 'workers/repair'
PIPELINE = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
COMMISSION = Path(r'C:\Program Files\CoChem\WardenCommissioning4.2.7-windows-20261007-r3-v1\commissioning.json')
STAGING_HELPER_SHA = '215af8db414c7bbc4352c825dbf250b7bbc244a8a2dbc2d4addadc72cf1acb74'
MANIFEST_SHA = '6aec5eaf82205be34e5849e73700f2e817e157be5c5ddd4366f04e549b75f0c4'
CONFIG_SHA = '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'
INSTALL_SHA = '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6'
EVIDENCE_SHA = '1cf291591724b6edef17ba7e8d48fc6cce75830872ff6183ac1139aa313a3177'
TASK = 'CoChem-4.2.7-HeldSupervisorObservation-20261007-r3-v1'
IDENTITY = {'name': 'CoChem423Repair', 'credential_target': 'CoChem423/repair'}
PAIR_NAMES = ('bootstrap-intent.json', 'unresolved-evidence.json', 'supervisor.db', 'component-recovery.db', 'paired-hold-complete.json')
SAFE_CODES=frozenset({'REPARSE_CONTROL','NONORDINARY_CONTROL','STAGING_BINDING','COMMISSION_BINDING','OVERSIZED_SUPPORT',
    'STAGING_HELPER_CHANGED','PAIR_SOURCE_RECEIPT','PRESERVE_TARGET_PRIVATE_STATE','COPIED_PAIR_DIFFERS','ORIGINAL_PAIR_CHANGED',
    'OBSERVATION_REQUIRES_UNRESOLVED_AUTHORITY','PAIR_AUTHORITY_DIFFERS','GENERIC_GUARD_DID_NOT_HOLD','ACTUATION_FORBIDDEN',
    'INVALID_INVOCATION','INVALID_INPUTS','SOURCE_AUTHORITY','PRESERVE_EXISTING_DATA_ROOT','PRESERVE_EXISTING_CONTROL',
    'PRESERVE_RELEASE_ROOT','OBSERVATION_NOT_HELD','REPORT_TOO_LARGE','NOT_PROVISIONED','OBSERVATION_CONTRACT',
    'REPAIR_IDENTITY_MIXED_STATE','REPAIR_CREDENTIAL_USERNAME_DIFFERS','REPAIR_ACCOUNT_QUERY_FAILED',
    'OBSERVATION_DURATION_INVALID'})


class ObservationHeld(ValueError):
    pass


def sha(raw): return hashlib.sha256(raw).hexdigest()


def safe_failure(error,phase):
    code=getattr(error,'winerror',None)
    return {'phase':phase,'error_type':type(error).__name__,
        'winerror':code if type(code) is int and 0<=code<=65535 else None,
        'code':str(error) if isinstance(error,ObservationHeld) and str(error) in SAFE_CODES else None}


def repair_identity_presence():
    """SYSTEM exact-name metadata query; credential blob is never dereferenced."""
    from cochem_pipeline import windows as win
    win.require_system()
    net=win._api()['netapi32']
    net.NetUserGetInfo.argtypes=[win.LPWSTR,win.LPWSTR,win.DWORD,C.POINTER(win.HANDLE)]
    net.NetUserGetInfo.restype=win.DWORD
    net.NetApiBufferFree.argtypes=[win.HANDLE];net.NetApiBufferFree.restype=win.DWORD
    buffer=win.HANDLE()
    status=net.NetUserGetInfo(None,IDENTITY['name'],0,C.byref(buffer))
    if status==0:
        try: account=True
        finally: net.NetApiBufferFree(buffer)
    elif status==2221: account=False
    else: raise C.WinError(status)
    credential=C.POINTER(win._CREDENTIALW)()
    api=win._api()['advapi32']
    if api.CredReadW(IDENTITY['credential_target'],1,0,C.byref(credential)):
        try:
            present=True
            # Inspect only the public target owner name, not CredentialBlob.
            matches=(credential.contents.UserName or '').casefold()==f"{os.environ['COMPUTERNAME']}\\{IDENTITY['name']}".casefold()
        finally: api.CredFree(credential)
    else:
        code=C.get_last_error()
        if code!=1168: raise C.WinError(code)
        present=False;matches=False
    return {'account_exists':account,'credential_exists':present,'credential_username_matches':matches}


def precheck_repair_identity():
    from cochem_pipeline import windows as win
    value=repair_identity_presence()
    if value['account_exists'] != value['credential_exists']:
        raise ObservationHeld('REPAIR_IDENTITY_MIXED_STATE')
    if value['account_exists']:
        if value['credential_username_matches'] is not True:
            raise ObservationHeld('REPAIR_CREDENTIAL_USERNAME_DIFFERS')
        # Validate existing credentials, batch rights, SID and all unprivileged
        # token groups BEFORE provision_layout can grant any existing right.
        token=win._worker_token(win.WorkerIdentity(**IDENTITY))
        win._close(token)
    return {**value,'existing_system_credential_used_for_token_validation':value['account_exists']}


def ordinary(path, *, directory=False):
    for item in (Path(path).absolute(),*Path(path).absolute().parents):
        info=item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ObservationHeld('REPARSE_CONTROL')
    info=Path(path).lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink==1):
        raise ObservationHeld('NONORDINARY_CONTROL')
    return info


def exclusive(path, raw, *, private=False):
    from cochem_pipeline import windows as win
    win.validate_private_directory(path.parent) if private else win.validate_code_path(path.parent)
    with path.open('xb') as stream:
        win.validate_private_path(path) if private else win.validate_code_path(path)
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def make_config(pipeline, targets):
    """Use exact current provider/routing policy and real production defaults."""
    from cochem_supervisor.config import DEFAULTS
    value = deepcopy(DEFAULTS)
    value.update(private_root=str(PRIVATE), repair_workspace=str(WORKSPACE),
        release_root=str(CODE / 'releases'), baseline_source=str(PIPELINE / 'source'),
        acceptance_root=str(STAGING / 'acceptance'), pipeline_python=str(PIPELINE / '.venv/Scripts/python.exe'),
        pipeline_config=str(PIPELINE / 'pipeline.json'), pointer_file=str(PRIVATE / 'observation-release.json'),
        test_python=str(STAGING / '.venv/Scripts/python.exe'), operator_name=pipeline['operator_name'],
        repair_worker=dict(IDENTITY), providers=[{'provider': key, **deepcopy(spec)} for key, spec in pipeline['providers'].items()],
        repair_execution_limits=deepcopy(pipeline['execution_limits']), auto_deploy=False,
        warden_task='CoChem-4.2.7-Warden', supervisor_task=TASK, test_targets=list(targets))
    # These are additional hard execution restrictions of this entrypoint,
    # not a claim that generic config.auto_deploy alone blocks paid generation.
    value['observation_only_contract'] = {'schema': 'cochem-held-observation/1',
        'authority_evidence_sha256': EVIDENCE_SHA, 'all_actuation_disabled': True,
        'warden_task_or_pointer_changes_authorized': False}
    return value


def validate_staging_receipt(value):
    if (value.get('schema') != 'cochem-independent-supervisor-staging/1'
            or value.get('status') != 'INDEPENDENT_SUPERVISOR_STAGED_UNPUBLISHED'
            or value.get('root') != str(STAGING) or value.get('helper_sha256') != STAGING_HELPER_SHA
            or value.get('manifest_sha256') != MANIFEST_SHA or value.get('phase') != 'complete'
            or value.get('paired_evidence_sha256') != EVIDENCE_SHA
            or value.get('pair_creation_started') is not True
            or any(value.get(key) is not False for key in ('activation_ready','paid_repair_enabled',
                'component_recovery_enabled','old_ledgers_opened','active_warden_changed','pipeline_configuration_changed'))
            or value.get('provider_or_model_calls') != 0 or type(value.get('provider_or_model_calls')) is not int
            or set(value.get('ledger_sha256', {})) != {'supervisor.db','component-recovery.db'}
            or not all(re.fullmatch('[a-f0-9]{64}', str(x)) for x in value['ledger_sha256'].values())
            or not re.fullmatch('[a-f0-9]{64}', str(value.get('paired_receipt_sha256')))):
        raise ObservationHeld('STAGING_BINDING')


def validate_commission(value):
    if (value.get('schema') != 'cochem-warden-commissioning/1'
            or value.get('status') != 'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED'
            or value.get('runtime_root') != str(PIPELINE)
            or value.get('install_receipt_sha256') != INSTALL_SHA
            or value.get('config_sha256') != CONFIG_SHA
            or value.get('model_jobs_submitted') != 0
            or value.get('full_srs_acceptance') is not False):
        raise ObservationHeld('COMMISSION_BINDING')


def definitions():
    """Read only exact protected previously reviewed definition files."""
    from cochem_pipeline import windows as win
    win.require_system()
    path = STAGING / 'controls/stage-independent-supervisor-holds-r3-v1.py'
    if ordinary(path).st_size>65536: raise ObservationHeld('OVERSIZED_SUPPORT')
    win.validate_code_path(path)
    # Bound before executing even definition-only support.
    raw = path.read_bytes()
    if sha(raw) != STAGING_HELPER_SHA: raise ObservationHeld('STAGING_HELPER_CHANGED')
    stage = runpy.run_path(str(path), run_name='held_staging_definitions')
    manifest, revision = stage['verify_runtime'](MANIFEST_SHA)
    bootstrap = STAGING / 'controls/bootstrap-unresolved-budget-pair.py'
    stage['read'](bootstrap, stage['BOOTSTRAP_SHA'])
    pair = runpy.run_path(str(bootstrap), run_name='held_pair_definitions')
    return stage, pair, manifest, revision


def closed_pair_copy(source, target, stage, pair, evidence, expected_receipt):
    """Copy only the closed fresh held pair; never transform/replay old history."""
    from cochem_pipeline import windows as win
    value = pair['verify_pair'](source, evidence)
    if sha(stage['read'](source / 'paired-hold-complete.json')) != expected_receipt:
        raise ObservationHeld('PAIR_SOURCE_RECEIPT')
    win.validate_private_directory(target)
    if list(target.iterdir()): raise ObservationHeld('PRESERVE_TARGET_PRIVATE_STATE')
    before = {name: stage['read'](source / name, limit=16 * 1024 * 1024) for name in PAIR_NAMES}
    for name, raw in before.items(): exclusive(target / name, raw, private=True)
    if pair['verify_pair'](target, evidence) != value:
        raise ObservationHeld('COPIED_PAIR_DIFFERS')
    if any(stage['read'](source / name) != raw for name, raw in before.items()):
        raise ObservationHeld('ORIGINAL_PAIR_CHANGED')
    return {name: sha(raw) for name, raw in before.items()}


def require_paired_holds(private):
    from cochem_supervisor.budget_authority import read_status
    values = [read_status(private / name) for name in ('supervisor.db','component-recovery.db')]
    for value in values:
        if (value.get('state') != 'UNRESOLVED_LEGACY_AUTHORITY' or value.get('blocked') is not True
                or value.get('evidence_sha256') != EVIDENCE_SHA or value.get('historical_spend_verified') is not False
                or value.get('remaining_legacy_allowance') is not None):
            raise ObservationHeld('OBSERVATION_REQUIRES_UNRESOLVED_AUTHORITY')
    if values[0] != values[1]: raise ObservationHeld('PAIR_AUTHORITY_DIFFERS')
    return values


def held_supervisor_class():
    from cochem_supervisor.engine import Supervisor
    class HeldObservationSupervisor(Supervisor):
        """Even removal of a hold can never authorize this entrypoint to actuate."""
        def _budget_authority_hold(self):
            require_paired_holds(self.private)
            if super()._budget_authority_hold() is not True:
                raise ObservationHeld('GENERIC_GUARD_DID_NOT_HOLD')
            return True

        def _start(self, *args, **kwargs): raise ObservationHeld('ACTUATION_FORBIDDEN')
        def _stop(self, *args, **kwargs): raise ObservationHeld('ACTUATION_FORBIDDEN')
        def _restart_once(self, *args, **kwargs): raise ObservationHeld('ACTUATION_FORBIDDEN')
        def _recover_components(self, *args, **kwargs): raise ObservationHeld('ACTUATION_FORBIDDEN')
    return HeldObservationSupervisor


def prepare(nonce, inputs_sha):
    """Future SYSTEM one-shot; production APIs used only on fresh dedicated roots."""
    from cochem_pipeline import windows as win
    win.require_system()
    if not re.fullmatch('[a-f0-9]{32}', nonce) or not re.fullmatch('[a-f0-9]{64}', inputs_sha):
        raise ObservationHeld('INVALID_INVOCATION')
    ordinary(CODE,directory=True);ordinary(Path(__file__))
    win.validate_code_path(CODE); win.validate_code_path(Path(__file__))
    report = {'schema':'cochem-held-supervisor-provision/1','nonce':nonce,'status':'OBSERVATION_PROVISION_HELD',
        'phase':'bindings','root':str(CODE),'data_root':str(DATA),'inputs_sha256':inputs_sha,
        'helper_sha256':sha(Path(__file__).read_bytes()),
        'data_provisioning_started':False,'pair_copy_started':False,'held_observation_phase_entered':False,
        'model_jobs_submitted':0,'paid_repair_enabled':False,'component_recovery_enabled':False,
        'existing_warden_changed':False,'existing_pipeline_pointer_changed':False,'old_ledgers_opened':False,
        'partial_outputs_preserved':True,'daemon_running':False,'full_srs_acceptance':False}
    # Public bounded receipt exists before any provisioning, including failure.
    with (CODE / 'provisioning.json').open('xb') as output:
        try:
            stage, pair, manifest, revision = definitions()
            stage['ordinary'](CODE, True)
            inputs = json.loads(stage['read'](CODE / 'inputs.json', inputs_sha, limit=65536))
            if (inputs.get('schema') != 'cochem-held-supervisor-inputs/1'
                    or set(inputs) != {'schema','staging_receipt_sha256','commissioning_receipt_sha256'}
                    or not all(re.fullmatch('[a-f0-9]{64}', str(inputs[k])) for k in ('staging_receipt_sha256','commissioning_receipt_sha256'))):
                raise ObservationHeld('INVALID_INPUTS')
            staged = json.loads(stage['read'](STAGING / 'controls/staging-receipt.json', inputs['staging_receipt_sha256'],limit=65536))
            validate_staging_receipt(staged)
            validate_commission(json.loads(stage['read'](COMMISSION, inputs['commissioning_receipt_sha256'],limit=65536)))
            evidence = json.loads(stage['read'](STAGING / 'controls/unresolved-budget-evidence.json',stage['EVIDENCE_SHA']))
            original = STAGING / 'unpublished-private/unresolved-pair'
            source_pair = pair['verify_pair'](original, evidence)
            if source_pair['evidence_sha256'] != EVIDENCE_SHA: raise ObservationHeld('SOURCE_AUTHORITY')
            pipeline = json.loads(stage['read'](PIPELINE / 'pipeline.json', CONFIG_SHA))
            config = make_config(pipeline,manifest['selected_regression_targets'])
            report.update(staging_receipt_sha256=inputs['staging_receipt_sha256'],commissioning_receipt_sha256=inputs['commissioning_receipt_sha256'],revision=revision)
            report['phase']='fresh_boundary'
            try: DATA.lstat()
            except FileNotFoundError: pass
            else: raise ObservationHeld('PRESERVE_EXISTING_DATA_ROOT')
            for path in (CODE / 'supervisor.json',PRIVATE / 'observation-release.json'):
                try: path.lstat()
                except FileNotFoundError: pass
                else: raise ObservationHeld('PRESERVE_EXISTING_CONTROL')
            # Wrapper precreates only this empty protected code directory.
            stage['ordinary'](CODE / 'releases',True);win.validate_code_path(CODE / 'releases')
            if list((CODE / 'releases').iterdir()): raise ObservationHeld('PRESERVE_RELEASE_ROOT')
            report['phase']='repair_identity_precheck'
            report['repair_identity_precheck']=precheck_repair_identity()
            report['phase']='dedicated_identity_layout';report['data_provisioning_started']=True
            layout=win.provision_layout(PRIVATE,{'repair':WORKSPACE},{'repair':win.WorkerIdentity(**IDENTITY)},add_defender=True)
            report['phase']='closed_held_pair_copy';report['pair_copy_started']=True
            report['copied_pair_sha256']=closed_pair_copy(original,PRIVATE,stage,pair,evidence,staged['paired_receipt_sha256'])
            report['phase']='observation_configuration'
            encoded=(json.dumps(config,indent=2)+'\n').encode()
            exclusive(CODE / 'supervisor.json',encoded)
            report['configuration_sha256']=sha(encoded)
            from cochem_supervisor.releases import ReleaseStore,tree_manifest
            releases=ReleaseStore(Path(config['release_root']),Path(config['pointer_file']),PRIVATE / 'release-journal.json')
            releases.bootstrap(PIPELINE / 'source',tree_manifest(PIPELINE / 'source'))
            win.validate_private_path(config['pointer_file'])
            from cochem_supervisor.config import load_config
            validated=load_config(CODE / 'supervisor.json')
            report['phase']='held_constructor_and_observation'
            supervisor=held_supervisor_class()(validated)
            try:
                require_paired_holds(PRIVATE)
                report['held_observation_phase_entered']=True
                observed=supervisor.tick(ignore_startup_grace=True)
                if observed.get('health',{}).get('budget_authority_hold') is not True:
                    raise ObservationHeld('OBSERVATION_NOT_HELD')
            finally: supervisor.runner.terminate()
            report['phase']='preservation'
            if pair['verify_pair'](original,evidence)!=source_pair: raise ObservationHeld('ORIGINAL_PAIR_CHANGED')
            stage['read'](PIPELINE / 'pipeline.json',CONFIG_SHA)
            require_paired_holds(PRIVATE)
            report.update(status='HELD_OBSERVATION_PROVISIONED',phase='complete',observation_verified=True,
                observed_controller_state=observed['health'].get('components',{}).get('controller',{}).get('state','unknown'),
                repair_identity_sid=layout['slots']['repair']['sid'])
        except Exception as error:
            report['failure']=safe_failure(error,report['phase'])
        finally:
            raw=(json.dumps(report,indent=2,allow_nan=False)+'\n').encode()
            if len(raw)>65536: raise ObservationHeld('REPORT_TOO_LARGE')
            output.write(raw);output.flush();os.fsync(output.fileno())
    return report


def observe():
    from cochem_pipeline import windows as win
    win.require_system();ordinary(CODE,directory=True);ordinary(Path(__file__))
    win.validate_code_path(CODE);win.validate_code_path(Path(__file__))
    stage,_,_,_=definitions()
    provision=json.loads(stage['read'](CODE / 'provisioning.json',limit=65536))
    if provision.get('status')!='HELD_OBSERVATION_PROVISIONED' or provision.get('data_root')!=str(DATA):
        raise ObservationHeld('NOT_PROVISIONED')
    stage['read'](CODE / 'supervisor.json',provision['configuration_sha256'])
    from cochem_supervisor.config import load_config
    from cochem_supervisor.__main__ import supervisor_lock
    config=load_config(CODE / 'supervisor.json')
    if config['auto_deploy'] is not False or config.get('observation_only_contract',{}).get('all_actuation_disabled') is not True:
        raise ObservationHeld('OBSERVATION_CONTRACT')
    require_paired_holds(PRIVATE)
    with supervisor_lock(PRIVATE / 'supervisor.lock'):
        supervisor=held_supervisor_class()(config)
        import psutil
        process=psutil.Process(os.getpid())
        runtime={'schema':'cochem-held-supervisor-runtime/1','status':'STARTING','pid':os.getpid(),
            'process_created_at_unix_seconds':process.create_time(),'instance_id':uuid.uuid4().hex,'sequence':0,
            'process_creation_filetime':win.process_creation_filetime(win._api()['kernel32'].GetCurrentProcess()),
            'observation_timing':{'count':0,'max_seconds':0.0,'over_one_second_count':0,'full_srs_acceptance':False},
            'provision_nonce':provision['nonce'],'configuration_sha256':provision['configuration_sha256'],
            'inputs_sha256':provision['inputs_sha256'],'helper_sha256':sha(stage['read'](Path(__file__))),
            'service_identity':'SYSTEM','paid_repair_enabled':False,'component_recovery_enabled':False,
            'warden_actuation_enabled':False,'full_srs_acceptance':False}
        signal.signal(signal.SIGINT,lambda *_:supervisor.stop_event.set())
        signal.signal(signal.SIGTERM,lambda *_:supervisor.stop_event.set())
        try:
            # Never call generic daemon recovery, route probing or paid preflight.
            while not supervisor.stop_event.is_set():
                publish_cycle(supervisor,runtime)
                supervisor.stop_event.wait(config['poll_seconds'])
        except Exception as error:
            runtime.update(status='HELD_OBSERVATION_FAILED',checked_at=time.time(),failure=safe_failure(error,'observation_cycle'))
            from cochem_supervisor.io import write_json
            write_json(CODE/'observation-runtime.json',runtime)
            raise
        finally: supervisor.runner.terminate()


def publish_cycle(supervisor,runtime):
    """Public receipt contains only bound process identity and fixed safety flags."""
    observed=supervisor.tick(ignore_startup_grace=True)
    if observed.get('health',{}).get('budget_authority_hold') is not True:
        raise ObservationHeld('OBSERVATION_NOT_HELD')
    duration=observed['health'].get('observation_seconds')
    if type(duration) not in (float,int) or not math.isfinite(duration) or not 0<=duration<=3600:
        raise ObservationHeld('OBSERVATION_DURATION_INVALID')
    aggregate=runtime['observation_timing']
    aggregate.update(count=aggregate['count']+1,max_seconds=max(aggregate['max_seconds'],duration),
        over_one_second_count=aggregate['over_one_second_count']+int(duration>1))
    from cochem_supervisor.io import write_json
    runtime.update(status='HELD_SUPERVISOR_OBSERVATION_RUNNING',sequence=runtime['sequence']+1,
        checked_at=time.time(),last_observation_seconds=duration,observed_controller_state=observed['health'].get('components',{}).get('controller',{}).get('state','unknown'))
    write_json(CODE/'observation-runtime.json',runtime)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group();group.add_argument('--prepare',action='store_true');group.add_argument('--observe',action='store_true')
    parser.add_argument('--nonce');parser.add_argument('--inputs-sha256');args=parser.parse_args()
    if args.prepare:
        value=prepare(args.nonce or '',args.inputs_sha256 or '')
        print(json.dumps({'status':value['status'],'phase':value['phase'],'daemon_running':False}))
        return 0 if value['status']=='HELD_OBSERVATION_PROVISIONED' else 2
    if args.observe: observe();return 0
    print(json.dumps({'status':'DRAFT_PREVIEW_NO_IO','code_root':str(CODE),'data_root':str(DATA),
        'runtime':str(STAGING),'warden_changes':False,'model_or_component_actuation':False,
        'future_mutations':['new identity only if missing; existing credentials preserved','fresh isolated private/workspace boundary and its Defender exclusion','exact held pair copy; source retained','new observation-only configuration/pointer/status/history','new independent observer task after verified provision'],
        'owner_command_ready':False}))
    return 0


if __name__=='__main__': raise SystemExit(main())
