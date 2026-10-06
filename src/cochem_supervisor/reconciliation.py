"""Protected SRS/WBS reconciliation for automatic repair promotion.

Reads candidate bytes as data only. This module does not import candidate code,
launch a model, claim a route or grant promotion. The supervisor owns those
operations and must recheck the frozen candidate after reviewing it.
"""
from __future__ import annotations

from copy import deepcopy
import difflib
import hashlib
import json
from pathlib import Path
import re

from .releases import _plain_ancestors, _stat_plain, tree_manifest, validate_changes


_SHA=re.compile(r'[0-9a-f]{64}')
_ID=re.compile(r'\*\*(S427-[A-Z]+-\d{3}):\*\*')
_MAX_ASSET=2*1024*1024
_MAX_SOURCE=512*1024


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
        ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _bytes(path, maximum):
    path=Path(path)
    _plain_ancestors(path)
    metadata=_stat_plain(path)
    if metadata.st_size>maximum:
        raise ValueError('Reconciliation source exceeds its bounded capture')
    raw=path.read_bytes()
    after=_stat_plain(path)
    if ((metadata.st_dev,metadata.st_ino,metadata.st_size,metadata.st_mtime_ns)!=
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or len(raw)>maximum):
        raise ValueError('Reconciliation source changed while being captured')
    return raw


def _specification(root):
    root=Path(root).absolute()
    candidates=(root,root/'src/cochem_pipeline/specification',root/'cochem_pipeline/specification',root/'specification')
    folder=next((candidate for candidate in candidates if (candidate/'4.2.7_SRS.md').is_file()),None)
    if folder is None:
        raise ValueError('Repair approval requires the protected installed canonical SRS assets')
    paths={name:folder/name for name in ('4.2.7_SRS.md','SRS_ADDENDUM_4.2.7.md','requirements_4.2.7.json')}
    # A protected source checkout stores its addendum/catalog under docs.
    for name in ('SRS_ADDENDUM_4.2.7.md','requirements_4.2.7.json'):
        if not paths[name].is_file():
            paths[name]=folder/'docs'/name
    raw={name:_bytes(path,_MAX_ASSET) for name,path in paths.items()}
    hashes={name:hashlib.sha256(value).hexdigest() for name,value in raw.items()}
    ledger=json.loads(raw['requirements_4.2.7.json'])
    rows=ledger.get('requirements')
    normative=_ID.findall(raw['4.2.7_SRS.md'].decode('utf-8'))
    if (ledger.get('specification_sha256')!=hashes['4.2.7_SRS.md']
            or not isinstance(rows,list) or not rows or len(normative)!=len(set(normative))
            or any(not isinstance(row,dict) or not isinstance(row.get('id'),str)
                   or not isinstance(row.get('chapter'),str) or not isinstance(row.get('requirement'),str)
                   or not row['requirement'].strip() for row in rows)
            or len({row['id'] for row in rows})!=len(rows)
            or {row['id'] for row in rows}!=set(normative)):
        raise ValueError('Protected repair SRS and complete requirement catalog hashes/coverage disagree')
    # The catalog is an index, never an independent authority for clause text.
    # Extract exact canonical prose to prevent a stale catalog wording shortcut.
    clauses={}
    for line in raw['4.2.7_SRS.md'].decode('utf-8').splitlines():
        match=_ID.search(line)
        if match:
            clauses[match.group(1)]=line[match.end():].strip()
    for row in rows:
        if row['requirement']!=clauses[row['id']]:
            raise ValueError('Protected repair catalog wording differs from canonical SRS bytes')
    return ledger,rows,hashes,raw['SRS_ADDENDUM_4.2.7.md'].decode('utf-8')


