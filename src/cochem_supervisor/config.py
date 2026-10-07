"""Explicit, protected supervisor policy; no model can change its own repair limits."""
from __future__ import annotations
import json
from copy import deepcopy
from pathlib import Path
import re
from cochem_pipeline.resource_limits import ResourceLimits

DEFAULT_ALLOWED = ['src/cochem_pipeline/','src/cochem_mcp/','src/cochem/warden/ladder.py']
PATH_FIELDS = ('private_root','repair_workspace','release_root','baseline_source','acceptance_root',
               'pipeline_python','pipeline_config','pointer_file','test_python')
DEFAULTS = {'poll_seconds':10,'startup_grace_seconds':120,'heartbeat_timeout':30,'stall_timeout':600,
            'repair_execution_limits':ResourceLimits().as_dict(),
            'repeated_failures':3,'max_per_incident':2,'max_per_day':4,'cooldown_seconds':1800,
            'repair_timeout_seconds':900,'test_timeout_seconds':600,'smoke_timeout_seconds':600,
            'version_probe_seconds':3600,'max_log_bytes':16777216,'minimum_passed_tests':200,
            'maximum_skipped_tests':64,'auto_deploy':True,'warden_task':'CoChem-4.2.2-Warden',
            'supervisor_task':'CoChem-4.2.3-Supervisor','allowed_paths':DEFAULT_ALLOWED,
            'test_targets':['pipeline_tests/test_config.py','pipeline_tests/test_hardware_guard.py',
                            'pipeline_tests/test_resource_telemetry.py','pipeline_tests/test_resource_limits.py',
                            'pipeline_tests/test_ramdisk.py','pipeline_tests/test_deployment.py',
                            'pipeline_tests/test_container_policy.py','pipeline_tests/test_containers.py',
                            'pipeline_tests/test_coding_git.py','pipeline_tests/test_git_deadline.py',
                            'pipeline_tests/test_coding_plan.py',
                            'pipeline_tests/test_coding_workflow.py','pipeline_tests/test_coding_boundaries.py',
                            'pipeline_tests/test_coding_acceptance.py','pipeline_tests/test_diagnostics.py',
                            'pipeline_tests/test_coding_reconciliation.py','pipeline_tests/test_operator_views.py',
                            'pipeline_tests/test_document_governance.py',
                            'pipeline_tests/test_operations_policy.py','pipeline_tests/test_runtime_knowledge_authority.py',
                            'pipeline_tests/test_upgrade_preview.py','pipeline_tests/test_deployment_revision.py',
                            'pipeline_tests/test_coding_srs_gates.py','pipeline_tests/test_knowledge.py',
                            'pipeline_tests/test_prompt_transport.py','pipeline_tests/test_crash_envelope.py',
                            'pipeline_tests/test_native_output_bounds.py','pipeline_tests/test_controller_guard.py',
                            'pipeline_tests/test_lease_invariants.py','pipeline_tests/test_planning_governance.py',
                            'pipeline_tests/test_execution_integration.py','pipeline_tests/test_runtime_storage_holds.py',
                            'pipeline_tests/test_inference_policy.py','pipeline_tests/test_worker_inference_policy.py',
                            'pipeline_tests/test_native_effort.py',
                            'pipeline_tests/test_oracle.py','pipeline_tests/test_recovery_telemetry.py',
                            'pipeline_tests/test_runtime_monitor.py','pipeline_tests/test_service.py',
                            'pipeline_tests/test_routing_policy.py','pipeline_tests/test_routing_store.py',
                            'pipeline_tests/test_routing_integration.py','pipeline_tests/test_runtime_routing.py',
                            'pipeline_tests/test_provider_failures.py',
                            'pipeline_tests/test_store.py','pipeline_tests/test_windows_contract.py',
                            'pipeline_tests/test_worker_contract.py','mcp_tests/test_config.py',
                            'mcp_tests/test_providers.py',
                            'mcp_tests/test_jobs.py','mcp_tests/test_server.py']}


