"""Bounded numeric native metadata; never persist arbitrary provider fields."""
from __future__ import annotations


_COUNTERS = {
    'codex': {
        'input_tokens': 'input_tokens', 'output_tokens': 'output_tokens',
        'cached_input_tokens': 'cached_input_tokens',
    },
    'claude': {
        'input_tokens': 'input_tokens', 'output_tokens': 'output_tokens',
        'cache_read_input_tokens': 'cached_input_tokens',
        'cache_creation_input_tokens': 'cache_creation_input_tokens',
    },
    'gemini': {
        'input_tokens': 'input_tokens', 'output_tokens': 'output_tokens',
        'input': 'input_tokens', 'output': 'output_tokens', 'prompt': 'prompt_tokens',
        'candidates': 'output_tokens', 'total': 'total_tokens',
        'cached': 'cached_input_tokens', 'thoughts': 'reasoning_tokens',
        'thinking_tokens': 'reasoning_tokens', 'cache_read_tokens': 'cached_input_tokens',
        'total_tokens': 'total_tokens',
        'tool': 'tool_tokens',
    },
}
_FIELDS = ('input_tokens', 'output_tokens', 'cached_input_tokens',
           'cache_creation_input_tokens', 'prompt_tokens', 'total_tokens',
           'reasoning_tokens', 'tool_tokens')


def native_usage(provider: str, reported: object) -> dict:
    """Project native counters only; missing/invalid values are never free usage.

    Counts retain provider semantics. In particular Claude cache creation/read
    and Gemini prompt/input counts are not summed or turned into a currency
    estimate. Unknown keys (including account data) cannot escape this projection.
    """
    if provider not in _COUNTERS:
        raise ValueError('Unsupported native usage provider')
    source = reported if isinstance(reported, dict) else {}
    values = {field: None for field in _FIELDS}
    reasons = {field: 'not_reported' for field in _FIELDS}
    native_fields = {}
    for key, field in _COUNTERS[provider].items():
        if key not in source:
            continue
        value = source[key]
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            values[field] = None
            reasons[field] = 'invalid_reported_value'
            continue
        native_fields[key] = value
        if reasons.get(field) in ('invalid_reported_value', 'conflicting_reported_values'):
            continue
        if values[field] is not None and values[field] != value:
            values[field] = None
            reasons[field] = 'conflicting_reported_values'
            continue
        values[field] = value
        reasons.pop(field, None)
    return {'schema': 'native-usage/1', 'source': 'native_cli', 'provider': provider,
            'reported': bool(native_fields), 'native_fields': native_fields,
            **values, 'unavailable': reasons, 'currency_cost': None,
            'currency_cost_unavailable': 'subscription_currency_not_reported'}