def build_review_manifest(specification_root, baseline_root, candidate_root, *, incident,
                          allowed_paths, test_evidence, outer_evidence):
    """Capture all governing clauses and a deterministic repair WBS, no omission."""
    ledger,requirements,hashes,amendment=_specification(specification_root)
    if (not isinstance(incident,dict) or not isinstance(incident.get('fingerprint'),str)
            or not incident['fingerprint'] or len(incident['fingerprint'])>256):
        raise ValueError('Repair reconciliation requires a concrete incident identity')
    if (not isinstance(test_evidence,dict) or type(test_evidence.get('passed')) is not int
            or test_evidence['passed']<=0 or test_evidence.get('failures')!=0 or test_evidence.get('errors')!=0
            or not isinstance(outer_evidence,dict) or outer_evidence.get('passed') is not True):
        raise ValueError('Repair review requires passing protected regression and outer acceptance evidence')
    baseline=tree_manifest(Path(baseline_root))
    changed=validate_changes(baseline,Path(candidate_root),allowed_paths)
    if len(changed['changed'])>32:
        raise ValueError('Repair reconciliation requires at most thirty-two bounded changed files')
    files=[];used=0
    for name in changed['changed']:
        before=_bytes(Path(baseline_root)/name,_MAX_SOURCE) if name in baseline else b''
        after=_bytes(Path(candidate_root)/name,_MAX_SOURCE) if name in changed['manifest'] else b''
        used+=len(before)+len(after)
        if used>_MAX_SOURCE:
            raise ValueError('Repair review source context exceeds its combined byte budget; further fracture is required')
        if (hashlib.sha256(before).hexdigest()!=baseline.get(name,hashlib.sha256(b'').hexdigest())
                or hashlib.sha256(after).hexdigest()!=changed['manifest'].get(name,hashlib.sha256(b'').hexdigest())):
            raise ValueError('Repair candidate or baseline drifted after its tree commitment')
        before_text=before.decode('utf-8');after_text=after.decode('utf-8')
        patch=''.join(difflib.unified_diff(before_text.splitlines(True),after_text.splitlines(True),
                      fromfile='a/'+name,tofile='b/'+name))
        files.append({'path':name,'before_sha256':hashlib.sha256(before).hexdigest(),
            'after_sha256':hashlib.sha256(after).hexdigest(),'diff_sha256':hashlib.sha256(patch.encode()).hexdigest(),
            'before_text':before_text,'after_text':after_text,'patch':patch})
    refs=[row['id'] for row in requirements]
    wbs=[{'id':f'REPAIR_{index:03d}','file_target':item['path'],
          'objective':'Reconcile this exact bounded repair with every governing requirement; preserve unaffected behavior.',
          'requirement_ids':refs,'before_sha256':item['before_sha256'],'after_sha256':item['after_sha256'],
          'diff_sha256':item['diff_sha256']} for index,item in enumerate(files,1)]
    commitments={**hashes,'baseline_tree':digest(baseline),'candidate_tree':digest(changed['manifest']),
        'incident':digest(incident),'regression_evidence':digest(test_evidence),
        'outer_acceptance_evidence':digest(outer_evidence),'repair_wbs':digest(wbs)}
    return {'schema':'repair-srs-wbs-reconciliation/1','specification_id':ledger['specification_id'],
            'specification_revision':ledger['revision'],'artifact_hashes':commitments,
            'incident_id':incident['fingerprint'],'scope':'all canonical requirements; no model-selected omissions',
            'requirements':[{'id':row['id'],'chapter':row['chapter'],'text':row['requirement']} for row in requirements],
            'owner_amendment_text':amendment,'wbs':wbs,'files':files,
            'tests':{key:test_evidence[key] for key in ('passed','failures','errors','skipped','tests') if key in test_evidence},
            'outer_acceptance':{'passed':True,'evidence_sha256':commitments['outer_acceptance_evidence']},
            'physical_source_bytes':used,'maximum_source_bytes':_MAX_SOURCE}


