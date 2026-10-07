"""Real kernel pipes and processes; native SYSTEM checks remain opt-in."""
import hashlib
import os
import subprocess
import sys
import threading
import time

import pytest

from cochem_pipeline.transport import PromptPipe, MAX_PROMPT_BYTES


pytestmark = pytest.mark.skipif(os.name == 'nt' and os.environ.get('COCHEM_SYSTEM_TESTS') != '1',
    reason='Windows named pipe tests require the actual SYSTEM controller identity')


def test_complete_utf8_prompt_larger_than_pipe_buffer_and_eof():
    payload = ('<oracle>αβ energy ΔG λ 中文</oracle>\n' * 40000).encode()
    with PromptPipe(payload) as pipe:
        child = subprocess.Popen([sys.executable, '-I', '-c',
            'import sys,hashlib; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'],
            stdin=pipe.reader, stdout=subprocess.PIPE)
        try:
            pipe.start()
            stdout, _ = child.communicate(timeout=10)
            assert child.returncode == 0
            pipe.verify_delivered()
            assert stdout.strip().decode() == hashlib.sha256(payload).hexdigest()
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
    assert not any(t.name == 'native-stdin' for t in threading.enumerate())


def test_nonreading_child_cancellation_never_blocks_controller():
    started = time.monotonic()
    with PromptPipe(b'x' * MAX_PROMPT_BYTES) as pipe:
        child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(30)'],
                                 stdin=pipe.reader)
        try:
            pipe.start()
            time.sleep(.1)
            pipe.close()
            assert not pipe.delivered
            assert time.monotonic() - started < 3
        finally:
            child.kill()
            child.wait(timeout=5)
    assert not any(t.name == 'native-stdin' for t in threading.enumerate())


def test_early_child_exit_does_not_count_as_prompt_delivery():
    with PromptPipe(b'x' * MAX_PROMPT_BYTES) as pipe:
        child = subprocess.Popen([sys.executable, '-I', '-c', 'pass'], stdin=pipe.reader)
        child.wait(timeout=5)
        pipe.start()
        with pytest.raises(BrokenPipeError):
            pipe.verify_delivered()


def test_unstarted_pipe_closes_both_endpoints_and_rejects_unbounded_input():
    with PromptPipe(b'') as pipe:
        descriptor = pipe.reader.fileno()
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert pipe._writer is None
    with pytest.raises(ValueError, match='bounded'):
        PromptPipe(b'x' * (MAX_PROMPT_BYTES + 1))
