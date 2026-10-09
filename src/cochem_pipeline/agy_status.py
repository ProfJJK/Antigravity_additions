"""Agy's observed tab-separated status reports, never authentication evidence.

This module launches nothing. It parses the standalone ``-p /model`` and
``-p /usage`` reports for operator diagnostics only. Native Agy quota groups
are not the pipeline's Claude/Codex subscription pools, and selected model
configuration is not an actual serving-model receipt.
"""
from __future__ import annotations

from datetime import datetime
import re


SCHEMA = 'cochem-agy-status-diagnostic/1'
REVIEWED_SHA256 = '38f30c7dd1ed808f5cf98fe2014de3d30903035a4f0df02d3eb72a9ff8993741'
_MAX_BYTES = 16384
_WINDOWS = {'Weekly Limit Remaining': 'weekly', 'Five Hour Limit Remaining': 'five_hour'}
_POOLS = {'Gemini Models', 'Claude and GPT models'}


class AgyStatusError(ValueError):
    """Output does not match the reviewed status format; do not infer readiness."""


def _lines(stdout: str, stderr: str, exit_code: int) -> list[str]:
    if type(exit_code) is not int or exit_code != 0:
        raise AgyStatusError('Agy status command did not exit successfully')
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise AgyStatusError('Agy status streams must be decoded text')
    try:
        size = len(stdout.encode('utf-8')) + len(stderr.encode('utf-8'))
    except UnicodeError:
        raise AgyStatusError('Agy status contains invalid Unicode') from None
    if size > _MAX_BYTES:
        raise AgyStatusError('Agy status exceeds its bounded format')
    if stderr:
        # There is no reviewed success-with-warning contract. A diagnostic,
        # authentication prompt or updater notice cannot silently pass here.
        raise AgyStatusError('Agy status stderr needs review')
    if any((ord(char) < 32 and char not in '\r\n\t') or ord(char) == 127 for char in stdout):
        raise AgyStatusError('Agy status contains unsupported control characters')
    normalized = stdout.replace('\r\n', '\n')
    if '\r' in normalized:
        raise AgyStatusError('Agy status contains unsupported line endings')
    if normalized.endswith('\n'):
        normalized = normalized[:-1]
    lines = normalized.split('\n')
    if not lines or any(not line for line in lines):
        raise AgyStatusError('Agy status is empty or contains blank records')
    return lines


def _utc(value: str, *, exact_seconds: bool = False) -> datetime:
    pattern = r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
    if not exact_seconds:
        pattern += r'(?:\.\d{1,6})?'
    pattern += 'Z'
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise AgyStatusError('Agy status time must be a UTC ISO timestamp')
    try:
        return datetime.fromisoformat(value[:-1] + '+00:00')
    except ValueError:
        raise AgyStatusError('Agy status time is not a valid calendar instant') from None


def parse_selected_model(stdout: str, stderr: str = '', exit_code: int = 0) -> dict:
    """Read selected configuration only; never remap its slug to a routed ID."""
    lines = _lines(stdout, stderr, exit_code)
    if len(lines) != 1 or len(fields := lines[0].split('\t')) != 2:
        raise AgyStatusError('Agy selected-model status requires one two-column record')
    slug, label = fields
    if not re.fullmatch(r'[a-z0-9][a-z0-9._/-]{0,127}', slug):
        raise AgyStatusError('Agy selected model has an unsupported identifier')
    if not 1 <= len(label) <= 128 or label != label.strip() or not all(32 <= ord(c) < 127 for c in label):
        raise AgyStatusError('Agy selected model has an unsupported display label')
    return {'model_id': slug, 'display_name': label, 'evidence_kind': 'selected_configuration'}


def parse_quota_table(stdout: str, *, observed_at_utc: str, stderr: str = '', exit_code: int = 0) -> list[dict]:
    """Preserve zero, disabled, and reset-time observations without admission claims."""
    observed = _utc(observed_at_utc)
    lines = _lines(stdout, stderr, exit_code)
    if not 1 <= len(lines) <= 4:
        raise AgyStatusError('Agy quota status has an unsupported record count')
    result, seen = [], set()
    for line in lines:
        fields = line.split('\t')
        if len(fields) != 4:
            raise AgyStatusError('Agy quota status requires four tab-separated columns')
        pool, window_label, remaining, reset = fields
        if pool not in _POOLS or window_label not in _WINDOWS:
            raise AgyStatusError('Agy quota status contains an unreviewed pool or window')
        window = _WINDOWS[window_label]
        if (pool, window) in seen:
            raise AgyStatusError('Agy quota status repeats a pool/window record')
        seen.add((pool, window))
        if remaining == 'disabled':
            if reset:
                raise AgyStatusError('Disabled Agy quota unexpectedly reports a reset')
            enabled, percent, reset_at, relation = False, None, None, 'not_applicable'
        else:
            if not re.fullmatch(r'(?:0|[1-9][0-9]?|100)%', remaining):
                raise AgyStatusError('Agy quota percentage is outside its reviewed format')
            reset_instant = _utc(reset, exact_seconds=True)
            enabled, percent, reset_at = True, int(remaining[:-1]), reset
            relation = 'future' if reset_instant > observed else 'due_or_past'
        result.append({'native_group': pool, 'window': window, 'enabled': enabled,
                       'remaining_percent': percent, 'resets_at_utc': reset_at,
                       'reset_relation_at_capture': relation})
    return result


def summarize_status(*, model_stdout: str, usage_stdout: str, observed_at_utc: str,
                     executable_sha256_before: str, executable_sha256_after: str,
                     version: str, model_stderr: str = '', usage_stderr: str = '',
                     model_exit_code: int = 0, usage_exit_code: int = 0) -> dict:
    """Describe stable captured output without activating any provider contract.

    The caller must supply hashes measured around these commands. A changed
    executable (including self-update) invalidates the combined version binding.
    This check describes caller-supplied provenance; it does not measure a file
    or certify a worker identity.
    """
    _utc(observed_at_utc)
    for digest in (executable_sha256_before, executable_sha256_after):
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise AgyStatusError('Agy diagnostic requires measured executable SHA-256 values')
    if executable_sha256_before != executable_sha256_after:
        raise AgyStatusError('Agy executable changed during status capture; recapture stable evidence')
    if executable_sha256_before != REVIEWED_SHA256:
        raise AgyStatusError('Agy status binary is outside the reviewed Windows capture')
    # Only the freshly observed Windows format is reviewed. Other versions need
    # their own capture; a public-doc example alone cannot extend this binding.
    if version != '1.3.1':
        raise AgyStatusError('Agy diagnostic requires the reviewed 1.3.1 status format')
    return {
        'schema': SCHEMA,
        'observed_at_utc': observed_at_utc,
        'executable_sha256': executable_sha256_before,
        'version': version,
        'selected_model': parse_selected_model(model_stdout, model_stderr, model_exit_code),
        'native_quotas': parse_quota_table(usage_stdout, observed_at_utc=observed_at_utc,
                                         stderr=usage_stderr, exit_code=usage_exit_code),
        'subscription_authentication': 'unverified',
        'subscription_verified': False,
        'isolated_worker_verified': False,
        'actual_serving_model': None,
        'reported_effort': None,
        'inference_readiness': 'not_established',
        'routing_or_provider_contract_changed': False,
    }
