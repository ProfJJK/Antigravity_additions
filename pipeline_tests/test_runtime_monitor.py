"""Real Linux filesystem/process contracts; Windows Job Objects remain host tests."""
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest
from watchdog.observers import Observer

from cochem_pipeline.oracle import ContextEngine, Oracle
from cochem_pipeline.runtime import BASELINE, SlotMonitor, clear_slot


@pytest.mark.skipif(os.name=='nt',reason='Linux watchdog/process contract; Windows isolation has separate SYSTEM tests')
def test_real_watchdog_storm_reaps_physical_process(tmp_path):
    workspace=tmp_path/'slot'
    workspace.mkdir()
    reaped=threading.Event()
    process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    def terminate(task_id):
        assert task_id=='actual-filesystem-task'
        process.terminate()
        process.wait(timeout=5)
        reaped.set()
    oracle=Oracle(ContextEngine(BASELINE,16384,.25),tmp_path/'private'/'tracking.db',terminate)
    observer=Observer()
    try:
        observer.schedule(SlotMonitor(oracle,'actual-filesystem-task'),str(workspace),recursive=True)
        observer.start()
        for index in range(600):
            (workspace/f'event-{index}').write_bytes(b'actual filesystem event')
        assert reaped.wait(5),'The real filesystem event storm must trigger asynchronous reaping'
        assert process.poll() is not None
        assert 'actual-filesystem-task' in oracle.tripped_tasks
        assert oracle.drain()==[]
    finally:
        observer.stop()
        observer.join(timeout=5)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        oracle.close()


@pytest.mark.skipif(os.name=='nt',reason='POSIX symlink check; Windows reparse-point checks need the actual host')
def test_cleanup_preserves_outside_target_of_worker_link(tmp_path):
    outside=tmp_path/'outside'
    outside.mkdir()
    (outside/'retained').write_text('must survive',encoding='utf-8')
    slot=tmp_path/'slot'
    slot.mkdir()
    (slot/'outside-link').symlink_to(outside,target_is_directory=True)
    (slot/'nested').mkdir()
    (slot/'nested'/'discard').write_text('worker output',encoding='utf-8')
    clear_slot(slot)
    assert list(slot.iterdir())==[]
    assert (outside/'retained').read_text(encoding='utf-8')=='must survive'
