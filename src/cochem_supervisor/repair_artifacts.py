"""Controller-owned source capture and bounded application of repair proposals."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .releases import (_plain_ancestors, _stat_plain, _safe_relative,
                       _scan, _read_file, MAX_FILES, MAX_BYTES, ReleaseError)


MAX_SOURCE_BYTES=512*1024
MAX_PROPOSAL_BYTES=2*1024*1024


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':'),
                                    ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _in_scope(name, scope):
    return any(name==allowed or allowed.endswith('/') and name.startswith(allowed) for allowed in scope)


def _inventory(root, scope):
    directories=[]
    scanned=_scan(root,max_files=MAX_FILES,max_bytes=MAX_BYTES,directory_names=directories)
    inventory={name:_read_file(path,metadata) for name,path,metadata in scanned if _in_scope(name,scope)}
    # Include existing empty directories: creating a module must not grant
    # authority to create a new package/directory or use a later injected one.
    parents=sorted(name for name in directories if _in_scope(name+'/',scope)
                   or any(not allowed.endswith('/') and str(Path(allowed).parent).replace('\\','/')==name
                          for allowed in scope))
    return inventory,parents


def source_packet(workspace, allowed_paths, diagnostic):
    from .runner import _allowlist
    scope=_allowlist(allowed_paths)
    root=Path(workspace).absolute()
    _plain_ancestors(root)
    _stat_plain(root,directory=True)
    inventory,parents=_inventory(root,scope)
    names={name for name in inventory if name.endswith('.py')}
    text=json.dumps(diagnostic,ensure_ascii=False)
    ordered=sorted(names,key=lambda name:(name not in text and Path(name).name not in text,name))
    files={}; omitted=[]; used=0
    for name in ordered:
        path=root/name
        _plain_ancestors(path.parent);_stat_plain(path)
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=inventory[name]:
            raise ValueError('Repair source changed during source context capture')
        if len(data)>MAX_SOURCE_BYTES or used+len(data)>MAX_SOURCE_BYTES:
            omitted.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
            continue
        content=data.decode('utf-8')
        files[name]={'text':content,'sha256':hashlib.sha256(data).hexdigest()}
        used+=len(data)
    return {'files':files,'omitted':omitted,'captured_bytes':used,'maximum_bytes':MAX_SOURCE_BYTES,
            'allowed_paths':list(scope),'inventory_complete':True,
            'baseline_inventory':inventory,'baseline_directories':parents,
            'baseline_inventory_sha256':_digest({'files':inventory,'directories':parents,'allowed_paths':list(scope)}),
            'selection':'Diagnostic-referenced files first, then lexical paths; omitted existing files may not be changed. '
                        'New Python modules require proved inventory absence and an existing captured allowlisted parent.'}


def parse_proposal(content):
    from cochem_pipeline.worker import strict_json
    if not isinstance(content,str) or len(content.encode())>MAX_PROPOSAL_BYTES:
        raise ValueError('Repair proposal exceeds its bounded output contract')
    value=strict_json(content)
    if (not isinstance(value,dict) or set(value)!={'summary','files'} or
        not isinstance(value['summary'],str) or not value['summary'].strip() or len(value['summary'])>16000 or
        not isinstance(value['files'],dict) or not 1<=len(value['files'])<=32 or
        any(not isinstance(k,str) or not isinstance(v,str) for k,v in value['files'].items())):
        raise ValueError('Repair proposal requires a summary and one to thirty-two complete UTF-8 files')
    return value


def apply_proposal(workspace, proposal, packet, allowed_paths):
    from .runner import _allowlist
    # Revalidate even if a model parser has already accepted the envelope.
    proposal=parse_proposal(json.dumps(proposal,ensure_ascii=False))
    scope=_allowlist(allowed_paths)
    root=Path(workspace).absolute()
    _plain_ancestors(root);_stat_plain(root,directory=True)
    inventory=packet.get('baseline_inventory')
    parents=packet.get('baseline_directories')
    if (packet.get('inventory_complete') is not True or not isinstance(inventory,dict)
            or not isinstance(parents,list) or packet.get('allowed_paths')!=list(scope)
            or packet.get('baseline_inventory_sha256')!=_digest(
                {'files':inventory,'directories':parents,'allowed_paths':list(scope)})):
        raise ValueError('Repair source requires a complete controller-captured baseline inventory')
    current,current_parents=_inventory(root,scope)
    if current!=inventory or current_parents!=parents:
        raise ValueError('Repair source or parent directories changed after captured baseline inventory')
    folded={name.casefold() for name in inventory}
    proposed_names=set()
    files=[]
    for name,content in proposal['files'].items():
        try:
            _safe_relative(name)
        except ReleaseError as exc:
            raise ValueError(str(exc)) from exc
        normalized=_allowlist([name])[0]
        if normalized.endswith('/') or normalized!=name or not name.endswith('.py') or not any(
                name==allowed or (allowed.endswith('/') and name.startswith(allowed)) for allowed in scope):
            raise ValueError('Repair proposal escapes its protected source allowlist')
        if name.casefold() in proposed_names:
            raise ValueError('Repair proposal names collide on Windows')
        proposed_names.add(name.casefold())
        captured=packet.get('files',{}).get(name)
        path=root/name
        _plain_ancestors(path);_stat_plain(path.parent,directory=True)
        if name in inventory:
            if not isinstance(captured,dict):
                raise ValueError('Repair proposal names an omitted existing source file')
            _stat_plain(path)
            actual=hashlib.sha256(path.read_bytes()).hexdigest()
            if actual!=captured.get('sha256') or actual!=inventory[name]:
                raise ValueError('Repair source changed after its captured inference context')
        else:
            parent=str(Path(name).parent).replace('\\','/')
            if (name.casefold() in folded or parent not in parents or path.exists() or path.is_symlink()
                    or any(child.name.casefold()==path.name.casefold() for child in path.parent.iterdir())):
                raise ValueError('New repair module lacks proved baseline/current absence and an existing captured parent')
            actual=None
        data=content.encode('utf-8')
        # Source repair is Python only. Parse syntax before touching candidate.
        compile(content,name,'exec',dont_inherit=True)
        files.append((path,data,actual))
    changes=[]
    for path,data,before in files:
        temporary=path.with_name('.repair-'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(data)
                stream.flush();os.fsync(stream.fileno())
            if before is None:
                # Publish complete bytes atomically without replacing a file
                # created after preflight. Remove the sole temporary link below.
                os.link(temporary,path)
            else:
                if hashlib.sha256(path.read_bytes()).hexdigest()!=before:
                    raise ValueError('Repair source changed before controller application')
                temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        changes.append({'path':str(path.relative_to(root)).replace('\\','/'),'before_sha256':before,
                        'after_sha256':hashlib.sha256(data).hexdigest(),
                        'operation':'create' if before is None else 'replace'})
    return {'application':'controller-only','changes':changes,'native_tools_enabled':False}
