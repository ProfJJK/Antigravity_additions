"""Real subprocess output limits; these tests do not attest Windows isolation."""
import json
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from cochem_pipeline.containers import _bounded_process
from cochem_pipeline.worker import NativeRunner, NativeOutputLimitError, _read_native_output


class PhysicalChild:
    """Own an actual test process; production uses WindowsProcess Job Objects."""
    def __init__(self, process):
        self.process = process
        self.pid = process.pid
        self.closed = False

    def poll(self):
        return self.process.poll()

    def wait(self, timeout=None):
        return self.process.wait(timeout=timeout)

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        self.closed = True


@pytest.mark.parametrize('finish_first', [False, True])
def test_actual_child_combined_output_overflow_is_resource_failure_and_closes_process(finish_first):
    runner = NativeRunner(SimpleNamespace(lease_seconds=30))
    with tempfile.TemporaryFile('w+b') as out, tempfile.TemporaryFile('w+b') as err:
        script = 'import os,time; os.write(1,b"o"*40000); os.write(2,b"e"*40000)'
        if not finish_first:
            script += '; time.sleep(30)'
        child = PhysicalChild(subprocess.Popen([sys.executable,'-I','-c',script],stdout=out,stderr=err))
        if finish_first:
            child.process.wait(timeout=5)
        with pytest.raises(NativeOutputLimitError) as caught:
            with runner._managed('output-overflow', child):
                runner._wait(child,lambda:True,5,None,output_streams=(out,err),output_limit_bytes=65536)
        assert caught.value.category == 'resource'
        assert caught.value.hold_scope is None
        assert caught.value.retry_after_seconds == 30
        assert child.closed and child.process.poll() is not None
        assert 'output-overflow' not in runner._active
        with pytest.raises(NativeOutputLimitError):
            _read_native_output(out,err,65536)


def test_bounded_output_reader_preserves_exact_limit_and_rejects_combined_excess():
    with tempfile.TemporaryFile('w+b') as out, tempfile.TemporaryFile('w+b') as err:
        out.write(b'out');err.write(b'err');out.flush();err.flush()
        assert _read_native_output(out,err,6) == (b'out',b'err')
        err.seek(0,2);err.write(b'!');err.flush()
        with pytest.raises(NativeOutputLimitError):
            _read_native_output(out,err,6)


def test_real_docker_client_launcher_preserves_literal_argv_and_bounds_output():
    literal = 'spaces ; $(printf should-not-run) & |'
    result = _bounded_process([sys.executable,'-I','-c',
        'import json,sys; print(json.dumps(sys.argv[1:]))',literal],env=dict(os.environ),
        timeout=5,output_limit=4096)
    assert result.returncode == 0
    assert json.loads(result.stdout) == [literal]
    overflow = _bounded_process([sys.executable,'-I','-c',
        'import os,time; os.write(1,b"x"*131072); time.sleep(30)'],env=dict(os.environ),
        timeout=5,output_limit=4096)
    assert overflow.output_exceeded
    assert len(overflow.stdout) == 4096
    assert not overflow.timed_out


@pytest.mark.skipif(os.name != 'nt',reason='Real Windows console allocation requires a native Windows process')
def test_native_docker_client_launch_has_no_console_window():
    result = _bounded_process([sys.executable,'-I','-c',
        'import ctypes; print(ctypes.WinDLL("kernel32").GetConsoleWindow())'],
        env=dict(os.environ),timeout=5,output_limit=4096)
    assert result.returncode == 0
    assert result.stdout.strip() == b'0'