def load_config(filename: str | Path) -> dict:
    raw=json.loads(Path(filename).read_text(encoding='utf-8-sig'))
    if not isinstance(raw,dict):
        raise ValueError('Supervisor configuration must be an object')
    result={**deepcopy(DEFAULTS),**raw}
    result['repair_execution_limits']=ResourceLimits.from_dict(result['repair_execution_limits']).as_dict()
    for key in PATH_FIELDS:
        value=result.get(key)
        if not isinstance(value,str) or '\x00' in value or not Path(value).is_absolute():
            raise ValueError(f'{key} must be an absolute native path')
        result[key]=str(Path(value).resolve())
    roots=[Path(result[key]) for key in ('private_root','repair_workspace','release_root','acceptance_root')]
    for index,left in enumerate(roots):
        if any(left==right or left in right.parents or right in left.parents for right in roots[index+1:]):
            raise ValueError('Private state, repair workspace, releases and acceptance roots must be disjoint')
    private=Path(result['private_root'])
    if private not in Path(result['pointer_file']).parents:
        raise ValueError('Release pointer must be inside protected supervisor private state')
    for key,value in DEFAULTS.items():
        if type(value) is int:
            supplied=result[key]
            low=1024 if key=='max_log_bytes' else 0 if key=='maximum_skipped_tests' else 1800 if key=='cooldown_seconds' else 1
            high=(67108864 if key=='max_log_bytes' else
                  2 if key=='max_per_incident' else 4 if key=='max_per_day' else
                  86400 if key in ('repair_timeout_seconds','test_timeout_seconds') else 864000)
            if type(supplied) is not int or not low<=supplied<=high:
                raise ValueError(f'Invalid bounded integer: {key}')
    if type(result['auto_deploy']) is not bool:
        raise ValueError('auto_deploy must be a boolean')
    for key in ('warden_task','supervisor_task'):
        if not isinstance(result[key],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',result[key]):
            raise ValueError('Scheduled task names must be literal simple names')
    worker=result.get('repair_worker')
    if not isinstance(worker,dict) or set(worker)!={'name','credential_target'} or any(
        not isinstance(value,str) or not value.strip() or '\x00' in value for value in worker.values()):
        raise ValueError('repair_worker requires its dedicated name and Credential Manager target')
    providers=result.get('providers')
    if not isinstance(providers,list) or not 1<=len(providers)<=3:
        raise ValueError('Configure one to three repair provider CLI specifications')
    seen=set()
    for spec in providers:
        from .runner import validate_provider_spec
        try:
            validate_provider_spec(spec)
        except (ValueError, TypeError) as exc:
            if isinstance(spec,dict) and spec.get('provider')=='gemini':
                result.setdefault('provider_contract_errors',{})['gemini']=str(exc)
                if 'gemini' in seen:
                    raise ValueError('Repair providers must be distinct')
                seen.add('gemini')
                continue
            raise ValueError('Repair providers require exact Chapter 06 models and native CLI contracts') from exc
        if spec['provider'] in seen:
            raise ValueError('Repair providers must be distinct')
        seen.add(spec['provider'])
        if not isinstance(spec.get('executable'),str) or '\x00' in spec['executable'] or not Path(spec['executable']).is_absolute():
            raise ValueError('Repair CLI must be an explicit absolute native executable')
    targets=result['test_targets']
    if not isinstance(targets,list) or not targets or any(not isinstance(item,str) or
        not re.fullmatch(r'(pipeline_tests|mcp_tests)(/[A-Za-z0-9_]+\.py)?',item) for item in targets):
        raise ValueError('Acceptance targets must refer only to independently installed test suites')
    allowed=result['allowed_paths']
    if not isinstance(allowed,list) or not allowed or any(not isinstance(item,str) or
        not any(item==base or (base.endswith('/') and item.startswith(base)) for base in DEFAULT_ALLOWED)
        or '..' in item or '\\' in item or ':' in item or '\x00' in item for item in allowed):
        raise ValueError('Allowed repair paths must stay within pipeline/MCP Python source')
    pipeline=json.loads(Path(result['pipeline_config']).read_text(encoding='utf-8-sig'))
    if not isinstance(pipeline,dict):
        raise ValueError('Pipeline configuration must be an object')
    for field in ('private_root','token_file'):
        if (not isinstance(pipeline.get(field),str) or '\x00' in pipeline[field]
                or not Path(pipeline[field]).is_absolute()):
            raise ValueError(f'Pipeline {field} must be an absolute native path')
    result['pipeline_private_root']=pipeline['private_root']
    result['pipeline_token_file']=pipeline['token_file']
    result['pipeline_port']=pipeline.get('port',47824)
    if type(result['pipeline_port']) is not int or not 1024<=result['pipeline_port']<=65535:
        raise ValueError('Invalid pipeline loopback port')
    result['pipeline_providers']=pipeline.get('providers',{})
    if not isinstance(result['pipeline_providers'],dict):
        raise ValueError('Pipeline providers must be an object')
    if 'gemini' not in seen and isinstance(result['pipeline_providers'].get('gemini'),dict):
        gemini={**result['pipeline_providers']['gemini'],'provider':'gemini'}
        if all(key in gemini for key in ('executable','arguments','protocol','subscription_probe')):
            try:
                validate_provider_spec(gemini)
            except (ValueError,TypeError) as exc:
                result.setdefault('provider_contract_errors',{})['gemini']=str(exc)
            result['providers'].append(gemini)
    result['pipeline_routing']=deepcopy(pipeline.get('routing',{}))
    # Independent parsing must remain usable when candidate pipeline imports
    # are broken; this reads only the protected policy, never a model module.
    from .probes import configured_current_routing_policy
    result['pipeline_routing']=configured_current_routing_policy(result['pipeline_routing'])
    return result
