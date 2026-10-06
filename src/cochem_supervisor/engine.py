"""Independent orchestration: evidence -> bounded repair -> external tests -> rollback."""
from __future__ import annotations
import hashlib
import json
import logging
from pathlib import Path
import threading
import time
import uuid

from . import __version__
from .io import write_json
from .monitor import read_observation,classify_error,redact_diagnostic
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

    def _stop(self):
        from .windows import stop_task
        stop_task(self.config['warden_task'],pointer_file=self.config['pointer_file'])
        return True

    def _clear_workspace(self):
        from cochem_pipeline.runtime import clear_slot
        clear_slot(Path(self.config['repair_workspace']))

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
        start_task(self.config['warden_task'])
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

    def _restart_once(self,incident):
        if not ('heartbeat' in incident['summary'].lower() or
                incident['evidence'].get('source')=='supervisor_status.json'):
            return False
        if self.ledger.has_event('WARDEN_RESTART_REQUESTED',fingerprint=incident['fingerprint']):
            return False
        self.ledger.record_event('WARDEN_RESTART_REQUESTED',{'reason':incident['summary']},
                                 fingerprint=incident['fingerprint'])
        self.stage='restarting'
        self._publish(incident=incident)
        self._stop()
        self._start(Path(self.releases.current()['source_root']))
        self._probe(Path(self.releases.current()['source_root']))
        return True

    def _version_checks(self):
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
        observation=read_observation(self.config['pipeline_private_root'],
            heartbeat_timeout=self.config['heartbeat_timeout'],stall_timeout=self.config['stall_timeout'],
            repeated_failures=self.config['repeated_failures'])
        self.stage='observing'
        self._publish(observation)
        incidents=list(observation['incidents'])
        if not ignore_startup_grace and time.time()-self.started_at<self.config['startup_grace_seconds']:
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
            if self._restart_once(incident):
                break
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
