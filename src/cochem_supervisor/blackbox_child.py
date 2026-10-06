"""Untrusted candidate adapter, invoked only inside the bounded repair identity.

This process does NOT attest tests. Its replies and all candidate-written data
are inputs to the separate protected supervisor verifier. It never receives
the private verdict location or imports the trusted verifier.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--candidate',required=True,type=Path)
    parser.add_argument('--database',required=True,type=Path)
    args=parser.parse_args()
    request=json.loads(sys.stdin.read(262145))
    sys.dont_write_bytecode=True
    sys.path.insert(0,str(args.candidate/'src'))
    from cochem_pipeline.store import JobStore
    store=JobStore(args.database)
    operation=request['operation']; data=request.get('data',{})
    try:
        if operation=='submit':
            result=store.submit(**data)
        elif operation=='claim':
            result=store.claim(**data)
        elif operation=='complete':
            result=store.complete(**data)
        elif operation=='heartbeat':
            result=store.heartbeat(**data)
        elif operation=='coding_init':
            from cochem_pipeline.coding import CodingProject
            from cochem_pipeline.coding_git import RepositorySnapshot,snapshot_digest
            from cochem_pipeline.container_policy import DockerPolicy
            from cochem_pipeline.routing import load_routing_policy
            store=JobStore(args.database,routing_policy=load_routing_policy({}))
            files={name:bytes.fromhex(content) for name,content in data['files'].items()}
            root=str(args.database.parent/'project')
            for name,content in files.items():
                path=Path(root)/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(content)
            project=CodingProject.from_dict('outer-contract',{'repository':root,'branch':'contract',
                'allowed_paths':['src'],'test_paths':['tests'],'auto_integrate':False})
            snapshot=RepositorySnapshot(root,'contract','refs/heads/contract','1'*40,files,
                {name:'100644' for name in files},{name:'2'*40 for name in files},snapshot_digest(files))
            result=store.submit_coding_snapshot(project,data['objective'],data['requirements'],snapshot,
                DockerPolicy(),workflow_id=data['workflow_id'])
        elif operation=='coding_plan':
            from cochem_pipeline.coding_plan import validate_plan
            from cochem_pipeline.coding import CodingProject
            job=store.get(data['job_id']); state=store.coding_state(job['workflow_id'])
            plan=validate_plan(data['output'],CodingProject.from_dict('outer-contract',state['project']),
                               store.coding_files(state['current_snapshot']),job['payload']['requirements'])
            result=store.complete_coding(**data,evidence={'plan':plan})
        elif operation=='complete_coding':
            result=store.complete_coding(**data)
        else:
            raise ValueError('Unknown bounded adapter operation')
        print(json.dumps({'result':result},ensure_ascii=True,allow_nan=False))
    except (ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'error_type':type(exc).__name__}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