def review_output_contract(manifest):
    wbs=[row['id'] for row in manifest['wbs']]
    return {'verdict':'PASS','manifest_sha256':digest(manifest),
        'artifact_hashes':deepcopy(manifest['artifact_hashes']),
        'requirements_checked':[{'requirement_id':row['id'],'chapter':row['chapter'],'wbs_ids':wbs,
            'status':'SATISFIED','rationale':'Concrete changed-code or preserved-behavior evidence for this requirement.'}
            for row in manifest['requirements']], 'findings':[]}


def validate_review(output, manifest, receipt, producer_receipt):
    """Check controller commitments and native asymmetry; a PASS word cannot pass."""
    if (not isinstance(output,dict) or output.get('verdict') not in {'PASS','FAIL'}
            or output.get('manifest_sha256')!=digest(manifest)
            or output.get('artifact_hashes')!=manifest['artifact_hashes']):
        raise ValueError('Repair approval is detached from exact SRS/WBS/source/test commitments')
    for native in (receipt,producer_receipt):
        if (not isinstance(native,dict) or native.get('provider') not in {'codex','claude','gemini'}
                or not isinstance(native.get('requested_model'),str) or not native['requested_model']
                or native.get('reported_model') not in (None,native['requested_model'])
                or native.get('subscription_verified') is not True or native.get('terminal_success') is not True
                or type(native.get('exit_code')) is not int or native['exit_code']!=0
                or type(native.get('pid')) is not int or not 0<native['pid']<=0xffffffff
                or not isinstance(native.get('session_id'),str) or not native['session_id']
                or not _SHA.fullmatch(native.get('stdout_sha256',''))):
            raise ValueError('Repair approval requires successful native producer and reviewer receipts')
    if receipt['provider']==producer_receipt['provider']:
        raise ValueError('Repair final approval requires a reviewer from a different provider')
    if receipt.get('review_output_sha256')!=digest(output):
        raise ValueError('Repair review output is not bound to its native receipt')
    rows=output.get('requirements_checked')
    requirements={row['id']:row for row in manifest['requirements']}
    if (not isinstance(rows,list) or len(rows)!=len(requirements)
            or any(not isinstance(row,dict) or row.get('requirement_id') not in requirements for row in rows)
            or len({row['requirement_id'] for row in rows})!=len(rows)):
        raise ValueError('Repair approval must explicitly reconcile every canonical requirement')
    expected_wbs=[row['id'] for row in manifest['wbs']]
    for row in rows:
        if (row.get('chapter')!=requirements[row['requirement_id']]['chapter'] or row.get('wbs_ids')!=expected_wbs
                or row.get('status') not in {'SATISFIED','UNCHANGED','DIVERGED'}
                or not isinstance(row.get('rationale'),str) or not 12<=len(row['rationale'].strip())<=4000):
            raise ValueError('Repair reconciliation must join concrete rationale to each exact SRS chapter and WBS task')
    findings=output.get('findings')
    if (not isinstance(findings,list) or len(findings)>200 or
            any(not isinstance(item,str) or not 12<=len(item.strip())<=4000 for item in findings)):
        raise ValueError('Repair review findings require bounded concrete text')
    approved=output['verdict']=='PASS'
    if approved and (findings or any(row['status']=='DIVERGED' for row in rows)):
        raise ValueError('Unresolved SRS/WBS divergence prevents repair approval')
    if not approved and not findings:
        raise ValueError('Rejected repair review requires actionable divergence findings')
    return {'schema':'repair-srs-wbs-reconciliation-verification/1','approved':approved,
            'manifest_sha256':digest(manifest),'output_sha256':digest(output),
            'producer_receipt_sha256':digest(producer_receipt),'reviewer_receipt_sha256':digest(receipt),
            'reviewer_provider':receipt['provider'],'reviewer_model':receipt['requested_model'],
            'requirements_checked':[row['requirement_id'] for row in rows],
            'artifact_hashes':deepcopy(manifest['artifact_hashes'])}
