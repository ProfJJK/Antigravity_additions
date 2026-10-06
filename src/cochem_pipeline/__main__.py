"""Warden service and unprivileged client commands."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
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


def daemon(filename):
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
    with service_lock(config.private_root/'warden.lock'):
        runtime=Runtime(config)
        server=ControlServer(runtime,config.token_file.read_text(encoding='utf-8').strip())
        def stop(*_):
            runtime.stop_event.set()
        signal.signal(signal.SIGINT,stop)
        signal.signal(signal.SIGTERM,stop)
        thread=threading.Thread(target=server.serve_forever,daemon=True,name='warden-control')
        thread.start()
        try:
            runtime.run()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)


def main():
    parser=argparse.ArgumentParser(description=f'CoChem {__version__} privileged planning pipeline')
    parser.add_argument('--version',action='version',version=__version__)
    commands=parser.add_subparsers(dest='command',required=True)
    service=commands.add_parser('daemon')
    service.add_argument('--config',required=True)
    for name in ('mcp','submit','status','cancel','health'):
        command=commands.add_parser(name)
        command.add_argument('--client-config',required=True)
        if name=='submit':
            command.add_argument('--objective',required=True)
            command.add_argument('--requirements',nargs='+',default=['REQ-001'])
            command.add_argument('--chapters',type=int,default=6)
        if name in ('status','cancel'):
            command.add_argument('workflow_id')
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,stream=sys.stderr)
    if args.command=='daemon':
        daemon(args.config)
        return
    config=json.loads(Path(args.client_config).read_text(encoding='utf-8-sig'))
    client=ControlClient(config.get('port',47824),config['token_file'])
    if args.command=='mcp':
        from .server import create_server
        create_server(client).run(transport='stdio')
        return
    if args.command=='submit':
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
