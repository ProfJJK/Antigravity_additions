"""Independent supervisor commands; paid repair starts only in daemon/once mode."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import signal
import sys

from . import __version__
from .config import load_config
from .state import Ledger


@contextmanager
def supervisor_lock(path:Path):
    # Windows production only. This lock is outside the managed pipeline's DB.
    import msvcrt
    with path.open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0');stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        yield


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',action='version',version=__version__)
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('daemon','once','recover','status','request-update','resume'):
        command=commands.add_parser(name)
        command.add_argument('--config',required=True)
        if name=='request-update':
            command.add_argument('--objective',required=True)
        if name=='resume':
            command.add_argument('--fingerprint',required=True)
            command.add_argument('--reason',default='Operator confirmed prerequisite restored')
    args=parser.parse_args()
    config=load_config(args.config)
    private=Path(config['private_root'])
    if args.command in ('status','request-update','resume'):
        ledger=Ledger(private/'supervisor.db')
        if args.command=='status':
            path=private/'supervisor-status.json'
            result={'supervisor':json.loads(path.read_text(encoding='utf-8')) if path.exists() else None,
                    'incidents':ledger.list_incidents()}
        elif args.command=='resume':
            result=ledger.unblock(args.fingerprint,args.reason)
        else:
            objective=args.objective.strip()
            if not objective or len(objective)>16000:
                raise ValueError('Update objective must contain 1..16000 characters')
            fingerprint=hashlib.sha256(('operator-update\0'+objective).encode()).hexdigest()
            incident={'fingerprint':fingerprint,'category':'update','repairable':True,
                      'summary':'Operator requested a bounded pipeline update','objective':objective,
                      'evidence':{'source':'operator-request'}}
            result=ledger.observe(fingerprint,'update',incident)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return
    from .windows import require_supervisor
    from .engine import Supervisor
    require_supervisor(config)
    logging.basicConfig(level=logging.INFO,stream=sys.stderr)
    log=RotatingFileHandler(private/'supervisor.log',maxBytes=8*1024*1024,backupCount=3,encoding='utf-8')
    logging.getLogger().addHandler(log)
    with supervisor_lock(private/'supervisor.lock'):
        supervisor=Supervisor(config)
        def stop(*_):
            supervisor.stop_event.set()
            supervisor.runner.terminate()
        signal.signal(signal.SIGINT,stop)
        signal.signal(signal.SIGTERM,stop)
        if args.command=='daemon':
            supervisor.run()
        elif args.command=='recover':
            print(json.dumps(supervisor.recover(),ensure_ascii=False,indent=2))
        else:
            supervisor.recover()
            try:
                print(json.dumps(supervisor.tick(ignore_startup_grace=True),ensure_ascii=False,indent=2))
            finally:
                supervisor.runner.terminate()


if __name__=='__main__':
    main()
