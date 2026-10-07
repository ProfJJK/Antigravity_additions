"""Physical queue benchmark and opt-in observation of a real Windows Warden.

Short probes are diagnostics, never a 48-hour certificate. Desktop heap used
bytes are not inferred from USER/GDI handles or UOI_HEAPSIZE: an independent
native diagnostic must supply fresh used/allocation evidence or that requirement
remains unavailable. This harness never restarts or kills the observed service.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import multiprocessing
import os
from pathlib import Path
import sqlite3
import statistics
import threading
import time

import psutil


def _read_object(path, maximum=1048576):
    with Path(path).open('rb') as stream:
        data=stream.read(maximum+1)
    if len(data)>maximum:
        raise ValueError('Performance evidence exceeds its bounded size')
    result=json.loads(data)
    if not isinstance(result,dict):
        raise ValueError('Performance evidence must be an object')
    return result


def _process_claimer(path, index, start, reports):
    from .store import JobStore
    store = None
    try:
        store = JobStore(path)
        store.reap_expired()
        reports.put(('ready', index))
        if not start.wait(30):
            raise TimeoutError('Queue benchmark start barrier timed out')
        began = time.perf_counter()
        node = store.claim('process-benchmark-'+str(index), max_workers=4, requires_cleanup=False)
        elapsed = (time.perf_counter()-began)*1000
        reports.put(('sample', {'claimer':index, 'elapsed_ms':elapsed,
            'acquired':node is not None, 'job_id':None if node is None else node['job_id']}))
    except BaseException as exc:
        reports.put(('error', repr(exc)))
    finally:
        if store is not None:
            store.close()


def _process_claims(path):
    context = multiprocessing.get_context('spawn')
    start, reports = context.Event(), context.Queue()
    children = [context.Process(target=_process_claimer, args=(path,index,start,reports)) for index in range(10)]
    try:
        for child in children:
            child.start()
        for _ in children:
            kind, value = reports.get(timeout=60)
            if kind != 'ready':
                raise RuntimeError('Queue contender failed before start: '+str(value))
        start.set()
        observations = []
        for _ in children:
            kind, value = reports.get(timeout=60)
            if kind != 'sample':
                raise RuntimeError('Queue contender failed during claim: '+str(value))
            observations.append(value)
        return observations
    finally:
        start.set()
        for child in children:
            if child.pid is not None:
                child.join(timeout=5)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=5)
        reports.close()
        reports.join_thread()


def benchmark_queue(output, *, rounds=20, concurrency='threads'):
    """Ten real concurrent claimers, actual SQLite WAL, four shared seats."""
    from .store import JobStore
    if type(rounds) is not int or not 1<=rounds<=1000:
        raise ValueError('Queue benchmark rounds must be between one and 1000')
    if concurrency not in ('threads','processes'):
        raise ValueError('Queue concurrency must be threads or processes')
    output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    observations=[]
    for round_number in range(rounds):
        store=JobStore(output/('queue-'+str(round_number)+'.db'))
        for index in range(10):
            store.submit('Physical contention '+str(index),['R1'],1)
        barrier=threading.Barrier(10)
        def claim(index):
            barrier.wait(timeout=10)
            started=time.perf_counter()
            node=store.claim('benchmark-'+str(index),max_workers=4,requires_cleanup=False)
            elapsed=(time.perf_counter()-started)*1000
            return {'round':round_number,'claimer':index,'elapsed_ms':elapsed,
                    'acquired':node is not None,'job_id':None if node is None else node['job_id']}
        if concurrency == 'threads':
            with ThreadPoolExecutor(max_workers=10) as pool:
                sampled=list(pool.map(claim,range(10)))
        else:
            sampled=_process_claims(store.path)
            for row in sampled:
                row['round']=round_number
        if len(store.active_jobs())!=4 or sum(row['acquired'] for row in sampled)!=4:
            raise RuntimeError('Real contention violated the four-seat admission invariant')
        observations.extend(sampled)
        store.close()
    acquired=[row['elapsed_ms'] for row in observations if row['acquired']]
    all_times=sorted(row['elapsed_ms'] for row in observations)
    result={'schema':1,'kind':'physical-sqlite-contention','platform':os.name,
            'claimers':10,'rounds':rounds,'samples':observations,
            'concurrency':concurrency,'total_claims':len(observations),
            'measurement_scope':'entire claim call including lock wait, transaction and commit; stores initialized before contention',
            'acquired_count':len(acquired),'four_seat_invariant_verified':True,
            'limit_ms':5,'maximum_acquisition_ms':max(acquired),
            'p50_all_claims_ms':statistics.median(all_times),
            'p95_all_claims_ms':all_times[max(0,math.ceil(.95*len(all_times))-1)],
            'maximum_all_claims_ms':max(all_times),
            'p50_acquisition_ms':statistics.median(acquired),
            'p95_acquisition_ms':sorted(acquired)[max(0,math.ceil(.95*len(acquired))-1)],
            'passed':max(acquired)<5,
            'native_windows_acceptance':False,
            'acceptance_status':'diagnostic_only_pending_actual_windows_launch',
            'acceptance_scope':'Ten-contender storage stress is not the deployed single-controller/four-worker topology; use the actual Warden queue launch observer for Windows acceptance'}
    (output/'queue-performance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def desktop_heap_evidence(path, *, pid, created_at, now):
    """Validate an external native measurement without inventing a heap sensor."""
    if path is None:
        return {'available':False,'passed':False,'reason':'No native desktop-heap used/allocation measurement is configured'}
    value=_read_object(path)
    for key in ('used_bytes','allocation_limit_bytes','measured_at','process_created_at'):
        number=value.get(key)
        if type(number) not in (int,float) or not math.isfinite(number):
            raise ValueError('Desktop heap evidence needs finite physical measurements')
    if (value.get('pid')!=pid or value['process_created_at']!=created_at
            or not 0<=now-value['measured_at']<=10
            or not 0<=value['used_bytes']<=value['allocation_limit_bytes']
            or value['allocation_limit_bytes']<=0 or not isinstance(value.get('source'),str)
            or not value['source'].strip()):
        raise ValueError('Desktop heap evidence is stale, unbound or invalid')
    percent=100*value['used_bytes']/value['allocation_limit_bytes']
    return {'available':True,'passed':percent<15,'percent':percent,'source':value['source'],
            'measurement_scope':'external native diagnostic; this harness does not generate used-byte evidence'}


def _supervisor_timing(database, since):
    if database is None:
        return {'monitor_samples':0,'recovery_samples':0,'monitor_passed':False,'recovery_passed':False}
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True,timeout=1) as conn:
        rows=conn.execute("SELECT event,details_json FROM supervisor_events WHERE timestamp>=? "
            "AND event IN ('SUPERVISOR_OBSERVATION_TIMED','RECOVERY_ACTION_STARTED') ORDER BY id LIMIT 1000000",(since,)).fetchall()
    monitor,recovery=[],[]
    for event,encoded in rows:
        item=json.loads(encoded)
        elapsed=item.get('duration_seconds' if event=='SUPERVISOR_OBSERVATION_TIMED' else 'elapsed_seconds')
        if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed<0:
            raise ValueError('Supervisor returned invalid timing evidence')
        (monitor if event=='SUPERVISOR_OBSERVATION_TIMED' else recovery).append(elapsed)
    return {'monitor_samples':len(monitor),'recovery_samples':len(recovery),
            'maximum_monitor_seconds':max(monitor,default=None),
            'maximum_recovery_start_seconds':max(recovery,default=None),
            'monitor_passed':bool(monitor) and max(monitor)<=1,
            'recovery_passed':bool(recovery) and max(recovery)<=5,
            'recovery_scope':'after collapse confirmation; waiting for required confirmation is recorded separately'}


def observe_native(heartbeat_path, output, *, duration_seconds=172800, interval_seconds=1,
                   supervisor_database=None, desktop_heap_report=None):
    from . import windows as win
    win.require_system()
    if not 1<=duration_seconds<=604800 or not .2<=interval_seconds<=10:
        raise ValueError('Native duration must be 1..604800s and interval .2..10s')
    heartbeat_path=Path(heartbeat_path)
    win.validate_private_path(heartbeat_path)
    if supervisor_database is not None:
        win.validate_private_path(supervisor_database)
    if desktop_heap_report is not None:
        win.validate_private_path(desktop_heap_report)
    output=Path(output)
    win.validate_private_directory(output.parent)
    output.mkdir(exist_ok=False)
    first=_read_object(heartbeat_path)
    process=psutil.Process(first['pid'])
    if process.username().casefold()!=r'nt authority\system':
        raise ValueError('Observed Warden must actually run as SYSTEM')
    win.validate_code_path(process.exe())
    created_at=process.create_time()
    began=time.time(); monotonic_start=time.monotonic()
    observations=0; initial_handles=None; final_handles=None; maximum_handles=0
    max_rss=0.; max_cpu=0.; heap_available=True; heap_passed=True
    error=None
    process.cpu_percent(interval=None)
    time.sleep(min(interval_seconds,duration_seconds))
    try:
        with (output/'native-samples.jsonl').open('x',encoding='utf-8') as stream:
            while True:
                heartbeat=_read_object(heartbeat_path)
                if (heartbeat.get('instance_id')!=first['instance_id'] or heartbeat.get('pid')!=process.pid
                        or process.create_time()!=created_at):
                    raise RuntimeError('Warden restarted; a new instance cannot complete this continuous run')
                now=time.time()
                if not 0<=now-heartbeat['timestamp']<=15:
                    raise RuntimeError('Actual scheduler progress became stale')
                handles=process.num_handles()
                rss=process.memory_info().rss/1048576
                affinity=process.cpu_affinity()
                cpu=process.cpu_percent(interval=None)/len(affinity)
                heap=desktop_heap_evidence(desktop_heap_report,pid=process.pid,created_at=created_at,now=now)
                heap_available &= heap['available'];heap_passed &= heap['passed']
                if initial_handles is None: initial_handles=handles
                final_handles=handles;maximum_handles=max(maximum_handles,handles)
                max_rss=max(max_rss,rss);max_cpu=max(max_cpu,cpu)
                observation={'timestamp':now,'elapsed_seconds':time.monotonic()-monotonic_start,
                    'pid':process.pid,'handles':handles,'rss_mb':rss,'cpu_percent':cpu,
                    'heartbeat_sequence':heartbeat['sequence'],'desktop_heap':heap}
                stream.write(json.dumps(observation)+'\n');stream.flush();observations+=1
                remaining=duration_seconds-(time.monotonic()-monotonic_start)
                if remaining<=0: break
                time.sleep(min(interval_seconds,remaining))
    except (Exception,KeyboardInterrupt) as exc:
        error=type(exc).__name__+': '+str(exc)
    elapsed=time.monotonic()-monotonic_start
    timing=_supervisor_timing(supervisor_database,began)
    complete=error is None and elapsed>=172800
    result={'schema':1,'kind':'native-windows-sustained-observation','pid':process.pid,
            'process_created_at':created_at,'instance_id':first['instance_id'],
            'started_at':began,'elapsed_seconds':elapsed,'sample_count':observations,
            'continuous_48h_complete':complete,'error':error,
            'handles':{'initial':initial_handles,'final':final_handles,'maximum':maximum_handles,
                       'net_growth':None if initial_handles is None else final_handles-initial_handles,
                       'passed':complete and final_handles<=initial_handles,
                       'scope':'physical handle-count growth; individual handle lifetime attribution is not available'},
            'controller':{'maximum_rss_mb':max_rss,'maximum_cpu_percent':max_cpu,
                          'passed':observations>0 and max_rss<=50 and max_cpu<=5},
            'desktop_heap':{'available':heap_available,'passed':observations>0 and heap_passed},
            'supervisor_timing':timing}
    result['passed']=all((complete,result['handles']['passed'],result['controller']['passed'],
        result['desktop_heap']['passed'],timing['monitor_passed'],timing['recovery_passed']))
    (output/'native-performance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--queue-rounds',type=int)
    parser.add_argument('--queue-concurrency',choices=('threads','processes'),default='threads')
    parser.add_argument('--queue-launch-config', type=Path,
        help='Synthetic four-worker storage profile beside the deployed configured database; not live Windows acceptance')
    parser.add_argument('--queue-workflows', type=int, default=12)
    parser.add_argument('--run-native',action='store_true')
    parser.add_argument('--heartbeat',type=Path)
    parser.add_argument('--duration-seconds',type=float,default=172800)
    parser.add_argument('--interval-seconds',type=float,default=1)
    parser.add_argument('--supervisor-database',type=Path)
    parser.add_argument('--desktop-heap-report',type=Path)
    args=parser.parse_args()
    if args.queue_launch_config is not None and args.queue_rounds is None and not args.run_native:
        from .queue_profile import benchmark_configured_queue
        result = benchmark_configured_queue(args.queue_launch_config, args.output, workflows=args.queue_workflows)
    elif args.queue_rounds is not None and not args.run_native and args.queue_launch_config is None:
        result=benchmark_queue(args.output,rounds=args.queue_rounds,concurrency=args.queue_concurrency)
    elif args.run_native and args.heartbeat is not None and args.queue_rounds is None and args.queue_launch_config is None:
        result=observe_native(args.heartbeat,args.output,duration_seconds=args.duration_seconds,
            interval_seconds=args.interval_seconds,supervisor_database=args.supervisor_database,
            desktop_heap_report=args.desktop_heap_report)
    else:
        parser.error('Select --queue-rounds, --queue-launch-config, or explicit --run-native with --heartbeat')
    print(json.dumps({key:value for key,value in result.items() if key!='samples'},indent=2))
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
