"""Warden service and unprivileged client commands."""
from __future__ import annotations
import argparse
from contextlib import contextmanager, nullcontext
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import signal
import sys
import threading

from . import __version__
from .config import load_config
from .service import ControlClient


@contextmanager
def service_lock(path:Path):
    # Called only after SYSTEM and private-directory validation, before slot cleanup.
    import msvcrt
    handle=path.open('a+b')
    try:
        handle.seek(0)
        if not handle.read(1):
            handle.write(b'0');handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        yield
    finally:
        handle.close()


def daemon(filename, queue_launch_output=None):
    from .windows import WorkerIdentity,require_system,validate_private_directory,validate_controller_token
    from .runtime import Runtime
    from .service import ControlServer
    config=load_config(filename)
    require_system()
    validate_private_directory(config.private_root)
    validate_controller_token(config.token_file,config.operator_name,
                              {k:WorkerIdentity(**v) for k,v in config.workers.items()})
    log = RotatingFileHandler(config.private_root/'warden.log',maxBytes=8*1024*1024,
                              backupCount=3,encoding='utf-8')
    log.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    logging.getLogger().addHandler(log)
    from .crash import record_crash
    prior_thread_hook = threading.excepthook
    def crash_thread(args):
        try:
            record_crash(config.private_root, args.exc_value,
                         getattr(args.exc_value, 'category', 'code'))
        except Exception:
            logging.error('Private thread crash metadata could not be persisted')
        prior_thread_hook(args)
    threading.excepthook = crash_thread
    try:
        with service_lock(config.private_root/'warden.lock'):
            runtime=Runtime(config)
            server=ControlServer(runtime,config.token_file.read_text(encoding='utf-8').strip())
            def stop(*_):
                runtime.stop_event.set()
            signal.signal(signal.SIGINT,stop)
            signal.signal(signal.SIGTERM,stop)
            thread=threading.Thread(target=server.serve_forever,daemon=True,name='warden-control')
            thread.start()
            runtime_started = False
            try:
                if queue_launch_output is not None:
                    from .queue_launch import QueueLaunchObserver
                    observation = QueueLaunchObserver(runtime, queue_launch_output)
                else:
                    observation = nullcontext()
                with observation:
                    runtime_started = True
                    runtime.run()
            finally:
                try:
                    if not runtime_started:
                        # An invalid observation destination must not leave the
                        # already constructed runtime's resources alive.
                        runtime.stop_event.set()
                        runtime.run()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=10)
    except Exception as exc:
        try:
            record_crash(config.private_root, exc, getattr(exc, 'category', 'code'))
        except Exception:
            logging.error('Private service crash metadata could not be persisted')
        raise
    finally:
        threading.excepthook = prior_thread_hook


def main():
    parser=argparse.ArgumentParser(description=f'CoChem {__version__} privileged planning and coding pipeline')
    parser.add_argument('--version',action='version',version=__version__)
    commands=parser.add_subparsers(dest='command',required=True)
    service=commands.add_parser('daemon')
    service.add_argument('--config',required=True)
    service.add_argument('--queue-launch-output',help='New protected evidence directory for real Windows launch queue observations')
    for name in ('doctor','provision-execution'):
        deployment=commands.add_parser(name)
        deployment.add_argument('--config',required=True)
    for name in ('mcp','submit','status','cancel','health','projects','code','code-status','code-cancel','code-resume',
                 'knowledge-search','knowledge-read','knowledge-status','knowledge-refresh'):
        command=commands.add_parser(name)
        command.add_argument('--client-config',required=True)
        if name=='submit':
            command.add_argument('--objective',required=True)
            command.add_argument('--requirements',nargs='+',default=['REQ-001'])
            command.add_argument('--chapters',type=int,default=6)
        if name=='code':
            command.add_argument('--project',required=True)
            command.add_argument('--objective',required=True)
            command.add_argument('--requirements',nargs='+',default=['REQ-001'])
            command.add_argument('--workflow-id')
        if name in ('status','cancel','code-status','code-cancel','code-resume'):
            command.add_argument('workflow_id')
        if name=='code-resume':
            command.add_argument('--reason',required=True)
        if name=='knowledge-search':
            command.add_argument('query')
            command.add_argument('--limit',type=int,default=5)
        if name=='knowledge-read':
            command.add_argument('doc_path')
        if name=='knowledge-refresh':
            command.add_argument('--full',action='store_true')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,stream=sys.stderr)
    if args.command=='daemon':
        daemon(args.config,queue_launch_output=args.queue_launch_output)
        return
    if args.command in ('doctor','provision-execution'):
        from .deployment import execution_readiness
        report=execution_readiness(load_config(args.config),provision=args.command=='provision-execution')
        print(json.dumps(report,indent=2,ensure_ascii=False))
        raise SystemExit(0 if report['ready'] else 1)
    config=json.loads(Path(args.client_config).read_text(encoding='utf-8-sig'))
    client=ControlClient(config.get('port',47824),config['token_file'])
    if args.command=='mcp':
        from .server import create_server
        create_server(client).run(transport='stdio')
        return
    if args.command=='knowledge-search':
        result=client.call('/knowledge/search',{'query':args.query,'limit':args.limit})
    elif args.command=='knowledge-read':
        result=client.call('/knowledge/read',{'doc_path':args.doc_path})
    elif args.command=='knowledge-status':
        result=client.call('/knowledge/status')
    elif args.command=='knowledge-refresh':
        result=client.call('/knowledge/refresh',{'full':args.full})
    elif args.command=='projects':
        result=client.call('/coding/projects')
    elif args.command=='code':
        result=client.call('/coding/submit',{'project_id':args.project,'objective':args.objective,
                                          'requirements':args.requirements,'workflow_id':args.workflow_id})
    elif args.command=='code-status':
        result=client.call('/coding/workflow/'+args.workflow_id)
    elif args.command in ('code-cancel','code-resume'):
        data={'workflow_id':args.workflow_id}
        if args.command=='code-resume':
            data['reason']=args.reason
        result=client.call('/coding/'+args.command.removeprefix('code-'),data)
    elif args.command=='submit':
        result=client.call('/submit',{'objective':args.objective,'requirements':args.requirements,
                                     'chapter_count':args.chapters})
    elif args.command=='status':
        result=client.call('/workflow/'+args.workflow_id)
    elif args.command=='cancel':
        result=client.call('/cancel',{'workflow_id':args.workflow_id})
    else:
        result=client.call('/health')
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=='__main__':
    main()
