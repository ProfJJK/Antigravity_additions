"""Durable bounded infrastructure recovery, independent of model repair budgets.

One automatic recovery per component is deliberately stricter than the old
six-restarts/hour policy. Observations and a delay must precede reservation;
crashes never refund a reservation, and later healthy observations never erase
its budget. Operator diagnosis is required after exhaustion.
"""
from __future__ import annotations

from contextlib import closing
import ctypes
import json
import math
import os
from pathlib import Path
import sqlite3
import time


class RecoveryLedger:
    def __init__(self, path: str | Path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=5)) as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS components(
                    component TEXT PRIMARY KEY,strikes INTEGER NOT NULL,
                    first_failure REAL,last_observed REAL NOT NULL,attempts INTEGER NOT NULL,
                    reserved_at REAL,last_result TEXT);
                CREATE TABLE IF NOT EXISTS recovery_events(
                    id INTEGER PRIMARY KEY,timestamp REAL NOT NULL,component TEXT NOT NULL,
                    event TEXT NOT NULL,details_json TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS recovery_no_update BEFORE UPDATE ON recovery_events
                BEGIN SELECT RAISE(ABORT,'Recovery evidence is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS recovery_no_delete BEFORE DELETE ON recovery_events
                BEGIN SELECT RAISE(ABORT,'Recovery evidence is immutable'); END;
            ''')

    def observe(self, component: str, healthy: bool, *, now: float | None=None) -> dict:
        if component not in {'warden','docker_engine'} or type(healthy) is not bool:
            raise ValueError('Recovery requires a known component and an explicit health observation')
        now=time.time() if now is None else now
        if type(now) not in (int,float) or not math.isfinite(now) or now<0:
            raise ValueError('Recovery time must be finite and nonnegative')
        with closing(sqlite3.connect(self.path,timeout=5,isolation_level=None)) as db:
            db.row_factory=sqlite3.Row
            db.execute('BEGIN IMMEDIATE')
            try:
                row=db.execute('SELECT * FROM components WHERE component=?',(component,)).fetchone()
                strikes=0 if healthy else (row['strikes']+1 if row else 1)
                first=None if healthy else (row['first_failure'] if row and row['first_failure'] is not None else now)
                attempts=row['attempts'] if row else 0
                reserved=row['reserved_at'] if row else None
                if row and now<row['last_observed']:
                    raise ValueError('Recovery observations cannot move backwards in time')
                state='healthy' if healthy else 'exhausted' if attempts>=1 else 'waiting'
                if not healthy and attempts<1 and strikes>=3 and now-first>=30:
                    state='reserved'
                    attempts+=1
                    reserved=now
                db.execute('''INSERT INTO components VALUES(?,?,?,?,?,?,NULL)
                    ON CONFLICT(component) DO UPDATE SET strikes=excluded.strikes,
                    first_failure=excluded.first_failure,last_observed=excluded.last_observed,
                    attempts=excluded.attempts,reserved_at=excluded.reserved_at''',
                    (component,strikes,first,now,attempts,reserved))
                result={'component':component,'state':state,'strikes':strikes,'required_strikes':3,
                        'backoff_seconds':30,'attempts':attempts,'maximum_attempts':1,
                        'next_eligible_at':None if first is None or attempts else first+30}
                db.execute('INSERT INTO recovery_events(timestamp,component,event,details_json) VALUES(?,?,?,?)',
                    (now,component,'RECOVERY_RESERVED' if state=='reserved' else 'OBSERVED',json.dumps(result,sort_keys=True)))
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise

    def finish(self, component: str, outcome: dict) -> None:
        encoded=json.dumps(outcome,sort_keys=True,allow_nan=False)
        if len(encoded)>65536:
            raise ValueError('Recovery result exceeds its evidence bound')
        with closing(sqlite3.connect(self.path,timeout=5)) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT attempts,last_result FROM components WHERE component=?',(component,)).fetchone()
            if row is None or row[0]!=1 or row[1] is not None:
                raise ValueError('Recovery requires an unfinished durable reservation')
            db.execute('UPDATE components SET last_result=? WHERE component=?',(encoded,component))
            db.execute('INSERT INTO recovery_events(timestamp,component,event,details_json) VALUES(?,?,?,?)',
                       (time.time(),component,'RECOVERY_FINISHED',encoded))
            db.commit()


def start_docker_service(timeout_seconds: float=30) -> dict:
    """Attempt only the fixed Docker Windows service; never stop another service.

    A running service is not proof of a healthy Docker engine. The next actual
    Docker probe must establish that independently. No service name, executable
    path, shell command, credential or restart loop comes from a model.
    """
    if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or not 0<timeout_seconds<=30:
        raise ValueError('Service recovery timeout must be in (0,30] seconds')
    if os.name!='nt':
        raise RuntimeError('Docker Windows-service recovery requires native Windows')
    handle=ctypes.c_void_p
    dword=ctypes.c_uint32
    class ServiceStatus(ctypes.Structure):
        _fields_=[(name,dword) for name in ('type','state','controls','win32_exit','service_exit',
                                          'checkpoint','wait_hint','pid','flags')]
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    api.OpenSCManagerW.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,dword]
    api.OpenSCManagerW.restype=handle
    api.OpenServiceW.argtypes=[handle,ctypes.c_wchar_p,dword]
    api.OpenServiceW.restype=handle
    api.QueryServiceStatusEx.argtypes=[handle,ctypes.c_int,ctypes.c_void_p,dword,ctypes.POINTER(dword)]
    api.QueryServiceStatusEx.restype=ctypes.c_int
    api.StartServiceW.argtypes=[handle,dword,ctypes.POINTER(ctypes.c_wchar_p)]
    api.StartServiceW.restype=ctypes.c_int
    api.CloseServiceHandle.argtypes=[handle]
    api.CloseServiceHandle.restype=ctypes.c_int
    manager=api.OpenSCManagerW(None,None,1)
    if not manager:
        raise ctypes.WinError(ctypes.get_last_error())
    service=None
    try:
        service=api.OpenServiceW(manager,'com.docker.service',0x14)
        if not service:
            raise ctypes.WinError(ctypes.get_last_error())
        deadline=time.monotonic()+timeout_seconds
        requested=False
        while True:
            status=ServiceStatus(); needed=dword()
            if not api.QueryServiceStatusEx(service,0,ctypes.byref(status),ctypes.sizeof(status),ctypes.byref(needed)):
                raise ctypes.WinError(ctypes.get_last_error())
            if status.state==4:
                return {'service':'com.docker.service','service_running':True,'start_requested':requested,
                        'engine_health_verified':False}
            if status.state==1 and not requested:
                if not api.StartServiceW(service,0,None):
                    error=ctypes.get_last_error()
                    if error!=1056:
                        raise ctypes.WinError(error)
                requested=True
            if time.monotonic()>=deadline:
                raise OSError('Docker service did not reach RUNNING within the bounded recovery timeout')
            time.sleep(.2)
    finally:
        if service:
            api.CloseServiceHandle(service)
        api.CloseServiceHandle(manager)
