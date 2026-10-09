"""Strict output and protected-bundle checks for the optional Windows CPU probe.

The legacy LibreHardwareMonitor WMI provider remains supported by telemetry.
The current 0.9.6 release instead exposes the library used by this one-shot
probe. Neither adapter may turn an unavailable sensor into a zero reading.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re
import stat


def validate_probe_path(value: str | None) -> None:
    if value is None:
        return
    if (not isinstance(value, str) or not 1 <= len(value) <= 1024
            or any(char in value for char in ('\x00', '"', '\n', '\r'))
            or not re.match(r'^[A-Za-z]:[\\/]', value)
            or ':' in value[2:] or '..' in PureWindowsPath(value).parts
            or PureWindowsPath(value).suffix.casefold() != '.exe'):
        raise ValueError('cpu_temperature_probe must be one absolute local Windows .exe path or null')


def parse_cpu_temperature(raw: bytes, nonce: str, now: float) -> float:
    """Validate a bounded fresh process response; pure, independently testable."""
    if not isinstance(raw, bytes) or len(raw) > 32768:
        raise ValueError('CPU probe output is invalid or exceeds its bound')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('CPU probe JSON has duplicate fields')
            result[key] = value
        return result
    record = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique)
    required = {'schema', 'provider', 'library_version', 'nonce', 'sampled_at_unix_ms', 'sensors'}
    if (not isinstance(record, dict) or set(record) != required
            or record['schema'] != 'cochem-cpu-temperature/1'
            or record['provider'] != 'LibreHardwareMonitorLib'
            or record['library_version'] != '0.9.6.0' or record['nonce'] != nonce
            or not re.fullmatch('[0-9a-f]{32}', nonce)):
        raise ValueError('CPU probe identity or schema does not match')
    measured = record['sampled_at_unix_ms']
    if (type(measured) is not int or not 0 <= measured <= 10**15 or not math.isfinite(now)
            or not -1 <= now - measured / 1000 <= 5):
        raise ValueError('CPU probe measurement is stale or from the future')
    sensors = record['sensors']
    if not isinstance(sensors, list) or not 1 <= len(sensors) <= 256:
        raise ValueError('CPU probe must return bounded nonempty sensors')
    values, identities = [], set()
    for sensor in sensors:
        if not isinstance(sensor, dict) or set(sensor) != {'identifier', 'name', 'celsius'}:
            raise ValueError('CPU sensor fields are invalid')
        identifier, value = sensor['identifier'], sensor['celsius']
        if (not isinstance(identifier, str) or len(identifier) > 256
                or not re.fullmatch(r'/intelcpu/[0-9]+/temperature/[0-9]+', identifier)
                or identifier in identities):
            raise ValueError('CPU sensor identity is invalid or duplicated')
        name = sensor['name']
        if (not isinstance(name, str) or len(name) > 128
                or (name != 'CPU Package' and not re.fullmatch(r'(CPU Core|P-Core|E-Core|Core) #[0-9]+', name))):
            raise ValueError('CPU sensor is not an absolute direct core/package reading')
        if type(value) not in (int, float) or not -50 <= value <= 150 or not math.isfinite(value):
            raise ValueError('CPU sensor temperature is invalid')
        identities.add(identifier)
        values.append(float(value))
    return max(values)


def verify_probe_bundle(executable: Path) -> Path:
    """Reject writable code/dependencies, extra files, links and changed bytes.

    ACL validation requires actual SYSTEM; no portable or fixture bypass exists.
    The manifest is inside the same protected versioned directory as the code.
    """
    from .windows import WindowsIsolationError
    try:
        return _verify_probe_bundle(executable)
    except WindowsIsolationError as error:
        raise OSError('CPU probe requires protected SYSTEM code and dependencies: ' + str(error)) from error


def _verify_probe_bundle(executable: Path) -> Path:
    from .windows import validate_code_path
    validate_probe_path(str(executable))
    # Reject original aliases, then return the exact canonical path whose
    # protected ancestry we check. Never execute a mutable user-side junction.
    canonical = executable.resolve(strict=True)
    if str(executable).casefold() != str(canonical).casefold():
        raise ValueError('CPU probe executable must use its canonical protected path')
    for path in (executable, *executable.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('CPU probe path ancestors must not be links/reparse points')
    executable = canonical
    validate_code_path(executable)
    root = executable.parent
    manifest_path = root / 'cochem-cpu-temperature.manifest.json'
    validate_code_path(manifest_path)
    manifest_info = manifest_path.lstat()
    if (not stat.S_ISREG(manifest_info.st_mode) or manifest_info.st_nlink != 1
            or getattr(manifest_info, 'st_file_attributes', 0) & 0x400
            or manifest_info.st_size > 65536):
        raise ValueError('CPU probe manifest must be a bounded regular file without alternate links')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or manifest.get('schema') != 'cochem-cpu-probe-bundle/1'
            or manifest.get('entrypoint') != executable.name):
        raise ValueError('CPU probe bundle schema or entrypoint differs')
    expected = manifest.get('files')
    if not isinstance(expected, dict) or not 1 <= len(expected) <= 128:
        raise ValueError('CPU probe bundle inventory is invalid')
    actual = {}
    for index, path in enumerate(root.rglob('*')):
        if index >= 256:
            raise ValueError('CPU probe bundle exceeds its entry bound')
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('CPU probe bundle must not contain links/reparse points')
        if path.is_dir():
            validate_code_path(path)
        elif path != manifest_path:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 32 * 1024 * 1024:
                raise ValueError('CPU probe bundle contains an invalid file')
            validate_code_path(path)
            actual[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            if len(actual) > 128:
                raise ValueError('CPU probe bundle exceeds its file bound')
    if actual != expected or executable.name not in actual:
        raise ValueError('CPU probe bundle contents differ from the protected manifest')
    return executable
