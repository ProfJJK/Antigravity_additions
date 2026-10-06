"""Bounded crash ingestion and private SQLite backup/evidence bundles.

The bounded raw database snapshot stays in SYSTEM-private storage and is never
an input to repair inference. The separate metadata projection and crash/metric
reports omit prompts, artifacts, exception values, source lines and locals.
"""
from __future__ import annotations
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from .io import write_json
from .releases import _plain_ancestors,_stat_plain


def _private_snapshot(database: Path,destination: Path) -> dict:
    """Copy actual SQLite pages under finite space/time limits, including WAL."""
    maximum=32*1024*1024
    deadline=time.monotonic()+1
    try:
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=.2)) as source:
            source.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,262144)
            source.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH,8192)
            source.setlimit(sqlite3.SQLITE_LIMIT_COLUMN,128)
            source.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH,32)
            source.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED,0)
            source.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            source.execute('PRAGMA query_only=ON')
            source.execute('PRAGMA trusted_schema=OFF')
            source.execute('BEGIN')
            page_size=source.execute('PRAGMA page_size').fetchone()[0]
            page_count=source.execute('PRAGMA page_count').fetchone()[0]
            if page_size*page_count>maximum:
                return {'state':'unavailable','reason':'size_limit','maximum_bytes':maximum}
            def progress(status,remaining,total):
                if total*page_size>maximum or time.monotonic()>deadline:
                    raise TimeoutError('Private snapshot exceeded its time or page bound')
            with closing(sqlite3.connect(destination)) as target:
                target.execute('PRAGMA max_page_count='+str(maximum//page_size))
                source.backup(target,pages=64,progress=progress,sleep=.01)
        if destination.stat().st_size>maximum:
            raise ValueError('Snapshot exceeded its file bound')
        return {'state':'captured','scope':'SYSTEM-private raw database; never sent to repair inference',
                'contains_private_payloads':True,'consistent_sqlite_backup':True,'maximum_bytes':maximum}
    except (OSError,ValueError,sqlite3.Error,TimeoutError) as exc:
        destination.unlink(missing_ok=True)
        return {'state':'unavailable','error_type':type(exc).__name__,'maximum_bytes':maximum}


def read_crash(private_root: Path, *, now: float | None=None, maximum_age=600) -> dict:
    path=Path(private_root)/'crash-envelope.json'
    if not path.exists() and not path.is_symlink():
        return {'state':'absent'}
    at=time.time() if now is None else now
    try:
        _plain_ancestors(path); info=_stat_plain(path)
        if info.st_size>65536:
            raise ValueError('Crash envelope exceeds 64 KiB')
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or value.get('schema')!=1:
            raise ValueError('Unknown crash envelope schema')
        for key in ('timestamp','process_started_at'):
            if type(value.get(key)) not in (int,float) or not math.isfinite(value[key]) or value[key]<0:
                raise ValueError('Invalid crash clock')
        if value['timestamp']<value['process_started_at'] or value['timestamp']>at+5:
            raise ValueError('Crash timestamps are inconsistent')
        if at-value['timestamp']>maximum_age:
            return {'state':'expired','timestamp':value['timestamp']}
        if type(value.get('pid')) is not int or value['pid']<=0:
            raise ValueError('Invalid crash process identity')
        name=value.get('exception_type')
        if not isinstance(name,str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}',name):
            raise ValueError('Invalid crash exception type')
        categories={'code','compatibility','configuration','auth','resource','quota','busy','context','provider','backlog','timeout','protocol'}
        if value.get('category') not in categories:
            raise ValueError('Invalid crash failure category')
        frames=value.get('frames')
        if not isinstance(frames,list) or not 1<=len(frames)<=32:
            raise ValueError('Crash envelope requires at most 32 physical frames')
        projected=[]
        for frame in frames:
            if not isinstance(frame,dict):
                raise ValueError('Invalid crash frame')
            for field,maximum in (('filename',256),('function',128)):
                if not isinstance(frame.get(field),str) or not re.fullmatch(r'[A-Za-z0-9_.<>-]{1,'+str(maximum)+'}',frame[field]):
                    raise ValueError('Crash frame names must not contain host paths or arbitrary text')
            coordinates={key:frame.get(key) for key in ('lineno','end_lineno','colno','end_colno')}
            if (type(coordinates['lineno']) is not int or coordinates['lineno']<1 or
                any(item is not None and (type(item) is not int or not 0<=item<=10_000_000) for item in coordinates.values())):
                raise ValueError('Invalid PEP 657 frame coordinates')
            projected.append({'filename':frame['filename'],'function':frame['function'],**coordinates})
        # Never copy arbitrary diagnostic/source/locals fields from the producer.
        return {'state':'observed','schema':1,'timestamp':value['timestamp'],'pid':value['pid'],
            'process_started_at':value['process_started_at'],'category':value['category'],
            'exception_type':name,'diagnostic':name+'; structured crash frames captured; private values omitted',
            'frames':projected}
    except (OSError,ValueError,TypeError,RuntimeError) as exc:
        return {'state':'invalid','error_type':type(exc).__name__}


def capture_bundle(private_root: Path, destination: Path, observation: dict) -> dict:
    root,destination=Path(private_root),Path(destination)
    _plain_ancestors(destination.parent)
    destination.mkdir(mode=0o700,exist_ok=False)
    result={'schema':1,'scope':'SYSTEM-private raw SQLite backup plus safe metadata projection, process metrics and PEP 657 frames',
        'safe_projection_omits':['prompts','payloads','artifacts','authentication','source lines','locals'],
        'files':{},'captured_at':time.time()}
    database=root/'job_board.db'
    snapshot=destination/'database-metadata.db'
    try:
        _plain_ancestors(database); _stat_plain(database)
        with database.open('rb') as stream:
            header=stream.read(16)
        result['sqlite_header_valid']=header==b'SQLite format 3\x00'
        result['private_database_snapshot']=_private_snapshot(database,destination/'database-private.db')
        if not result['sqlite_header_valid']:
            with database.open('rb') as stream:
                (destination/'database-header.bin').write_bytes(stream.read(4096))
        deadline=time.monotonic()+1
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=.2)) as source:
            source.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,262144)
            source.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH,8192)
            source.setlimit(sqlite3.SQLITE_LIMIT_COLUMN,128)
            source.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH,32)
            source.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED,0)
            source.execute('PRAGMA query_only=ON');source.execute('PRAGMA trusted_schema=OFF')
            source.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            source.row_factory=sqlite3.Row
            source.execute('BEGIN')
            schema=source.execute("SELECT name,type,sql,rootpage FROM sqlite_schema WHERE name IN ('pipeline_jobs','pipeline_events')").fetchall()
            if ({row['name'] for row in schema}!={'pipeline_jobs','pipeline_events'} or
                    any(row['type']!='table' or row['rootpage']<=0 or not row['sql'].lstrip().upper().startswith('CREATE TABLE ') for row in schema)):
                raise ValueError('Diagnostic source metadata must use ordinary physical tables')
            columns={row[1] for row in source.execute('PRAGMA table_info(pipeline_jobs)')}
            fields=[name for name in ('kind','status','attempts','max_attempts','updated_at','lease_expires_at') if name in columns]
            if not {'kind','status','updated_at'}<=columns:
                raise ValueError('Job metadata schema is unavailable')
            expressions=[('substr('+name+',1,64) AS '+name) if name in {'kind','status'} else name for name in fields]
            if 'lease_owner' in columns:
                expressions.append('(lease_owner IS NOT NULL) AS lease_owned');fields.append('lease_owned')
            rows=source.execute('SELECT '+','.join(expressions)+' FROM pipeline_jobs ORDER BY updated_at DESC LIMIT 257').fetchall()
            events=source.execute('SELECT substr(event,1,96) AS event,timestamp FROM pipeline_events ORDER BY id DESC LIMIT 257').fetchall()
            with closing(sqlite3.connect(snapshot)) as target:
                target.execute('PRAGMA max_page_count=256')
                target.execute('CREATE TABLE jobs ('+','.join(name+' '+('TEXT' if name in {'kind','status'} else 'REAL') for name in fields)+')')
                target.execute('CREATE TABLE events(event TEXT,timestamp REAL)')
                for row in rows[:256]:
                    values=[]
                    for name in fields:
                        value=row[name]
                        if name in {'kind','status'}:
                            value=value if isinstance(value,str) and re.fullmatch('[A-Z_]{1,64}',value) else 'UNKNOWN'
                        elif value is not None and (type(value) not in (int,float) or not math.isfinite(value)):
                            value=None
                        values.append(value)
                    target.execute('INSERT INTO jobs VALUES('+','.join('?' for _ in fields)+')',values)
                for row in events[:256]:
                    event=row['event'] if isinstance(row['event'],str) and re.fullmatch('[A-Z_]{1,96}',row['event']) else 'UNKNOWN'
                    at=row['timestamp'] if type(row['timestamp']) in (int,float) and math.isfinite(row['timestamp']) else None
                    target.execute('INSERT INTO events VALUES(?,?)',(event,at))
                target.commit()
        result['database']={'state':'captured','sampled_jobs':min(len(rows),256),'sampled_events':min(len(events),256),
                            'truncated':len(rows)>256 or len(events)>256,'consistent_read_transaction':True}
    except (OSError,ValueError,sqlite3.Error,RuntimeError) as exc:
        snapshot.unlink(missing_ok=True)
        result['database']={'state':'unavailable','error_type':type(exc).__name__}
        result.setdefault('private_database_snapshot',{'state':'unavailable','error_type':type(exc).__name__})
    write_json(destination/'crash.json',read_crash(root))
    write_json(destination/'metrics.json',observation.get('health',{}))
    for path in sorted(destination.iterdir()):
        _stat_plain(path)
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        result['files'][path.name]={'sha256':digest.hexdigest(),'size':path.stat().st_size,
            'private_payload':path.name in {'database-private.db','database-header.bin'}}
    write_json(destination/'manifest.json',result)
    return {**result,'directory':str(destination),'manifest_sha256':hashlib.sha256((destination/'manifest.json').read_bytes()).hexdigest()}
