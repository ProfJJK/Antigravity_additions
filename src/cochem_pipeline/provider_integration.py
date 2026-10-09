"""Evidence-bound pending integration checks, never a provider outage claim.

A hold only prevents native execution. It cannot assert failed authentication,
authorize inference or turn incomplete native contracts into working contracts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat


_FIELDS = {'schema', 'state', 'scope', 'reason', 'evidence_path', 'evidence_sha256',
           'executable_sha256', 'version', 'finding_ids'}
_PREFIX = {'gemini': 'AGY-', 'claude': 'CLAUDE-', 'codex': 'CODEX-'}


class IntegrationVerificationHold(ValueError):
    """The scoped integration is pending; no CLI process should be launched."""
    category = 'compatibility'

    def __init__(self, evidence):
        self.integration_evidence = evidence
        super().__init__('Isolated pipeline integration verification pending: ' + evidence['reason'])


def _physical_bytes(path, maximum, *, retain):
    """Read only bounded ordinary files without redirected ancestry or races."""
    if not path.is_absolute():
        raise ValueError('Integration evidence and executable paths must be absolute')
    for current in (path, *path.parents):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Integration evidence cannot traverse redirected paths')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= maximum:
        raise ValueError('Integration evidence requires a bounded ordinary file')
    digest, blocks, size = hashlib.sha256(), [], 0
    with path.open('rb') as stream:
        while block := stream.read(min(1048576, maximum - size + 1)):
            size += len(block)
            if size > maximum:
                raise ValueError('Integration evidence exceeded its byte bound')
            digest.update(block)
            if retain:
                blocks.append(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (
            after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev):
        raise ValueError('Integration evidence changed during validation')
    return digest.hexdigest(), b''.join(blocks)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate integration evidence JSON member')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Invalid integration evidence JSON constant')
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid bounded integration evidence JSON') from exc


def integration_hold(provider, spec):
    """Validate a deliberate scoped hold; absent means normal full validation.

    A missing *staged target executable* is allowed while held. Its observed
    bytes/version remain bound by the capability report, and an existing target
    must match those bytes. Missing/changed evidence never removes the hold.
    """
    if not isinstance(spec, dict) or 'integration_hold' not in spec:
        return None
    hold = spec['integration_hold']
    if (provider not in _PREFIX or not isinstance(hold, dict) or set(hold) != _FIELDS
            or hold.get('schema') != 'native-integration-hold/1'
            or hold.get('state') != 'verification_pending'
            or hold.get('scope') != 'isolated_pipeline_integration'):
        raise ValueError('Native integration hold requires its exact scoped pending-verification schema')
    for field, maximum in (('reason', 768), ('version', 2048), ('evidence_path', 4096)):
        value = hold[field]
        if (not isinstance(value, str) or not value.strip() or len(value) > maximum
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError('Native integration hold requires a bounded operator reason, version and evidence path')
    for field in ('evidence_sha256', 'executable_sha256'):
        if not isinstance(hold[field], str) or not re.fullmatch('[a-f0-9]{64}', hold[field]):
            raise ValueError('Native integration hold requires exact evidence and executable digests')
    findings = hold['finding_ids']
    if (not isinstance(findings, list) or not 1 <= len(findings) <= 8
            or any(not isinstance(item, str) or not re.fullmatch('[A-Z][A-Z0-9-]{1,63}', item)
                   or not item.startswith(_PREFIX[provider]) for item in findings)
            or len(set(findings)) != len(findings)):
        raise ValueError('Native integration hold requires distinct provider-specific pending findings')
    executable = spec.get('executable')
    if (not isinstance(executable, str) or '\0' in executable
            or not Path(executable).is_absolute()):
        raise ValueError('Native integration hold requires the exact staged executable path')
    digest, raw = _physical_bytes(Path(hold['evidence_path']), 1048576, retain=True)
    if digest != hold['evidence_sha256']:
        raise ValueError('Native integration capability evidence digest changed')
    document = _json(raw)
    if not isinstance(document, dict) or document.get('schema') != 'windows-native-contract-followup/1':
        raise ValueError('Native integration hold requires a recorded Windows capability inspection')
    installed = document.get('installed_executables', {})
    native = installed.get(provider) if isinstance(installed, dict) else None
    if (not isinstance(native, dict) or native.get('sha256') != hold['executable_sha256']
            or native.get('version') != hold['version']):
        raise ValueError('Native integration hold contradicts the observed executable identity')
    pending = document.get('activation_blockers')
    if not isinstance(pending, list) or len(pending) > 64:
        raise ValueError('Native integration capability evidence lacks pending findings')
    for finding in findings:
        matches = [item for item in pending if isinstance(item, dict) and item.get('id') == finding]
        if (len(matches) != 1 or matches[0].get('status') not in
                ('unverified', 'compatibility-hold-before-dispatch')):
            raise ValueError('Native integration hold must reference actual pending verification findings')
    target = Path(executable)
    # is_symlink also detects dangling links; lstat detects reparse points when
    # present. A nonexistent target cannot be launched while this hold exists.
    present = target.exists() or target.is_symlink()
    if present and _physical_bytes(target, 1073741824, retain=False)[0] != hold['executable_sha256']:
        raise ValueError('Staged native executable differs from the bound integration evidence')
    return {'scope': hold['scope'], 'state': hold['state'], 'category': 'compatibility',
            'provider': provider, 'reason': hold['reason'], 'finding_ids': list(findings),
            'evidence_path': hold['evidence_path'], 'evidence_sha256': digest,
            'executable_sha256': hold['executable_sha256'], 'version': hold['version'],
            'staged_executable_present': present}


def require_integration_ready(provider, spec):
    held = integration_hold(provider, spec)
    if held is not None:
        raise IntegrationVerificationHold(held)
