"""Validate and summarize an existing private Agy status capture; launches nothing."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import stat
import sys


def _read(path: Path, limit: int) -> bytes:
    info = path.stat(follow_symlinks=False)
    if (not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or info.st_nlink != 1 or info.st_size > limit):
        raise ValueError('Capture source must be a bounded ordinary file')
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError('Capture source exceeded its bounded read')
    return content


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('Capture contains duplicate JSON members')
        value[key] = item
    return value


def _nonfinite(_):
    raise ValueError('Nonfinite capture JSON')


def parse_capture(filename: Path) -> dict:
    from cochem_pipeline.agy_status import REVIEWED_SHA256, summarize_status

    filename = filename.absolute()
    if any(parent.is_symlink() or getattr(parent.stat(follow_symlinks=False), 'st_file_attributes', 0) & 0x400
           for parent in filename.parents):
        raise ValueError('Capture directory must not redirect through a symlink')
    raw = _read(filename, 65536)
    capture = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    if (not isinstance(capture, dict) or capture.get('schema') != 'cochem-agy-owner-status-capture/1'
            or capture.get('isolated_worker_account') is not False
            or capture.get('authentication_asserted') is not False
            or capture.get('executable_sha256') != REVIEWED_SHA256):
        raise ValueError('Capture is not reviewed owner-scope status evidence')
    expected_args = {'version': ['--version'], 'help': ['--help'], 'model': ['-p', '/model'], 'usage': ['-p', '/usage']}
    results = capture.get('results')
    if not isinstance(results, list) or len(results) != 4:
        raise ValueError('Capture requires version, help, model and usage observations')
    records, streams, measured = {}, {}, []
    executable = None
    for result in results:
        if not isinstance(result, dict):
            raise ValueError('Capture observation must be an object')
        probe = result.get('probe')
        if probe not in expected_args or probe in records:
            raise ValueError('Capture has missing, duplicate or unsupported probes')
        records[probe] = result
        if (result.get('failures') != [] or type(result.get('exit_code')) is not int or result['exit_code'] != 0
                or result.get('executable_sha256_before') != REVIEWED_SHA256
                or result.get('executable_sha256_after') != REVIEWED_SHA256):
            raise ValueError('Capture probe failed or changed executable during execution')
        argv = result.get('argv')
        if (not isinstance(argv, list) or len(argv) != 1 + len(expected_args[probe])
                or not isinstance(argv[0], str) or not PureWindowsPath(argv[0]).is_absolute()
                or argv[1:] != expected_args[probe] or (executable is not None and executable != argv[0])):
            raise ValueError('Capture argv does not identify the standalone reviewed status command')
        executable = argv[0]
        streams[probe] = {}
        item = {'probe': probe, 'argv_suffix': expected_args[probe], 'exit_code': 0,
                'executable_sha256_before': REVIEWED_SHA256, 'executable_sha256_after': REVIEWED_SHA256}
        for stream in ('stdout', 'stderr'):
            spec = result.get(stream)
            target = filename.parent / f'{probe}.{stream}.txt'
            if (not isinstance(spec, dict) or spec.get('path') != str(target)
                    or type(spec.get('bytes')) is not int or not 0 <= spec['bytes'] <= 16384
                    or not isinstance(spec.get('sha256'), str) or not re.fullmatch('[a-f0-9]{64}', spec['sha256'])):
                raise ValueError('Capture stream must identify its bounded sibling file')
            content = _read(target, 16384)
            digest = hashlib.sha256(content).hexdigest()
            if len(content) != spec['bytes'] or digest != spec['sha256']:
                raise ValueError('Capture stream bytes no longer match their recorded digest')
            streams[probe][stream] = content.decode('utf-8')
            item[stream] = {'bytes': len(content), 'sha256': digest}
        measured.append(item)
    if streams['version']['stderr'] or streams['version']['stdout'].strip() != '1.3.1':
        raise ValueError('Capture version output does not verify the reviewed binary version')
    stamp = datetime.fromisoformat(capture['observed_at_utc'].replace('Z', '+00:00'))
    if stamp.utcoffset() != timezone.utc.utcoffset(stamp):
        raise ValueError('Capture observation timestamp must explicitly use UTC')
    observed = stamp.isoformat().replace('+00:00', 'Z')
    summary = summarize_status(model_stdout=streams['model']['stdout'], model_stderr=streams['model']['stderr'],
        usage_stdout=streams['usage']['stdout'], usage_stderr=streams['usage']['stderr'], observed_at_utc=observed,
        executable_sha256_before=REVIEWED_SHA256, executable_sha256_after=REVIEWED_SHA256, version='1.3.1')
    return summary | {'scope': 'owner_account_status_only', 'capture_sha256': hashlib.sha256(raw).hexdigest(),
                      'probe_evidence': measured, 'native_model_jobs_executed': 0,
                      'parser_launches_processes': False, 'native_cache_refresh_not_excluded': True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    arguments = parser.parse_args()
    try:
        output = json.dumps(parse_capture(arguments.capture), indent=2, allow_nan=False) + '\n'
        if arguments.output:
            with arguments.output.open('x', encoding='utf-8') as stream:
                stream.write(output)
        else:
            print(output, end='')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'Cannot validate Agy status capture: {exc}', file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == '__main__':
    main()
