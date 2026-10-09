"""Bounded owner-account CLI status capture; no free-form/model prompt input."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from datetime import datetime, timezone

EXE = Path(r'C:\Users\ansac\AppData\Local\agy\bin\agy.exe')
EXPECTED = '38f30c7dd1ed808f5cf98fe2014de3d30903035a4f0df02d3eb72a9ff8993741'
STAGE = Path(r'C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006')
if hashlib.sha256(EXE.read_bytes()).hexdigest() != EXPECTED:
    raise SystemExit('Installed Agy differs from the observed 1.3.1 binary.')
directory = STAGE / ('agy-status-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
directory.mkdir()
env = dict(os.environ)
for key in list(env):
    upper = key.upper()
    if upper.startswith(('OPENAI_', 'ANTHROPIC_', 'AZURE_OPENAI_', 'GEMINI_',
                         'GOOGLE_API_', 'GOOGLE_GENAI_', 'GOOGLE_CLOUD_',
                         'CLOUDSDK_AUTH_', 'GCLOUD_', 'BEDROCK_', 'VERTEX_')) or upper in {
        'CODEX_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS', 'AWS_BEARER_TOKEN_BEDROCK',
    }:
        env.pop(key)
results = []
for probe_name, arguments in (('version', ['--version']), ('help', ['--help']),
                              ('model', ['-p', '/model']), ('usage', ['-p', '/usage'])):
    before_hash = hashlib.sha256(EXE.read_bytes()).hexdigest()
    if before_hash != EXPECTED:
        raise SystemExit('Native executable changed before probe: ' + probe_name)
    argv = [str(EXE), *arguments]
    start = time.monotonic()
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=directory, env=env,
                               shell=False, close_fds=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    buffers = [bytearray(), bytearray()]
    failures = []

    def drain(pipe, index):
        try:
            while chunk := pipe.read(4096):
                remaining = 131072 - len(buffers[index])
                buffers[index].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    failures.append('output_limit')
                    process.kill()
                    break
        except OSError:
            failures.append('pipe_read_error')
        finally:
            pipe.close()

    readers = [threading.Thread(target=drain, args=(pipe, index), daemon=True)
               for index, pipe in enumerate((process.stdout, process.stderr))]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        failures.append('timeout')
        subprocess.run([r'C:\Windows\System32\taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
        process.wait(timeout=2)
    finally:
        for reader in readers:
            reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            failures.append('pipe_cleanup_unconfirmed')
        elif process.returncode is not None:
            process._handle.Close()
    after_hash = hashlib.sha256(EXE.read_bytes()).hexdigest()
    if after_hash != before_hash:
        failures.append('executable_changed_during_probe')
    entry = dict(probe=probe_name, argv=argv, exit_code=process.returncode, pid=process.pid,
                 executable_sha256_before=before_hash, executable_sha256_after=after_hash,
                 elapsed_seconds=round(time.monotonic()-start, 3), failures=failures)
    for index, stream_name in enumerate(('stdout', 'stderr')):
        raw = bytes(buffers[index])
        output = directory / (probe_name + '.' + stream_name + '.txt')
        with output.open('xb') as stream:
            stream.write(raw)
        entry[stream_name] = dict(path=str(output), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    results.append(entry)
    if failures:
        break
report = dict(schema='cochem-agy-owner-status-capture/1',
              observed_at_utc=datetime.now(timezone.utc).isoformat(),
              scope='Owner-account documented standalone CLI slash reports; no natural-language model prompt',
              executable_sha256=EXPECTED, operator_account=os.environ.get('USERNAME'),
              isolated_worker_account=False, authentication_asserted=False,
              official_contract='https://www.antigravity.google/docs/cli/headless/',
              environment_api_overrides_removed_in_child_only=True,
              native_cache_refresh_not_excluded=True, results=results)
path = directory / 'capture.json'
with path.open('x', encoding='utf-8') as stream:
    json.dump(report, stream, indent=2)
print(json.dumps(dict(report_path=str(path), probes=[{k:v for k,v in row.items() if k not in ('stdout','stderr')}
                                                   for row in results])))
