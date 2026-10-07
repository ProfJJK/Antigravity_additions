"""Physical child RSS/file-handle growth and persistent native process identity."""
import json
import os
import subprocess
import sys

import pytest

from cochem_supervisor.process_observer import ProcessObserver


PROGRAM='''import os,sys,tempfile
blocks=[]; handles=[]
print(os.getpid(),flush=True)
for line in sys.stdin:
    if line.strip()=='grow':
        blocks.append(bytearray(8*1024*1024))
        handles.extend(tempfile.TemporaryFile() for _ in range(8))
    print('ready',flush=True)
'''


def test_real_process_memory_and_handle_slopes_survive_observer_reload(tmp_path):
    child=subprocess.Popen([sys.executable,'-I','-c',PROGRAM],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        pid=int(child.stdout.readline())
        state=tmp_path/'history.json'
        arguments={'minimum_window_seconds':1,'rss_growth_bytes':4*1024*1024,
                   'rss_rate_bytes':1024,'handle_growth':8,'handle_rate':1}
        observer=ProcessObserver(state,**arguments)
        first=observer.observe(pid,now=1000)
        for timestamp in (1001,1002):
            child.stdin.write('grow\n');child.stdin.flush()
            assert child.stdout.readline().strip()=='ready'
            result=ProcessObserver(state,**arguments).observe(pid,now=timestamp)
        assert {item['metric'] for item in result['alarms']}=={'rss_bytes','handles'}
        process=result['processes'][0]
        assert process['created_at']==first['processes'][0]['created_at']
        assert process['slopes']['rss_bytes']['growth']>=12*1024*1024
        assert process['slopes']['handles']['growth']>=16
        assert process['handle_metric']==('windows_handles' if os.name=='nt' else 'posix_file_descriptors')
        child.stdin.close();child.wait(timeout=5)
        ended=observer.observe(pid,now=1003)
        assert not ended['processes'] and ended['unavailable']
    finally:
        if child.poll() is None: child.kill();child.wait(timeout=5)


def test_pid_reuse_cannot_inherit_another_process_history(tmp_path):
    import psutil
    process=psutil.Process()
    old_created=process.create_time()-100
    history={'schema':1,'processes':{f'{process.pid}:{old_created:.6f}':{
        'pid':process.pid,'created_at':old_created,'samples':[
            {'at':1,'rss_bytes':1,'handles':1},{'at':2,'rss_bytes':2,'handles':2}]}}}
    state=tmp_path/'history.json';state.write_text(json.dumps(history))
    observed=ProcessObserver(state).observe(process.pid,now=3)
    assert observed['processes'][0]['slopes']['rss_bytes']['sample_count']==1
    assert not observed['alarms']


def test_short_burst_is_not_a_sustained_leak_and_history_is_bounded(tmp_path):
    observer=ProcessObserver(tmp_path/'history.json')
    for at in range(10): result=observer.observe(os.getpid(),now=1000+at)
    assert not result['alarms']
    assert all(len(entry['samples'])<=8 for entry in json.loads(observer.path.read_text())['processes'].values())
    with pytest.raises(ValueError,match='backwards'):
        observer.observe(os.getpid(),now=999)


def test_fast_polling_keeps_a_complete_sustained_window(tmp_path):
    observer=ProcessObserver(tmp_path/'history.json')
    for at in range(41): result=observer.observe(os.getpid(),now=1000+at)
    assert result['processes'][0]['slopes']['rss_bytes']['window_seconds']>=30
    assert result['processes'][0]['slopes']['rss_bytes']['sample_count']<=8
