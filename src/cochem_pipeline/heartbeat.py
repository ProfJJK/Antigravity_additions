"""Atomic scheduler progress evidence for the independently installed supervisor."""
from __future__ import annotations
import json
import os
from pathlib import Path
import time
import uuid


class Heartbeat:
    def __init__(self, private_root: Path, version: str, interval: float = 2):
        self.path = Path(private_root)/'supervisor_status.json'
        self.version, self.interval = version, interval
        self.instance_id = uuid.uuid4().hex
        self.started_at = time.time()
        self.sequence = 0
        self.last_write = float('-inf')

    def completed_tick(self, status: dict, *, now: float | None = None) -> bool:
        observed = time.time() if now is None else now
        self.sequence += 1
        if observed-self.last_write < self.interval:
            return False
        data = {'schema':1,'instance_id':self.instance_id,'pid':os.getpid(),
                'process_started_at':self.started_at,'sequence':self.sequence,
                'timestamp':observed,'version':self.version,'status':status,
                'source_root':str(Path(__file__).resolve().parents[2])}
        temporary = self.path.with_name('.heartbeat-'+self.instance_id+'.tmp')
        try:
            with temporary.open('w',encoding='utf-8') as stream:
                json.dump(data,stream,ensure_ascii=False,allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary,self.path)
            self.last_write = observed
        finally:
            temporary.unlink(missing_ok=True)
        return True
