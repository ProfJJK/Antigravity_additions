"""Typed native CLI failures, without trusting generated prose as availability.

Only native error envelopes can create provider holds. Explicit machine codes
take precedence; tightly anchored native diagnostics cover CLI versions whose
error envelopes omit those codes. Generated answers are never diagnoses.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any


_SUMMARIES = {
    'resource': 'Host or container resource pressure requires controlled retry',
    'quota': 'Provider quota or rate limit reached',
    'auth': 'Subscription authentication failed',
    'busy': 'Provider model is temporarily busy',
    'context': 'Provider model context limit reached',
    'provider': 'Provider service unavailable',
    'timeout': 'Native request or execution deadline exceeded',
    'protocol': 'Unknown native CLI argument or output protocol',
    'code': 'Native CLI execution failed; protected logs require diagnosis',
    'configuration': 'Selected captured model is disabled by current operator configuration',
    'compatibility': 'Installed native CLI rejected the exact requested model configuration',
}
_DEFAULT_SCOPE = {
    'quota': 'pool', 'auth': 'pool', 'busy': 'model',
    'provider': 'model',
    'configuration': 'job', 'compatibility': 'model',
}
_MAX_RETRY_AFTER = 86400.0


def _bounded_retry(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return min(_MAX_RETRY_AFTER, max(0.0, number)) if math.isfinite(number) else None


class ProviderFailure(RuntimeError):
    """A fixed, persistable diagnosis; raw CLI output is never exception text."""

    def __init__(self, category: str, retry_after_seconds: float | None = None,
                 hold_scope: str | None = None):
        if category not in _SUMMARIES:
            raise ValueError('Unknown provider failure category')
        if hold_scope not in (None, 'pool', 'model', 'job'):
            raise ValueError('Unknown provider failure hold scope')
        if category in ('code', 'protocol', 'timeout', 'context') and hold_scope is not None:
            raise ValueError('Execution, protocol and context failures cannot hold a provider globally')
        if category == 'configuration' and hold_scope not in (None,'job'):
            raise ValueError('Configuration requires an explicit job hold')
        if category == 'compatibility' and hold_scope not in (None,'model','job'):
            raise ValueError('Compatibility requires a model or explicit job hold')
        self.category = category
        self.summary = _SUMMARIES[category]
        self.hold_scope = hold_scope or _DEFAULT_SCOPE.get(category)
        self.retry_after_seconds = _bounded_retry(retry_after_seconds)
        super().__init__(self.summary)


_CODES = {
    'quota': {
        'rate_limit', 'rate_limited', 'rate_limit_error', 'rate_limit_exceeded',
        'quota_exceeded', 'insufficient_quota', 'resource_exhausted',
        'too_many_requests', 'usage_limit_reached', 'usage_limit_exceeded',
    },
    'auth': {
        'authentication_error', 'authentication_failed', 'unauthorized',
        'unauthenticated', 'invalid_api_key', 'invalid_authentication',
        'invalid_token', 'token_expired', 'not_authenticated', 'auth_required',
        'login_required',
    },
    'busy': {
        'overloaded', 'overloaded_error', 'server_overloaded', 'server_busy',
        'busy', 'capacity_exceeded', 'model_overloaded', 'unavailable',
    },
    'context': {
        'context_length_exceeded', 'context_window_exceeded',
        'max_context_length_exceeded', 'context_limit_exceeded',
        'prompt_too_long', 'input_too_long',
    },
    'provider': {
        'api_error', 'server_error', 'internal_server_error', 'internal_error',
        'internal', 'service_unavailable', 'bad_gateway', 'gateway_timeout',
    },
    'compatibility': {'model_not_found', 'unknown_model', 'unsupported_model', 'model_not_supported',
                      'requested_model_not_found', 'model_not_available'},
    'protocol': {
        'invalid_request_error', 'invalid_request', 'invalid_argument',
        'unsupported_protocol', 'invalid_cli_argument',
    },
    'timeout': {
        'timeout', 'request_timeout', 'connection_timeout', 'deadline_exceeded',
        'request_timed_out', 'etimedout',
    },
}
_HTTP_CODES = {401: 'auth', 408: 'timeout', 429: 'quota', 500: 'provider', 502: 'provider',
               503: 'busy', 504: 'provider', 529: 'busy'}
_CODE_FIELDS = ('code', 'type', 'status', 'status_code', 'http_status',
                'http_status_code', 'error_type')


def _category(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if type(value) is int:
        return _HTTP_CODES.get(value)
    if not isinstance(value, str):
        return None
    token = value.strip().casefold()
    if token.isascii() and token.isdecimal():
        # Limit the conversion too: an untrusted native stream can contain a
        # string with thousands of digits, which Python deliberately rejects.
        return _HTTP_CODES.get(int(token)) if len(token) == 3 else None
    return next((category for category, codes in _CODES.items() if token in codes), None)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate native JSON member')
        result[key] = value
    return result


def _records(raw: str) -> list[dict[str, Any]]:
    """Decode complete JSON/JSONL; never extract JSON from surrounding prose."""
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (ValueError, RecursionError):
        values = []
        for line in raw.splitlines():
            try:
                value = json.loads(line, object_pairs_hook=_strict_object)
            except (ValueError, RecursionError):
                continue
            if isinstance(value,dict):
                values.append(value)
        return values
    return [value] if isinstance(value, dict) else []


def _error_fields(provider: str, record: dict[str, Any]) -> tuple[list[dict[str, Any]], str] | None:
    """Select native error containers, excluding model/tool/result content."""
    kind = record.get('type')
    error = record.get('error')
    if provider == 'codex':
        if kind not in ('error', 'turn.failed'):
            return None
        if isinstance(error, dict):
            return [error, record], 'code' if error else 'protocol'
        if kind == 'error' and isinstance(record.get('message'), str):
            return [record], 'code'
        return [record], 'protocol'
    if provider == 'claude':
        subtype = record.get('subtype')
        if kind == 'result' and (record.get('is_error') is True or
                                isinstance(subtype, str) and subtype.startswith('error_')):
            errors = record.get('errors')
            fields = [value for value in errors if isinstance(value, dict)] if isinstance(errors, list) else []
            if isinstance(error, dict):
                fields.append(error)
            return [*fields, record], 'code'
        if kind == 'error' and isinstance(error, dict):
            return [error, record], 'code' if error else 'protocol'
        return None
    # Gemini's JSON output reports the native error as a top-level error object.
    # Other typed events (messages, tool output, successful result records) do
    # not become failures merely because their payload contains an error field.
    if kind not in (None, 'error', 'result') or not isinstance(error, dict):
        return None
    if kind == 'result' and not (record.get('is_error') is True or
                               isinstance(record.get('subtype'), str) and record['subtype'].startswith('error_')):
        return None
    return [error, record], 'code' if error else 'protocol'


_NATIVE_DIAGNOSTICS = (
    ('auth', r'^(?:not logged in\b|please (?:log|sign) in\b|authentication (?:failed|required)\b|unauthorized\b|your (?:session|authentication token) has expired\b|your authentication token could not be refreshed\b)'),
    ('quota', r'^(?:you(?:[\x27\u2019]ve| have) hit your (?:usage )?limit\b|rate limit (?:reached|exceeded)\b|usage limit (?:reached|exceeded)\b|credit balance is too low\b)'),
    ('context', r'^(?:your input exceeds the context window\b|the input exceeds the context window\b|prompt is too long\b|input is too long\b|context (?:window|length|limit) exceeded\b)'),
    ('busy', r'^(?:the model is currently overloaded\b|the server is overloaded\b|model is temporarily unavailable\b|this model is currently at capacity\b|server is busy\b)'),
)


def _native_diagnostic_category(provider: str, record: dict, fields: list[dict]) -> str | None:
    # These fields are selected only after establishing a native failure event.
    # In particular, Claude result text and Codex agent_message.text stay data.
    messages = [field.get('message') for field in fields]
    if provider == 'claude' and record.get('type') == 'result' and isinstance(record.get('errors'),list):
        messages.extend(record['errors'])
    for message in messages:
        if not isinstance(message,str):
            continue
        message = message[:4096].strip().casefold()
        for category, pattern in _NATIVE_DIAGNOSTICS:
            if re.match(pattern,message):
                return category
    return None


def _retry_after(fields: list[dict[str, Any]]) -> float | None:
    hints: list[float] = []
    for field in fields:
        for key in ('retry_after_seconds', 'retry_after', 'retryAfter', 'retryAfterSeconds'):
            value = _bounded_retry(field.get(key))
            if value is not None:
                hints.append(value)
        headers = field.get('headers')
        if isinstance(headers, dict):
            for key, raw in headers.items():
                if key.casefold() == 'retry-after':
                    value = _bounded_retry(raw)
                    if value is not None:
                        hints.append(value)
        details = field.get('details')
        if isinstance(details, list):
            for detail in details:
                if not isinstance(detail, dict) or detail.get('@type') != 'type.googleapis.com/google.rpc.RetryInfo':
                    continue
                raw = detail.get('retryDelay')
                if isinstance(raw, str) and re.fullmatch(r'\d+(?:\.\d+)?s', raw):
                    value = _bounded_retry(raw[:-1])
                    if value is not None:
                        hints.append(value)
    return max(hints, default=None)


def parse_native_failure(provider: str, stdout: str, stderr: str,
                         exit_code: int) -> ProviderFailure | None:
    """Read recognized native failure envelopes from either CLI output stream.

    A nonzero exit alone is insufficient to diagnose provider availability.
    Failed terminal records remain failures even if the process exits zero.
    Retry hints are finite seconds, bounded to one day. Known anchored native
    error diagnostics are accepted only inside failure envelopes; generated
    answers, nested tool results and arbitrary textual stderr are excluded.
    """
    if provider not in ('codex', 'claude', 'gemini'):
        raise ValueError('Unsupported native provider')
    failures: list[ProviderFailure] = []
    for record in [*_records(stdout), *_records(stderr)]:
        selected = _error_fields(provider, record)
        if selected is None:
            continue
        fields, fallback = selected
        categories = [_category(field.get(key)) for field in fields for key in _CODE_FIELDS]
        # Prefer a specific typed cause to a generic API/server classification.
        category = next((candidate for candidate in ('auth', 'quota', 'context', 'busy', 'timeout', 'provider', 'compatibility', 'protocol')
                         if candidate in categories), None)
        if category is None:
            category = _native_diagnostic_category(provider,record,fields) or fallback
        retry_after = _retry_after(fields) if category in _DEFAULT_SCOPE else None
        failures.append(ProviderFailure(category, retry_after))
    if not failures:
        return None
    priority = {'auth': 0, 'quota': 1, 'context': 2, 'busy': 3, 'timeout': 4, 'provider': 5, 'compatibility': 6, 'protocol': 7, 'code': 8}
    failure = min(failures, key=lambda item: priority[item.category])
    matching_hints = [item.retry_after_seconds for item in failures
                      if item.category == failure.category and item.retry_after_seconds is not None]
    return ProviderFailure(failure.category, max(matching_hints, default=None))


def native_failure_summary(stderr: str) -> str:
    """Legacy safe diagnostic only; never use this prose to decide a hold."""
    text=stderr[:16384].casefold()
    patterns=(
        ('Subscription authentication failed',r'not logged in|unauthorized|authentication|expired token|\b401\b'),
        ('Provider quota or rate limit reached',r'quota|rate.?limit|usage limit|credit balance|\b429\b'),
        ('Provider service unavailable',r'service unavailable|bad gateway|connection (?:reset|refused)|\b50[234]\b'),
        ('Unknown native CLI argument or output protocol',r'(?:unknown|unrecognized|unexpected|invalid).{0,40}(?:argument|option|flag|protocol)'),
        ('Host resources exhausted: out of memory or disk full',r'out of memory|cannot allocate memory|no space left|disk full'),
        ('Native CLI permission denied',r'permission denied|access denied'),
    )
    for summary,pattern in patterns:
        if re.search(pattern,text):
            return summary
    return 'Unclassified native CLI failure; protected logs require diagnosis'
