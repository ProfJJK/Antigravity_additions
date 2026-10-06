"""Canonical 4.2.7 execution contracts and controller-collected research.

The owner withdrew phantom seven-stage and agentic Method Matrix requirements.
The actual planning/TDD state machine is enforced with captured source bytes,
controller dispatch records, native receipts, independent audits and Git evidence.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, getproxies, proxy_bypass


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def _text(value,label,maximum=16000):
    if not isinstance(value,str) or not value.strip() or '\x00' in value or len(value)>maximum:
        raise ValueError(label+' requires bounded nonempty text')
    return value


SPECIFICATION_ID = 'COCHEM-4.2.7'
# This executable contract defines dispatch eligibility. Physical evidence and
# independent audits below remain necessary; a schema label cannot prove work.
_DISPATCH_STATES = {
    'CODE_PLAN': ('PLANNING', 'REVISING_PLAN'),
    'CODE_PLAN_REVIEW': ('REVIEWING_PLAN',),
    'CODE_RESEARCH': ('RESEARCHING', 'RESEARCH_REQUIRED'),
    'CODE_TEST_AUTHOR': ('AUTHORING_TESTS', 'EDITING'),
    'CODE_EDIT': ('EDITING', 'IMPROVING', 'REFINING'),
    'CODE_TEST': ('TESTING_PRECODE', 'TESTING', 'FINAL_TESTING'),
    'CODE_REVIEW': ('REVIEWING',),
    'CODE_INTEGRATE': ('STAGING_GIT',),
}
_PREDECESSORS = {
    'CODE_PLAN': ('CODE_PLAN_REVIEW',),
    'CODE_PLAN_REVIEW': ('CODE_PLAN',),
    'CODE_RESEARCH': ('CODE_PLAN_REVIEW', 'CODE_TEST', 'CODE_REVIEW', 'CODE_EDIT', 'CODE_INTEGRATE', 'CODE_RESEARCH'),
    'CODE_TEST_AUTHOR': ('CODE_RESEARCH', 'CODE_TEST'),
    'CODE_EDIT': ('CODE_TEST', 'CODE_REVIEW', 'CODE_EDIT', 'CODE_RESEARCH'),
    'CODE_TEST': ('CODE_TEST_AUTHOR', 'CODE_EDIT', 'CODE_REVIEW'),
    'CODE_REVIEW': ('CODE_TEST',),
    'CODE_INTEGRATE': ('CODE_REVIEW', 'CODE_INTEGRATE'),
}
LEGACY_PHANTOM_HOLDS = frozenset({
    'Production planning requires the canonical seven-stage protocol, Method Matrix M-1..M-8, and registered external research sources',
    'Canonical seven-stage source is registered, but its exact execution transitions have no verified controller binding',
    'Registered protocol source is absent or its captured Git bytes changed',
    'Registered method_matrix source is absent or its captured Git bytes changed',
    'Registered protocol clause is absent from its physical source',
    'Registered method_matrix clause is absent from its physical source',
    'Seven-stage registration must preserve the source order',
})


def execution_contract():
    return {'schema': 'cochem-planning/4.2.7', 'specification_id': SPECIFICATION_ID,
            'specification_path': '4.2.7_SRS.md#chapter-05-coding-execution',
            'dispatch_states': {kind: list(states) for kind, states in _DISPATCH_STATES.items()},
            'predecessor_kinds': {kind: list(kinds) for kind, kinds in _PREDECESSORS.items()},
            'initial_stage': 'CODE_PLAN', 'planning_revision_limit': [1, 10],
            'leaf_cycle_limit': 10, 'methodological_pivot_limit': 3,
            'evidence_gates': ['source-bound-plan', 'independent-plan-audit', 'verified-research',
                'sealed-assertion-red', 'bounded-source-and-test-diff', 'physical-green',
                'independent-file-audits', 'ordered-phase-ledger', 'fenced-git-cas']}


def normalize_policy(raw):
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict) or set(raw) - {'protocol', 'method_matrix', 'research_sources', 'max_revisions'}:
        raise ValueError('Invalid registered planning policy')
    # Owner amendment S427-GOV-001 withdraws the phantom agentic definitions.
    # Preserve them only in historical workflow evidence, never as executable
    # policy or chemical-calculation semantics. Legacy configs still load.
    result = {key: deepcopy(value) for key, value in raw.items() if key not in {'protocol', 'method_matrix'}}
    sources = result.get('research_sources', [])
    if not isinstance(sources, list) or sources and not 2 <= len(sources) <= 8:
        raise ValueError('Register two to eight external research sources when enabled')
    ids = set(); urls = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {'id', 'url'}:
            raise ValueError('Research source registration requires id and exact URL')
        identifier = _text(source['id'], 'source ID', 128)
        url = validate_url(source['url'])
        if identifier in ids or url in urls:
            raise ValueError('Research source registrations must be distinct')
        ids.add(identifier); urls.add(url)
    revisions = result.get('max_revisions', 5)
    if type(revisions) is not int or not 1 <= revisions <= 10:
        raise ValueError('Planning revision budget must be within 1..10')
    if result:
        result['max_revisions'] = revisions
    return result


def validate_url(url):
    _text(url,'research URL',2048)
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.port not in (None,443) or any(ord(c)<33 for c in url)):
        raise ValueError('External research requires literal HTTPS URLs without credentials or redirects')
    if parsed.hostname.casefold() in {'localhost','metadata.google.internal'} or parsed.hostname.endswith('.local'):
        raise ValueError('Research must use public external sources')
    try:
        literal=ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal=None
    if literal is not None and not literal.is_global:
        raise ValueError('Research must use public external sources')
    return url


def validate_registration(policy, files):
    """Bind executable policy and actual captured source; never claim execution."""
    policy = normalize_policy(policy)
    from .coding import manifest
    contract = execution_contract()
    return {'schema': 'planning-source-registration/2', 'specification_id': SPECIFICATION_ID,
            'policy_sha256': digest(policy), 'source_manifest_sha256': digest(manifest(files)),
            'contract_sha256': digest(contract), 'contract': contract}


def planning_readiness(projects):
    records = []
    for project_id, project in sorted(projects.items()):
        policy = normalize_policy(project.planning)
        records.append({'project_id': project_id, 'ready': True,
                        'specification_id': SPECIFICATION_ID,
                        'external_research_required': bool(policy.get('research_sources')),
                        'source_bytes_verified': False,
                        'reason': 'Executable planning contract is installed; submission captures project bytes and execution still requires physical evidence'})
    return {'ready': True, 'required': bool(records), 'projects': records,
            'specification_id': SPECIFICATION_ID, 'contract_sha256': digest(execution_contract()),
            'specification_blockers': []}


def validate_dispatch(transition, kind, payload, *, current_state=None):
    """Check controller dispatch against executable state/phase/parent rules."""
    if (not isinstance(transition, dict) or transition.get('specification_id') != SPECIFICATION_ID
            or transition.get('contract_sha256') != digest(execution_contract())
            or kind not in _DISPATCH_STATES or transition.get('kind') != kind
            or transition.get('from_state') not in _DISPATCH_STATES[kind]
            or type(transition.get('cycle')) is not int or not 1 <= transition['cycle'] <= 10
            or transition.get('cycle') != payload.get('cycle')
            or type(transition.get('leaf_index')) is not int or not 0 <= transition['leaf_index'] < 200
            or transition.get('leaf_index') != payload.get('leaf_index')
            or transition.get('snapshot_sha256') != payload.get('snapshot_sha256')
            or transition.get('phase') != payload.get('phase')
            or transition.get('leaf_id') != payload.get('active_leaf', {}).get('id')):
        raise ValueError('Coding dispatch violates the canonical execution contract')
    if current_state is not None and transition['from_state'] != current_state:
        raise ValueError('Coding completion is detached from its captured execution state')
    state = transition['from_state']; phase = payload.get('phase')
    if kind == 'CODE_TEST' and not (
            state == 'TESTING_PRECODE' and phase == 'precode'
            or state == 'TESTING' and phase in ('postedit', 'final')
            or state == 'FINAL_TESTING' and phase == 'final'):
        raise ValueError('Coding test phase is not permitted in the captured execution state')
    if kind == 'CODE_EDIT' and not (
            state == 'EDITING' and phase in (None, 'P4', 'P7', 'P9')
            or state == 'IMPROVING' and phase == 'P7'
            or state == 'REFINING' and phase == 'P9'):
        raise ValueError('Coding edit phase is not permitted in the captured execution state')
    if kind == 'CODE_RESEARCH' and ((state == 'RESEARCHING') != (payload.get('research_phase') == 'initial')):
        raise ValueError('Coding research phase is not permitted in the captured execution state')
    predecessor = transition.get('predecessor')
    if predecessor is None:
        if kind != 'CODE_PLAN' or state != 'PLANNING':
            raise ValueError('Only initial planning may dispatch without a completed predecessor')
    elif (not isinstance(predecessor, dict) or predecessor.get('kind') not in _PREDECESSORS[kind]
            or not isinstance(predecessor.get('job_id'), str)
            or any(not isinstance(predecessor.get(key), str) or not re.fullmatch('[0-9a-f]{64}', predecessor[key])
                   for key in ('receipt_sha256', 'evidence_sha256'))):
        raise ValueError('Coding dispatch has no permitted completed physical predecessor')
    return transition


def validate_execution_history(jobs, evidence):
    """Join dispatch records to completed controller receipts; no status toggle."""
    stages = [job for job in jobs if job.get('kind') in _DISPATCH_STATES]
    index = {job['job_id']: job for job in stages}
    if len(index) != len(stages) or not stages:
        raise ValueError('Canonical execution requires distinct physical stage jobs')
    roots = []
    for job in stages:
        payload = job.get('payload', {})
        transition = validate_dispatch(payload.get('execution_transition'), job['kind'], payload)
        predecessor = transition['predecessor']
        if predecessor is None:
            roots.append(job['job_id']); continue
        parent = index.get(predecessor['job_id'])
        receipt = (parent or {}).get('receipt') or {}
        receipt_sha = (parent or {}).get('receipt_sha256') or digest(receipt)
        parent_evidence = evidence.get(predecessor['job_id'], {})
        # Authenticated public views intentionally remove lease authority from
        # receipts/Docker evidence. Join their original controller commitments;
        # recompute full private bytes only when the private receipt is present.
        if (parent is None or parent.get('status') != 'COMPLETED' or parent['kind'] != predecessor['kind']
                or receipt_sha != predecessor['receipt_sha256']
                or parent_evidence.get('sha256') != predecessor['evidence_sha256']
                or ('receipt_sha256' not in parent and digest(parent_evidence.get('evidence')) != predecessor['evidence_sha256'])
                or parent.get('updated_at', float('inf')) > job.get('created_at', -1)):
            raise ValueError('Canonical transition is detached from its completed predecessor receipt')
    if len(roots) != 1:
        raise ValueError('Canonical execution requires exactly one initial planning stage')
    # Parent timestamps alone cannot rule out equal-timestamp cycles.
    for job in stages:
        seen = set(); cursor = job
        while cursor['payload']['execution_transition']['predecessor'] is not None:
            if cursor['job_id'] in seen:
                raise ValueError('Canonical execution predecessor graph contains a cycle')
            seen.add(cursor['job_id'])
            cursor = index[cursor['payload']['execution_transition']['predecessor']['job_id']]
    return {'specification_id': SPECIFICATION_ID, 'contract_sha256': digest(execution_contract()),
            'stage_count': len(stages), 'initial_job_id': roots[0]}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise ValueError('Research redirects require a separately registered exact source URL')


def collect_external_sources(policy,*,heartbeat=lambda: True):
    """Fetch bounded source bytes with system TLS trust and inherited proxy policy."""
    policy=normalize_policy(policy)
    if not policy:
        raise ValueError('No external research policy is registered')
    collected=[]; opener=build_opener(_NoRedirect()); deadline=time.monotonic()+30
    for source in policy['research_sources']:
        if not heartbeat():
            raise RuntimeError('Research collection was revoked')
        host=urlsplit(source['url']).hostname
        # An inherited policy proxy owns remote DNS and destination admission.
        # Resolving locally would break managed environments with proxy-only
        # networking; never bypass that proxy or its injected CA trust.
        if not (getproxies().get('https') and not proxy_bypass(host)):
            addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ValueError('Research source resolves outside public address space')
        request=Request(source['url'],headers={'Accept':'text/plain, text/html, application/json','Accept-Encoding':'identity'})
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise TimeoutError('External research collection exceeded its thirty-second budget')
        with opener.open(request,timeout=min(8,remaining)) as response:
            if response.status!=200 or response.geturl()!=source['url']:
                raise ValueError('Research source did not return an exact successful response')
            chunks=[]; size=0
            while size<=65536:
                if time.monotonic()>=deadline or not heartbeat():
                    raise TimeoutError('External research collection exceeded its budget or was revoked')
                chunk=response.read1(min(4096,65537-size))
                if not chunk:
                    break
                chunks.append(chunk); size+=len(chunk)
            raw=b''.join(chunks)
            if not raw or len(raw)>65536:
                raise ValueError('Research response must contain 1..65536 bytes')
            text=raw.decode('utf-8')
            if not text.strip() or '\x00' in text:
                raise ValueError('Research response must contain actual UTF-8 text')
            collected.append({**source,'text':text,'sha256':hashlib.sha256(raw).hexdigest(),
                'byte_count':len(raw),'retrieved_at':time.time(),'http_status':200,'transport':'https'})
    return collected


def validate_external_research(output,sources,requirements):
    """Measure evidence coverage; a model-supplied confidence number has no effect."""
    index={item['id']:item for item in sources}; verified=[]; used=set(); covered=set()
    citations=output.get('external_sources')
    if not isinstance(citations,list) or not 2<=len(citations)<=20:
        raise ValueError('Research requires citations to at least two controller-fetched external sources')
    for citation in citations:
        if not isinstance(citation,dict) or citation.get('source_id') not in index:
            raise ValueError('Research citation is not bound to a registered fetched source')
        source=index[citation['source_id']]; quote=_text(citation.get('quote'),'external quotation')
        text=source.get('text')
        if (not isinstance(text,str) or hashlib.sha256(text.encode()).hexdigest()!=source.get('sha256')
                or source.get('http_status')!=200 or source.get('transport')!='https'
                or len(quote)<12 or quote not in text):
            raise ValueError('Research quotation lacks intact controller-collected HTTPS evidence')
        refs=citation.get('requirement_ids')
        if not isinstance(refs,list) or not refs or any(ref not in requirements for ref in refs):
            raise ValueError('External research must trace declared requirement IDs')
        used.add(source['url']); covered.update(refs)
        verified.append({'source_id':source['id'],'url':source['url'],'source_sha256':source['sha256'],
                         'quote_sha256':hashlib.sha256(quote.encode()).hexdigest(),'requirement_ids':sorted(set(refs))})
    passed=len(used)>=2 and covered==set(requirements)
    confidence={'schema':'research-evidence-confidence/1','score':len(covered)/len(requirements),
                'distinct_sources':len(used),'requirements_covered':sorted(covered),'passed':passed,
                'meaning':'Verified quotation and requirement coverage; not an estimate of scientific correctness'}
    if not passed:
        raise ValueError('Deterministic research confidence gate lacks distinct sources or requirement coverage')
    return {'verified_external_sources':verified,'research_confidence':confidence}
