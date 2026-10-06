"""Manual bounded provider preflight through the ordinary protected job board.

Offline version/help checks cover every installed CLI. Optional model inference
submits one tool-free PREFLIGHT_REQUEST with at most three native dispatches and one accepted result; Chapter 06 chooses
its model. Unselected providers remain explicitly unverified.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
import uuid

from .io import write_json
from .probes import verify_routing_assignment


def run_preflight(supervisor, *, model_probe=False, timeout_seconds=120):
    if type(model_probe) is not bool or type(timeout_seconds) is not int or not 1<=timeout_seconds<=600:
        raise ValueError('Preflight requires explicit inference choice and a bounded deadline')
    supervisor._version_checks(force=True)
    contracts=json.loads((supervisor.private/'cli-contracts.json').read_text(encoding='utf-8'))
    authentication={}
    from cochem_mcp.providers import executable_prefix
    from cochem_pipeline.worker import subscription_status, subscription_probe_status
    for spec in supervisor.config['providers']:
        provider=spec['provider']
        try:
            prefix=executable_prefix(provider,spec['executable']) if provider!='gemini' else [spec['executable']]
            arguments=(['login','status'] if provider=='codex' else
                ['--setting-sources','','auth','status','--json'] if provider=='claude' else spec['subscription_probe']['arguments'])
            receipt=supervisor.runner.run_process(supervisor.config['repair_worker'],[*prefix,*arguments],
                Path(supervisor.config['repair_workspace']),supervisor.private/'preflight-auth'/uuid.uuid4().hex,
                timeout_seconds=30,heartbeat=supervisor._heartbeat)
            stdout=Path(receipt['stdout_path']).read_text(encoding='utf-8',errors='replace')
            stderr=Path(receipt['stderr_path']).read_text(encoding='utf-8',errors='replace')
            verified=(subscription_probe_status(stdout,stderr,receipt['exit_code'],spec['subscription_probe'])
                if provider=='gemini' else subscription_status(provider,stdout,stderr,receipt['exit_code']))
            authentication[provider]={'checked':True,'subscription_verified':verified,
                'scope':'dedicated repair worker identity','pid':receipt['pid'],'exit_code':receipt['exit_code'],
                'stdout_sha256':receipt.get('stdout_sha256')}
        except (ValueError,KeyError,OSError,RuntimeError) as exc:
            authentication[provider]={'checked':False,'subscription_verified':False,
                'scope':'dedicated repair worker identity','reason_type':type(exc).__name__}
    configured=['claude','codex','gemini']
    for provider in configured:
        contracts['providers'].setdefault(provider,{'available':False,'reason':'Native CLI contract is not configured'})
        authentication.setdefault(provider,{'checked':False,'subscription_verified':False,'reason':'Native CLI contract is not configured'})
    result={'schema':'cochem-provider-preflight/4.2.7','manual':True,'started_at':time.time(),
        'offline_contracts':contracts['providers'],'authentication':authentication,'model_probe_requested':model_probe,
        'model_dispatch_limit':3 if model_probe else 0,'accepted_result_limit':1,'covered_providers':[],
        'unverified_providers':configured,'model_identity_verified':False,
        'note':'Offline CLI capabilities do not establish model availability; routing never pins a provider.'}
    if not model_probe:
        return result
    identifier='manual-preflight-'+uuid.uuid4().hex
    result['workflow_id']=identifier
    finished=False
    try:
        workflow=supervisor.client.call('/preflight',{'workflow_id':identifier})
        deadline=time.monotonic()+timeout_seconds
        while workflow.get('status') not in ('COMPLETED','FAILED'):
            if supervisor.stop_event.is_set() or time.monotonic()>=deadline:
                raise TimeoutError('Bounded routed model preflight expired')
            supervisor.stop_event.wait(min(.2,max(0,deadline-time.monotonic())))
            workflow=supervisor.client.call('/workflow/'+identifier)
        finished=True
        receipts=[]
        for job in workflow.get('jobs',[]):
            if job.get('kind')=='MACRO_PLANNING_REQUEST' or job.get('status')!='COMPLETED':
                continue
            if job.get('kind')!='PREFLIGHT_REQUEST' or job.get('output')!={'ready':True}:
                raise ValueError('Preflight completed an unexpected job or response')
            assignment=verify_routing_assignment(job,supervisor.config.get('pipeline_routing'))
            receipt=job['receipt']
            if (receipt.get('subscription_verified') is not True or receipt.get('exit_code')!=0 or
                type(receipt.get('pid')) is not int or receipt['pid']<=0 or not receipt.get('session_id')):
                raise ValueError('Preflight model completion lacks native process/subscription evidence')
            receipts.append({**assignment,'pid':receipt['pid'],'session_id':receipt['session_id'],
                'reported_model':receipt.get('reported_model'),'stdout_sha256':receipt.get('stdout_sha256')})
        if len(receipts)>1:
            raise ValueError('Preflight exceeded its one-model dispatch bound')
        covered=sorted({row['provider'] for row in receipts})
        result.update(status=workflow['status'],receipts=receipts,covered_providers=covered,
            unverified_providers=sorted(set(configured)-set(covered)),
            model_identity_verified=bool(receipts) and all(row['reported_model']==row['model'] for row in receipts))
    finally:
        if not finished:
            result['cancel_receipt']=supervisor.client.call('/cancel',{'workflow_id':identifier})
        result['finished_at']=time.time()
        write_json(supervisor.private/'preflight'/f'{identifier}.json',result)
    return result
