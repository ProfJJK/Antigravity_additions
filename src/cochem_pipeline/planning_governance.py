"""Source-bound planning policy and controller-collected research evidence.

The missing historical stage names and Method Matrix clauses are never invented.
Production projects register their exact source text in the captured Git tree.
Registration proves the specification available, not that its stages executed.
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


def normalize_policy(raw):
    if raw is None or raw=={}:
        return {}
    if not isinstance(raw,dict) or set(raw)-{'protocol','method_matrix','research_sources','max_revisions'}:
        raise ValueError('Invalid registered planning policy')
    result=deepcopy(raw)
    for key,count in (('protocol',7),('method_matrix',8)):
        spec=result.get(key)
        if not isinstance(spec,dict) or set(spec)!={'path','sha256','clauses'}:
            raise ValueError(key+' requires its exact source path, SHA256 and clause quotations')
        from .coding import safe_path
        safe_path(spec['path'])
        if not isinstance(spec['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',spec['sha256']):
            raise ValueError('Planning source requires its full SHA256')
        clauses=spec['clauses']
        if not isinstance(clauses,list) or len(clauses)!=count:
            raise ValueError(key+' requires exactly '+str(count)+' registered clauses')
        seen=set()
        for clause in clauses:
            if not isinstance(clause,dict) or set(clause)!={'id','quote'}:
                raise ValueError('Planning clauses require identifiers and verbatim source quotations')
            identifier=_text(clause['id'],'clause ID',128)
            if identifier in seen or len(_text(clause['quote'],'clause quotation'))<12:
                raise ValueError('Planning clauses must be distinct and substantive')
            seen.add(identifier)
        if key=='method_matrix' and seen!={f'M-{number}' for number in range(1,9)}:
            raise ValueError('Method Matrix requires the actual M-1 through M-8 definitions')
    sources=result.get('research_sources')
    if not isinstance(sources,list) or not 2<=len(sources)<=8:
        raise ValueError('Register two to eight external research sources')
    ids=set(); urls=set()
    for source in sources:
        if not isinstance(source,dict) or set(source)!={'id','url'}:
            raise ValueError('Research source registration requires id and exact URL')
        identifier=_text(source['id'],'source ID',128)
        url=validate_url(source['url'])
        if identifier in ids or url in urls:
            raise ValueError('Research source registrations must be distinct')
        ids.add(identifier); urls.add(url)
    revisions=result.get('max_revisions',5)
    if type(revisions) is not int or not 1<=revisions<=10:
        raise ValueError('Planning revision budget must be within 1..10')
    result['max_revisions']=revisions
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


def validate_registration(policy,files):
    policy=normalize_policy(policy)
    if not policy:
        raise ValueError('Production planning requires the canonical seven-stage protocol, Method Matrix M-1..M-8, and registered external research sources')
    documents={}
    for key in ('protocol','method_matrix'):
        spec=policy[key]; raw=files.get(spec['path'])
        if not isinstance(raw,bytes) or hashlib.sha256(raw).hexdigest()!=spec['sha256']:
            raise ValueError('Registered '+key+' source is absent or its captured Git bytes changed')
        text=raw.decode('utf-8')
        positions=[]
        for clause in spec['clauses']:
            if clause['quote'] not in text:
                raise ValueError('Registered '+key+' clause is absent from its physical source')
            positions.append(text.index(clause['quote']))
        if key=='protocol' and positions!=sorted(set(positions)):
            raise ValueError('Seven-stage registration must preserve the source order')
        documents[key]=deepcopy(spec)
    return {'schema':'planning-source-registration/1','policy_sha256':digest(policy),
            'documents':documents,'registration_verified':True,'stage_execution_verified':False}


def seven_stage_execution_binding():
    """No controller binding exists until the canonical stage definitions arrive.

    A source registration or caller-supplied boolean cannot install executable
    transition semantics. This must be replaced by the verified implementation,
    not by a configuration switch or a model assertion.
    """
    return None


def planning_readiness(projects):
    """Report the missing production transition binding without inferring execution.

    This inexpensive diagnostic only inspects configuration. Submission checks
    source bytes against the actual captured Git baseline before recording a hold.
    """
    records=[]
    for project_id,project in sorted(projects.items()):
        registered=bool(project.planning)
        records.append({'project_id':project_id,'ready':False,
            'source_registration_configured':registered,'source_bytes_verified':False,
            'stage_execution_verified':False,
            'reason':('Canonical seven-stage source is registered, but its exact execution transitions have no verified controller binding'
                      if registered else 'Production planning requires the canonical seven-stage protocol, Method Matrix M-1..M-8, and registered external research sources')})
    return {'ready':not records,'required':bool(records),'projects':records,
            'stage_execution_verified':False,'specification_blockers':[
                'Canonical v2/task_planning_orchestra.py seven-stage definitions are unavailable',
                'Canonical Method Matrix M-1 through M-8 definitions are unavailable']}


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


def method_matrix_artifact(links,registration,artifacts,requirements):
    if not isinstance(links,list) or len(links)!=8:
        raise ValueError('Planning must link all eight registered Method Matrix clauses')
    expected={clause['id'] for clause in registration['documents']['method_matrix']['clauses']}; seen=set()
    for item in links:
        if not isinstance(item,dict) or item.get('id') not in expected or item['id'] in seen:
            raise ValueError('Method Matrix linkage is missing or duplicates a registered clause')
        paths=item.get('artifacts'); refs=item.get('requirement_ids')
        if (not isinstance(paths,list) or not paths or any(path not in artifacts for path in paths)
                or not isinstance(refs,list) or not refs or any(ref not in requirements for ref in refs)):
            raise ValueError('Method Matrix evidence must bind actual plan artifacts and requirement IDs')
        seen.add(item['id'])
    return {'source':registration['documents']['method_matrix'],'links':deepcopy(links),
            'linked_artifact_hashes':{name:hashlib.sha256(artifacts[name].encode()).hexdigest()
                                    for item in links for name in item['artifacts']}}
