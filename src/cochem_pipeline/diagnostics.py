"""Bounded repair evidence derived from controller-observed results.

This is a projection for a prompt, never replacement evidence. The complete
receipt remains immutable in SQLite. UTF-8 byte length is used as a conservative
token upper bound; no unmeasured native tokenizer count is claimed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

_DIAGNOSTIC_FIELDS = ('test_receipt','last_test','failures','previous_failure','research_dossier',
                      'minor_findings','repair_findings','findings','debug_notes','diagnostics',
                      'operator_resolution','strategy','root_cause','hypothesis')
MAX_DIAGNOSTIC_BYTES = 1900


def _encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _clip(value, limit):
    return str(value).encode('utf-8')[:limit].decode('utf-8','ignore')


def bounded_coding_diagnostics(payload: dict) -> dict:
    """Preserve task authority, replace unbounded diagnostics by a <2000-byte projection.

    The diagnostic object alone is byte bounded: objectives, requirement IDs,
    selected paths and evidence hashes are retained verbatim. The caller's
    separate context budget governs the rest of the task prompt.
    """
    if not isinstance(payload,dict):
        raise ValueError('Coding payload must be an object')
    result = deepcopy(payload)
    sources = {key:result.pop(key) for key in _DIAGNOSTIC_FIELDS if key in result}
    if not sources:
        return result
    evidence = {'schema':1,'source_sha256':_hash(sources),'sources':{},'excerpts':[]}
    excerpts = []
    for name,value in sources.items():
        record = {'sha256':_hash(value)}
        if name in ('test_receipt','last_test') and isinstance(value,dict):
            record.update(passed=value.get('passed'),failure_category=value.get('failure_category'))
            for command in value.get('commands',[])[:4]:
                if not isinstance(command,dict):
                    continue
                item = {key:command.get(key) for key in ('name','exit_code','junit') if key in command}
                # Exact source substrings, including native escaped newlines,
                # can be checked against the retained original receipt.
                for key in ('junit_cases','stderr','stdout'):
                    if command.get(key):
                        source = json.dumps(command[key],ensure_ascii=False,sort_keys=True)
                        excerpts.append({'source':name,'field':key,'text':source[:800]})
                record.setdefault('commands',[]).append(item)
        elif name == 'failures' and isinstance(value,list):
            record['failure_count'] = len(value)
            excerpts.extend({'source':name,'field':'reason','text':str(item.get('reason',''))}
                            for item in value[-3:] if isinstance(item,dict))
        elif value:
            excerpts.append({'source':name,'field':'value','text':json.dumps(value,ensure_ascii=False,sort_keys=True)})
        evidence['sources'][name] = record
    # Bound summaries too: even operator command names / JUnit structures are
    # not allowed to grow the prompt through arbitrarily long nested fields.
    if len(_encoded(evidence)) > 1200:
        evidence['sources'] = {name:{'sha256':_hash(value)} for name,value in sources.items()}
    for item in excerpts:
        remaining = MAX_DIAGNOSTIC_BYTES - len(_encoded(evidence)) - 150
        if remaining < 80:
            break
        bounded = {**item,'text':_clip(item['text'],min(600,remaining))}
        evidence['excerpts'].append(bounded)
        while len(_encoded(evidence)) >= MAX_DIAGNOSTIC_BYTES-70:
            bounded['text'] = _clip(bounded['text'],max(0,len(bounded['text'].encode())-64))
            if not bounded['text']:
                evidence['excerpts'].pop()
                break
    evidence['truncated'] = True
    evidence['utf8_bytes'] = 0
    for _ in range(4):
        evidence['utf8_bytes'] = len(_encoded(evidence))
    if evidence['utf8_bytes'] >= 2000:
        raise ValueError('Bounded diagnostics exceeded the strict repair evidence budget')
    result['diagnostics'] = evidence
    return result
