"""Independent orchestration: evidence -> bounded repair -> external tests -> rollback."""
from __future__ import annotations
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from . import __version__
from .io import write_json
from .monitor import classify_error,redact_diagnostic
from .state import Ledger
from .releases import ReleaseError,ReleaseStore,snapshot_tree,tree_manifest,validate_changes


class CandidateRejected(RuntimeError):
    """A bounded repair may retry a rejected patch; it cannot bypass the gate."""
    category = 'code'


class Supervisor:
    def __init__(self,config:dict):
        from .windows import require_supervisor
        from .runner import RepairRunner
        from .probes import ControllerClient
        require_supervisor(config)
        self.config=config
        self.private=Path(config['private_root'])
        self.ledger=Ledger(self.private/'supervisor.db')
        self.releases=ReleaseStore(Path(config['release_root']),Path(config['pointer_file']),
                                   self.private/'release-journal.json')
        if not Path(config['pointer_file']).is_file():
            raise RuntimeError('The reviewed initial release must be bootstrapped by the installer')
        self.runner=RepairRunner(config)
        self.client=ControllerClient(config['pipeline_port'],config['pipeline_token_file'])
        self.stop_event=threading.Event()
        self.started_at=time.time()
        self.start_requested_at=0.0
        self.stage='starting'
        self.current_attempt=None
        self.smoke_report=None
        self.smoke_candidate=None
        self.acceptance_manifest=tree_manifest(Path(config['acceptance_root']))

    def _publish(self,observation=None,**details):
        write_json(self.private/'supervisor-status.json',{
            'version':__version__,'timestamp':time.time(),'stage':self.stage,
            'attempt_id':self.current_attempt,'observation':observation,**details})

    def _heartbeat(self):
        if self.stop_event.is_set():
            return False
        if self.current_attempt is None:
            return True
        return self.ledger.heartbeat(self.current_attempt,lease_seconds=180)

    def _read_observation(self):
        """Run the sterile detector separately from the privileged actuator.

        The protected detector never imports pipeline/MCP application modules.
        Temporary handles bound output readback; a timeout terminates this one
        fixed child, whose implementation never launches subprocesses.
        """
        import psutil
        package=Path(__file__).resolve().parent
        command=[sys.executable,'-I','-S',str(package/'detector_bootstrap.py'),
            '--supervisor-package',str(package),'--psutil-package',str(Path(psutil.__file__).resolve().parent),
            '--private-root',self.config['pipeline_private_root'],
            '--heartbeat-timeout',str(self.config['heartbeat_timeout']),
            '--stall-timeout',str(self.config['stall_timeout']),
            '--repeated-failures',str(self.config['repeated_failures']),
            '--wal-limit-mb',str(self.config.get('wal_limit_mb',256)),
            '--process-history',str(self.private/'process-history.json')]
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as error:
            completed=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=output,stderr=error,
                timeout=5,creationflags=0x08000000 if os.name=='nt' else 0)
            if completed.returncode or output.tell()>2*1024*1024:
                raise RuntimeError('Independent sterile detector did not produce bounded observation evidence')
            output.seek(0)
            value=json.loads(output.read(2*1024*1024))
        if (not isinstance(value,dict) or value.get('import_audit',{}).get('sterile') is not True
                or value['import_audit'].get('isolated') is not True or value['import_audit'].get('site_disabled') is not True
                or value['import_audit'].get('forbidden_modules') or not isinstance(value.get('observation'),dict)):
            raise RuntimeError('Independent detector import boundary was not verified')
        return value['observation']

    def _stop(self):
        from .windows import stop_task
        from .cleanup import reconcile_stopped_containment
        receipt=stop_task(self.config['warden_task'],pointer_file=self.config['pointer_file'])
        cleared=reconcile_stopped_containment(Path(self.config['pipeline_private_root'])/'job_board.db',receipt)
        if cleared:
            self.ledger.record_event('WARDEN_EXECUTION_CLEANUP_VERIFIED',{'cleared_guards':cleared})
        return True

    def _clear_workspace(self):
        from .workspace import clear_workspace
        clear_workspace(Path(self.config['repair_workspace']))

    def _prepare_workspace(self,candidate):
        from .windows import prepare_repair_workspace
        prepare_repair_workspace(candidate,self.config['repair_worker']['name'])

    def _protect_release_tree(self,path):
        from .windows import protect_release_tree
        protect_release_tree(path)

    def _quarantine_hold(self):
        identities=sorted(self.runner.quarantined_identities)
        marker=self.private/'repair-quarantine.json'
        if identities:
            write_json(marker,{'identities':identities,'timestamp':time.time(),
                'reason':'Repair process tree or profile cleanup was not verified'})
        if identities or marker.exists():
            self.stage='quarantined'
            self._publish(quarantined_identities=identities,
                          reason='Administrator must verify cleanup before clearing the quarantine marker')
            return True
        return False

    def _start(self,source):
        from .windows import start_task
        self.start_requested_at=time.time()
        start_task(self.config['warden_task'],pointer_file=self.config['pointer_file'])
        return True

    def _probe(self,source):
        from .probes import wait_for_health,run_smoke_workflow
        healthy=wait_for_health(Path(self.config['pipeline_private_root']),self.client,Path(source),
                                self.start_requested_at,timeout_seconds=self.config['startup_grace_seconds'],
                                heartbeat=self._heartbeat)
        if not healthy:
            return False
        # Rollback checks the previous service's actual heartbeat and API. Only
        # the candidate also spends quota on the bounded two-chapter workflow.
        if self.smoke_candidate is not None and Path(source)==self.smoke_candidate:
            self.smoke_report=run_smoke_workflow(self.config,self.client,
                self.private/'attempts'/self.current_attempt/'live-smoke.json',self._heartbeat)
            return self.smoke_report.get('passed') is True
        return True

    def recover(self):
        self.stage='recovering'
        self.smoke_candidate=None
        result=self.releases.recover(self._start,self._stop,self._probe)
        self.releases.current()  # Verify only after crash recovery can restore a bad candidate.
        self.ledger.recover_expired()
        if result is not None:
            self.ledger.record_event('DEPLOYMENT_RECOVERED',result)
        self._publish(recovery=result)
        return result

    def _capture_recovery_diagnostics(self,incident,observation=None):
        """Persist bounded private evidence before changing component state."""
        if observation is None:
            observation=self._read_observation()
        directory=self.private/'diagnostics'
        directory.mkdir(parents=True,exist_ok=True)
        target=directory/(uuid.uuid4().hex+'.json')
        from .diagnostics import capture_bundle
        bundle=capture_bundle(Path(self.config['pipeline_private_root']),target.with_suffix(''),observation)
        write_json(target,{'captured_at':time.time(),'incident':incident,'observation':observation,
                           'scope':'private SQLite backup plus bounded heartbeat, metadata, hardware and infrastructure diagnostics',
                           'bundle':bundle})
        digest=hashlib.sha256(target.read_bytes()).hexdigest()
        self.ledger.record_event('PRE_RECOVERY_DIAGNOSTICS',{'path':str(target),'sha256':digest},
                                 fingerprint=incident['fingerprint'])
        return {'path':str(target),'sha256':digest,'bundle':bundle}

    def _restart_once(self,incident,observation=None):
        if not ('heartbeat' in incident['summary'].lower() or
                'stalled beyond' in incident['summary'].lower() or
                incident['evidence'].get('source')=='supervisor_status.json' or
                incident['evidence'].get('component')=='controller'):
            return False
        if self.ledger.has_event('WARDEN_RESTART_REQUESTED',fingerprint=incident['fingerprint']):
            return False
        diagnostic=self._capture_recovery_diagnostics(incident,observation)
        self.ledger.record_event('WARDEN_RESTART_REQUESTED',{'reason':incident['summary'],'diagnostic':diagnostic},
                                 fingerprint=incident['fingerprint'])
        self.stage='restarting'
        self._publish(incident=incident)
        try:
            self._recovery_action_started('warden',incident['fingerprint'])
            self._stop()
            self._start(Path(self.releases.current()['source_root']))
            verified=bool(self._probe(Path(self.releases.current()['source_root'])))
        except Exception as exc:
            self.ledger.record_event('WARDEN_RESTART_FAILED',{'health_verified':False,
                'diagnostic':redact_diagnostic(f'{type(exc).__name__}: {exc}')},fingerprint=incident['fingerprint'])
            raise
        self.ledger.record_event('WARDEN_RESTART_FINISHED',{'health_verified':verified},fingerprint=incident['fingerprint'])
        if not verified:
            self.ledger.record_event('WARDEN_RESTART_FAILED',{'health_verified':False},fingerprint=incident['fingerprint'])
        return True

    def _recovery_action_started(self,component,fingerprint):
        started=time.time()
        confirmed=getattr(self,'recovery_confirmed_at',started)
        self.ledger.record_event('RECOVERY_ACTION_STARTED',{'component':component,
            'confirmed_at':confirmed,'started_at':started,'elapsed_seconds':max(0,started-confirmed)},
            fingerprint=fingerprint)

    def _observe_controller(self,observation):
        # Test drivers that explicitly do not provision an HTTP controller have
        # no client. Production construction always supplies the real client.
        if not hasattr(self,'client'):
            return
        from .monitor import _incident
        try:
            status=self.client.call('/health')
            if status.get('service_identity')!='SYSTEM' or type(status.get('pid')) is not int or status['pid']<=0:
                raise ValueError('Controller health did not attest its native service identity')
            if (observation['health'].get('heartbeat')=='fresh' and
                    (status['pid']!=observation['health'].get('heartbeat_pid') or
                     status.get('instance_id')!=observation['health'].get('heartbeat_instance_id'))):
                raise ValueError('Controller health does not match the current heartbeat process')
            component={'state':'healthy','required':True,'checked_at':time.time()}
        except Exception as exc:
            diagnostic=redact_diagnostic(f'{type(exc).__name__}: {exc}',256)
            category=classify_error(diagnostic)['category']
            blocked_auth=category=='auth'
            component={'state':'unavailable','required':True,'checked_at':time.time(),'diagnostic':diagnostic}
            observation['incidents'].append(_incident('auth' if blocked_auth else 'configuration',False,
                'The authenticated controller health endpoint is unavailable',
                {'component':'controller_auth' if blocked_auth else 'controller','diagnostic':diagnostic},
                'controller authentication blocked' if blocked_auth else 'controller health unavailable'))
            observation['health']['repair_hold']=True
            observation['health']['state']='blocked'
        observation['health'].setdefault('components',{})['controller']=component

    def _recover_components(self,observation):
        """Recover only the diagnosed component after durable strikes/backoff."""
        from .component_recovery import RecoveryLedger,start_docker_service
        ledger=RecoveryLedger(self.private/'component-recovery.db')
        incidents=observation['incidents']
        docker=next((item for item in incidents if item['evidence'].get('component')=='docker_engine'),None)
        warden=next((item for item in incidents if 'heartbeat' in item['summary'].lower()
            or 'stalled beyond' in item['summary'].lower() or item['evidence'].get('component')=='controller'),None)
        delayed=False
        for component,incident in (('docker_engine',docker),('warden',warden)):
            if component=='warden' and docker is not None:
                continue  # Recover the failed engine before touching its dependent warden.
            decision=ledger.observe(component,incident is None)
            if incident is None:
                continue
            self.ledger.observe(incident['fingerprint'],incident['category'],incident)
            if decision['state']=='waiting':
                delayed=True
                continue
            if decision['state']=='exhausted':
                if not self.ledger.has_event('COMPONENT_RECOVERY_EXHAUSTED',fingerprint=incident['fingerprint']):
                    self.ledger.record_event('COMPONENT_RECOVERY_EXHAUSTED',decision,fingerprint=incident['fingerprint'])
                if component=='warden' and self.ledger.has_event('WARDEN_RESTART_FAILED',fingerprint=incident['fingerprint']):
                    observation['health']['repair_hold']=True
                    observation['health']['state']='blocked'
                continue
            # A reservation survives a crash during diagnostics or the action.
            # No subsequent tick can repeat the physical recovery automatically.
            try:
                self.recovery_confirmed_at=time.time()
                if component=='docker_engine':
                    diagnostic=self._capture_recovery_diagnostics(incident,observation)
                    self.ledger.record_event('COMPONENT_RECOVERY_REQUESTED',
                        {**decision,'diagnostic':diagnostic},fingerprint=incident['fingerprint'])
                    self._recovery_action_started(component,incident['fingerprint'])
                    outcome=start_docker_service()
                else:
                    outcome={'restart_requested':self._restart_once(incident,observation)}
                ledger.finish(component,{'action_completed':True,**outcome})
                self.ledger.record_event('COMPONENT_RECOVERY_FINISHED',{'component':component,**outcome},
                                         fingerprint=incident['fingerprint'])
            except Exception as exc:
                result={'action_completed':False,'component':component,
                        'diagnostic':redact_diagnostic(f'{type(exc).__name__}: {exc}')}
                ledger.finish(component,result)
                self.ledger.record_event('COMPONENT_RECOVERY_BLOCKED',result,fingerprint=incident['fingerprint'])
            return True
        return delayed

    @staticmethod
    def _code_evidence(incident):
        """An unavailable heartbeat is not evidence that changing code will help."""
        evidence=incident.get('evidence',{})
        if (incident.get('category')=='code' and evidence.get('component')=='database_structure'
                and evidence.get('invariant') in {'inactive_lease_owner','pending_exhausted_failure_budget'}):
            return type(evidence.get('count')) is int and evidence['count']>0
        diagnostic=evidence.get('diagnostic')
        if incident.get('category') not in {'code','compatibility'} or not isinstance(diagnostic,str):
            return False
        if not classify_error(diagnostic)['repairable']:
            return False
        if evidence.get('source')=='crash-envelope.json':
            return bool(evidence.get('frames'))
        return (type(evidence.get('required_repetitions')) is int and
                max(evidence.get('observed_jobs',0),evidence.get('attempts',0))>=evidence['required_repetitions'])

    def _prepare_structural_repair(self,observation):
        """Freeze a collapsed queue before considering its evidenced code repair."""
        incident=next(item for item in observation['incidents']
                      if item['evidence'].get('component')=='database_structure')
        self.ledger.observe(incident['fingerprint'],incident['category'],incident)
        self.stage='collapsed'
        self.recovery_confirmed_at=time.time()
        self._publish(observation)
        # Persistent corruption can survive many held ticks. Preserve its first
        # physical snapshot rather than copying up to 32 MiB every poll.
        if not self.ledger.has_event('PRE_RECOVERY_DIAGNOSTICS',fingerprint=incident['fingerprint']):
            self._capture_recovery_diagnostics(incident,observation)
        try:
            self._recovery_action_started('structural_freeze',incident['fingerprint'])
            if self._stop() is not True:
                raise RuntimeError('Collapsed queue containment was not verified')
        except Exception as exc:
            self.ledger.record_event('STRUCTURAL_FREEZE_BLOCKED',{
                'diagnostic':redact_diagnostic(f'{type(exc).__name__}: {exc}')},fingerprint=incident['fingerprint'])
            return False
        self.ledger.record_event('STRUCTURAL_FREEZE_VERIFIED',{
            'invariant':incident['evidence']['invariant'],'count':incident['evidence']['count']},
            fingerprint=incident['fingerprint'])
        refreshed=self._read_observation()
        self._observe_controller(refreshed)
        health=refreshed['health']
        blockers=(health.get('database')!='readable' or health.get('execution_cleanup_hold')
            or health.get('hardware_capacity')==0 or health.get('hardware',{}).get('state') in {'paused','critical'}
            or any(name!='controller' and part.get('required') and part.get('state')!='healthy'
                   for name,part in health.get('components',{}).items())
            or any(item['category'] not in {'code','compatibility'} and item['evidence'].get('component')!='controller'
                   for item in refreshed['incidents']))
        repairable=[item for item in refreshed['incidents'] if
                    item['evidence'].get('component')=='database_structure' and self._code_evidence(item)]
        observation.clear();observation.update(refreshed)
        if blockers or not repairable:
            observation['health'].update(repair_hold=True,state='collapsed')
            return False
        for item in refreshed['incidents']:
            item['repairable']=item in repairable
        refreshed['health'].update(repair_hold=False,state='collapsed',structural_freeze_verified=True)
        return True

    def _escalate_failed_restart(self,observation):
        """Allow evidenced source repair after a failed restart and verified stop.

        Availability alone, auth, quota and resource failures remain holds. The
        controller can be unavailable because the very implementation being
        repaired crashes on startup; it is not required to attest its own fix.
        """
        incidents=observation['incidents']
        failed=next((item for item in incidents if self.ledger.has_event(
            'WARDEN_RESTART_FAILED',fingerprint=item['fingerprint'])),None)
        evidenced=[item for item in incidents if self._code_evidence(item)]
        if failed is None or not evidenced:
            return False

        def external_hold(value, *, before_stop=False):
            health=value['health']
            startup_crash=any(self._code_evidence(item) and item['evidence'].get('source')=='crash-envelope.json'
                              for item in value['incidents'])
            if ((health.get('database')!='readable' and not (startup_crash and health.get('database')=='missing'))
                    or health.get('hardware_capacity')==0):
                return True
            if health.get('hardware',{}).get('state') in {'paused','critical'}:
                return True
            if health.get('execution_cleanup_hold') and not before_stop:
                return True
            if any(name!='controller' and part.get('required') and part.get('state')!='healthy'
                   for name,part in health.get('components',{}).items()):
                return True
            for item in value['incidents']:
                evidence=item['evidence']
                if evidence.get('component')=='controller':
                    continue
                if startup_crash and health.get('database')=='missing' and evidence.get('source')=='job_board.db':
                    continue
                if before_stop and evidence.get('state')=='BLOCKED' and 'sampled_holds' in evidence:
                    continue
                if item['category'] not in {'code','compatibility'}:
                    return True
            return False

        if external_hold(observation,before_stop=True):
            return False
        self._capture_recovery_diagnostics(failed,observation)
        try:
            if self._stop() is not True:
                raise RuntimeError('Contained Warden shutdown was not verified')
        except Exception as exc:
            self.ledger.record_event('REPAIR_CLEANUP_BLOCKED',{
                'diagnostic':redact_diagnostic(f'{type(exc).__name__}: {exc}')},fingerprint=failed['fingerprint'])
            return False
        refreshed=self._read_observation()
        self._observe_controller(refreshed)
        if external_hold(refreshed):
            return False
        repairable=[item for item in refreshed['incidents'] if self._code_evidence(item)]
        if not repairable:
            return False
        self.ledger.record_event('WARDEN_REPAIR_CLEANUP_VERIFIED',{
            'code_incidents':[item['fingerprint'] for item in repairable],
            'proof':'verified contained tree stop and read-only cleanup recheck'},fingerprint=failed['fingerprint'])
        # Preserve observed availability failures for diagnosis, but dispatch
        # only the implementation evidence through the ordinary durable budget.
        for item in refreshed['incidents']:
            item['repairable']=item in repairable
        refreshed['health'].update(repair_hold=False,state='degraded')
        observation.clear(); observation.update(refreshed)
        return True

    def _repair_boundary_check(self):
        from .windows import verify_repair_docker_boundary, verify_repair_execution_limits
        return {'resources':verify_repair_execution_limits(self.config),
                'docker':verify_repair_docker_boundary(self.config)}

    def _outer_acceptance(self,candidate,directory):
        from .blackbox import run_blackbox
        def invoke(arguments,request,logs,timeout):
            return self.runner.run_process(self.config['repair_worker'],
                [self.config['test_python'],'-I','-m','cochem_supervisor.blackbox_child',*arguments],
                Path(self.config['repair_workspace']),logs,stdin_text=json.dumps(request),
                timeout_seconds=timeout,heartbeat=self._heartbeat)
        return run_blackbox(candidate,Path(self.config['repair_workspace'])/'outer-contract',
            directory/'outer-acceptance.json',invoke,timeout_seconds=self.config['test_timeout_seconds'])

    def _repair_boundary_hold(self):
        try:
            self._repair_boundary_check()
        except Exception as exc:
            details={'category':'configuration','repairable':False,
                     'diagnostic':redact_diagnostic(f'{type(exc).__name__}: {exc}')}
            self.ledger.record_event('REPAIR_ISOLATION_BLOCKED',details)
            self.stage='blocked'
            self._publish(reason='Repair-account execution isolation or resource readiness is not verified',failure=details)
            return True
        return False

    def _version_checks(self):
        if self._repair_boundary_hold():
            return []
        from cochem_mcp.providers import executable_prefix
        record_file=self.private/'cli-contracts.json'
        previous=json.loads(record_file.read_text(encoding='utf-8')) if record_file.exists() else {}
        if time.time()-previous.get('checked_at',0)<self.config['version_probe_seconds']:
            return previous.get('incidents',[])
        results={}
        incidents=[]
        for spec in self.config['providers']:
            provider=spec['provider']
            try:
                prefix=executable_prefix(provider,spec['executable'])
                folder=self.private/'version-probes'/uuid.uuid4().hex
                receipt=self.runner.run_process(self.config['repair_worker'],prefix+['--version'],
                    Path(self.config['repair_workspace']),folder,timeout_seconds=30,heartbeat=self._heartbeat)
                version=redact_diagnostic(Path(receipt['stdout_path']).read_text(encoding='utf-8',errors='replace'),200)
                help_args=['exec','--help'] if provider=='codex' else ['--help']
                help_receipt=self.runner.run_process(self.config['repair_worker'],prefix+help_args,
                    Path(self.config['repair_workspace']),self.private/'version-probes'/uuid.uuid4().hex,
                    timeout_seconds=30,heartbeat=self._heartbeat)
                help_text=Path(help_receipt['stdout_path']).read_text(encoding='utf-8',errors='replace')
                flags=(['--json','--model','--ignore-user-config','--ephemeral'] if provider=='codex' else
                       ['--print','--output-format','--permission-mode','--setting-sources',
                        '--strict-mcp-config','--no-session-persistence'])
                missing=[flag for flag in flags if flag not in help_text]
                results[provider]={'available':True,'version':version,'missing_flags':missing,
                                   'exit_code':receipt['exit_code'],'help_exit_code':help_receipt['exit_code']}
                if receipt['exit_code'] or help_receipt['exit_code'] or missing:
                    fingerprint=hashlib.sha256(json.dumps([provider,'repair-CLI-contract',missing],sort_keys=True).encode()).hexdigest()
                    incidents.append({'fingerprint':fingerprint,'category':'configuration','repairable':False,
                        'summary':'Native repair CLI requires an administrator compatibility update',
                        'evidence':{'provider':provider,**results[provider],'latest_failure_at':time.time()}})
            except Exception as exc:
                results[provider]={'available':False,'diagnostic':redact_diagnostic(f'{type(exc).__name__}: {exc}')}
                self.ledger.record_event('CLI_PROBE_BLOCKED',{'provider':provider,**results[provider]})
                if self._quarantine_hold() or self.stop_event.is_set():
                    break
        write_json(record_file,{'checked_at':time.time(),'providers':results,'incidents':incidents})
        if results!=previous.get('providers'):
            self.ledger.record_event('CLI_CONTRACTS_CHECKED',{'providers':results})
        return incidents

    def _eligible_providers(self):
        contracts=self.private/'cli-contracts.json'
        observed=json.loads(contracts.read_text(encoding='utf-8')).get('providers',{}) if contracts.exists() else {}
        return [spec for spec in self.config['providers']
                if observed.get(spec['provider'],{}).get('available') is True
                and not observed[spec['provider']].get('missing_flags')
                and observed[spec['provider']].get('exit_code')==0
                and observed[spec['provider']].get('help_exit_code')==0]

    def _previous_failure(self,fingerprint):
        for event in reversed(self.ledger.history(fingerprint)):
            if event['event'] in ('ATTEMPT_FAILED','ATTEMPT_ROLLED_BACK'):
                details=event['details']
                return {key:details[key] for key in ('failure','test_diagnostic','live_smoke') if key in details}
        return None

    def _repair(self,incident):
        from .acceptance import validate_junit
        if self.stop_event.is_set() or self._quarantine_hold():
            return False
        if self.releases.recovery_required():
            self.recover()
            return False
        if self._repair_boundary_hold():
            return False
        eligible=self._eligible_providers()
        if not eligible:
            self.stage='blocked'
            self._publish(reason='No compatible repair CLI is available; inspect cli-contracts.json')
            return False
        attempt=self.ledger.reserve(incident['fingerprint'],self.config['max_per_incident'],
            self.config['max_per_day'],self.config['cooldown_seconds'],lease_seconds=180)
        if attempt is None:
            return False
        self.current_attempt=attempt['attempt_id']
        self.smoke_report=None
        directory=self.private/'attempts'/self.current_attempt
        details={'incident':incident,'deployed':False}
        terminal='FAILED'
        try:
            directory.mkdir(parents=True,exist_ok=False)
            details['diagnostics']=self._capture_recovery_diagnostics(incident)
            self.stage='repairing'
            self._publish(incident=incident)
            self._clear_workspace()
            current=self.releases.current()
            candidate=Path(self.config['repair_workspace'])/'source'
            baseline=snapshot_tree(Path(current['source_root']),candidate)
            self._prepare_workspace(candidate)
            ordinal=attempt['incident']['attempts']
            spec=eligible[(ordinal-1)%len(eligible)]
            evidence={'objective':incident.get('objective','Repair the independently observed pipeline defect. '
                'Preserve existing behavior and authentication boundaries. Do not modify the supervisor, tests, '
                'dependencies, configuration, credentials, database schemas or deployment tooling. '
                'All changes must preserve compatibility with existing stored workflow data.'),
                'allowed_paths':self.config['allowed_paths'],'incident':incident,
                'previous_failure':self._previous_failure(incident['fingerprint'])}
            bundle=details['diagnostics']['bundle']
            # Inference receives immutable artifact metadata, never the raw
            # private snapshot, workflow payloads or a private storage path.
            evidence['diagnostic_bundle']={key:bundle[key] for key in
                ('schema','captured_at','database','private_database_snapshot','files','manifest_sha256')}
            details['native_receipt']=self.runner.run(spec,self.config['repair_worker'],candidate,evidence,
                directory/'model',self.config['repair_timeout_seconds'],self._heartbeat)
            try:
                changed=validate_changes(baseline,candidate,self.config['allowed_paths'])
            except (ValueError,ReleaseError) as exc:
                raise CandidateRejected(str(exc)) from exc
            frozen=Path(self.config['release_root'])/('.validation-'+self.current_attempt)
            snapshot_tree(candidate,frozen)
            self._protect_release_tree(frozen)
            if tree_manifest(frozen)!=changed['manifest']:
                raise CandidateRejected('Candidate changed while creating its protected validation snapshot')
            self.stage='testing'
            self._publish(changed=changed['changed'])
            report=Path(self.config['repair_workspace'])/'acceptance.xml'
            scratch=Path(self.config['repair_workspace'])/'acceptance-tmp'
            argv=[self.config['test_python'],'-I','-m','cochem_supervisor.acceptance',
                  '--candidate',str(frozen),'--tests',self.config['acceptance_root'],
                  '--report',str(report),'--scratch',str(scratch),'--targets',*self.config['test_targets']]
            process=self.runner.run_process(self.config['repair_worker'],argv,Path(self.config['repair_workspace']),
                directory/'acceptance',timeout_seconds=self.config['test_timeout_seconds'],heartbeat=self._heartbeat)
            if process['exit_code']!=0:
                details['test_diagnostic']={key:redact_diagnostic(Path(process[key]).read_text(
                    encoding='utf-8',errors='replace')[-8000:],4000) for key in ('stdout_path','stderr_path')}
                raise CandidateRejected('Independent acceptance process failed; protected logs retained')
            try:
                details['tests']=validate_junit(report,self.config['minimum_passed_tests'],self.config['maximum_skipped_tests'])
            except ValueError as exc:
                raise CandidateRejected(f'Independent acceptance report rejected: {exc}') from exc
            if tree_manifest(Path(self.config['acceptance_root']))!=self.acceptance_manifest:
                raise CandidateRejected('Independent acceptance tests changed during validation')
            if tree_manifest(frozen)!=changed['manifest']:
                raise CandidateRejected('Candidate changed during acceptance tests')
            # Candidate imports share the pytest interpreter and can forge its
            # XML. Regression results remain useful but cannot attest themselves.
            # Promotion additionally requires protected out-of-process assertions.
            details['tests']['scope']='in-process regression diagnostics; not tamperproof attestation'
            try:
                details['outer_acceptance']=self._outer_acceptance(frozen,directory)
            except ValueError as exc:
                raise CandidateRejected(str(exc)) from exc
            if details['outer_acceptance'].get('passed') is not True:
                raise CandidateRejected('Independent outer acceptance did not pass')
            if tree_manifest(frozen)!=changed['manifest']:
                raise CandidateRejected('Candidate changed during outer acceptance')
            if not self._heartbeat():
                raise RuntimeError('Repair lease was lost before deployment')
            release=self.releases.prepare(frozen,changed['manifest'],self._protect_release_tree)
            details.update(changes=changed['changed'],release=release)
            if not self.config['auto_deploy']:
                terminal='BLOCKED'
                details['reason']='Candidate validated; automatic deployment is disabled by policy'
            else:
                if not self._heartbeat():
                    raise RuntimeError('Repair lease was lost before activation')
                self.stage='deploying'
                self._publish(release=release)
                self.smoke_candidate=Path(release['source_root'])
                transaction=self.releases.deploy(release,self._start,self._stop,self._probe)
                details.update(deployment=transaction,live_smoke=self.smoke_report,
                               deployed=transaction['state']=='COMMITTED')
                terminal='SUCCEEDED' if details['deployed'] else 'ROLLED_BACK'
                if (terminal=='ROLLED_BACK' and self.smoke_report and
                    self.smoke_report.get('failure_category') in ('auth','quota','provider','resource','configuration')):
                    terminal='BLOCKED'
        except Exception as exc:
            classification=classify_error(f'{type(exc).__name__}: {exc}')
            if getattr(exc,'category',None) in ('code','compatibility','auth','quota','provider','resource','configuration'):
                classification['category']=exc.category
                classification['repairable']=exc.category in ('code','compatibility')
            details['failure']={'type':type(exc).__name__,**classification}
            if classification['category'] in ('auth','quota','provider','resource','configuration'):
                terminal='BLOCKED'
            self.ledger.record_event('REPAIR_EXCEPTION',details['failure'],attempt_id=self.current_attempt,
                                     fingerprint=incident['fingerprint'])
        finally:
            try:
                if directory.is_dir():
                    write_json(directory/'supervisor-receipt.json',details)
                self.ledger.finish(self.current_attempt,terminal,details)
            finally:
                self.current_attempt=None
                self.smoke_candidate=None
                self.stage='observing'
                self._publish(last_result=terminal,quarantined_identities=sorted(self.runner.quarantined_identities))
                self._quarantine_hold()
        return True

    def tick(self,*,ignore_startup_grace=False):
        self.ledger.recover_expired()
        if self._quarantine_hold():
            return {'health':{'state':'blocked','reason':'repair identity quarantined'},'incidents':[]}
        if self.releases.recovery_required():
            self.recover()
        observation_started=time.monotonic()
        observed_at=time.time()
        observation=self._read_observation()
        self._observe_controller(observation)
        duration=time.monotonic()-observation_started
        observation['health']['observation_seconds']=duration
        self.ledger.record_event('SUPERVISOR_OBSERVATION_TIMED',{'observed_at':observed_at,'duration_seconds':duration})
        self.stage='observing'
        self._publish(observation)
        if not ignore_startup_grace and time.time()-self.started_at<self.config['startup_grace_seconds']:
            return observation
        if observation['health'].get('structural_integrity',{}).get('state')=='violated':
            if not self._prepare_structural_repair(observation):
                return observation
        elif self._recover_components(observation):
            return observation
        if observation['health'].get('repair_hold') and not self._escalate_failed_restart(observation):
            return observation
        incidents=list(observation['incidents'])
        # A missing/stale heartbeat or an expired lease is an availability
        # symptom. Without a diagnosed exception it never authorizes inference.
        for item in incidents:
            if item['repairable'] and item['category'] in {'code','compatibility'} and not self._code_evidence(item):
                item['repairable']=False
        if self._repair_boundary_hold():
            observation['health']['repair_hold']=True
            observation['health']['state']='blocked'
            self._publish(observation)
            return observation
        try:
            incidents.extend(self._version_checks())
        except Exception as exc:
            classification=classify_error(f'{type(exc).__name__}: {exc}')
            self.ledger.record_event('CLI_PROBE_BLOCKED',classification)
            return observation
        if self._quarantine_hold() or self.stop_event.is_set():
            return observation
        # Explicit operator update requests enter the same durable limits.
        for item in self.ledger.list_incidents():
            if item['category']=='update' and item['status']=='OPEN':
                incidents.append(item['evidence'])
        for incident in incidents:
            try:
                previous=self.ledger.get_incident(incident['fingerprint'])
            except (KeyError,ValueError):
                previous=None
            if previous and previous['status']=='RESOLVED':
                evidence=incident['evidence']
                changed_at=evidence.get('latest_failure_at',evidence.get('last_progress_at',time.time()))
                if changed_at is not None and changed_at<=previous.get('resolved_at',0):
                    continue
            self.ledger.observe(incident['fingerprint'],incident['category'],incident)
            if not incident['repairable'] or (previous and previous['status'] in ('BLOCKED','EXHAUSTED','REPAIRING')):
                continue
            if self._repair(incident):
                break
        return observation

    def run(self):
        self.recover()
        try:
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    logging.exception('Supervisor cycle failed; new repairs are held until next verified cycle')
                    self.stage='blocked'
                    self._publish(failure=classify_error(f'{type(exc).__name__}: {exc}'))
                self.stop_event.wait(self.config['poll_seconds'])
        finally:
            self.runner.terminate()
            self.stage='stopped'
            self._publish()
