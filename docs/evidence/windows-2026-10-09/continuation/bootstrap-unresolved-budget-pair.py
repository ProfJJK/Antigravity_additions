"""Fresh unpublished repair ledgers with paired, immutable uncertainty guards.

This preparation primitive is not an installer, migration, or activation path.
A reviewed protected SYSTEM launcher must validate its complete runtime before
calling bootstrap_pair. All mutations are confined to one new private child;
no source ledger, WAL, job history, credential or live configuration is opened.
No retry/cleanup/release API is provided. Failed construction stays unpublished.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid


SCHEMA = 'cochem-unresolved-budget-pair/1'
STATUS = 'UNRESOLVED_PAIR_STAGED_UNPUBLISHED'
INTENT = 'bootstrap-intent.json'
EVIDENCE = 'unresolved-evidence.json'
RECEIPT = 'paired-hold-complete.json'
LEDGERS = ('supervisor.db', 'component-recovery.db')
PINS = {
    'cochem_supervisor.budget_authority': '36157718119f518756619b5222be9e930dba82a1726fd4b1bed23d153a217852',
    'cochem_supervisor.state': '404abfa2971a8f4a865171fc8fee3fe2c51d7837b6d49975346e6bac3740ff74',
    'cochem_supervisor.component_recovery': '3f1336f3ec7d9a2e14289c1f032f2baa9cfb1ab0bed06648e39f9ff016ac871d',
}


class BootstrapHeld(ValueError):
    """A bounded public code; never include private exception text."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ordinary(path, *, directory=False):
    path = Path(path).absolute()
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise BootstrapHeld('REPARSE_PATH')
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
        raise BootstrapHeld('NONORDINARY_PATH')
    return info


def private(path, *, directory=False, inherit=False):
    from cochem_pipeline import windows as win
    win.require_system()
    ordinary(path, directory=directory)
    win.validate_private_directory(path) if directory else win.validate_private_path(path)
    if inherit:
        _, _, rules = win._acl(Path(path))
        if not any(sid == win.SYSTEM_SID and mask == win.FULL_CONTROL
                   and flags & 3 == 3 and not flags & (4 | 8)
                   for sid, mask, flags in rules):
            raise BootstrapHeld('PRIVATE_INHERITANCE_UNVERIFIED')


def dependencies():
    from cochem_supervisor import budget_authority, state, component_recovery
    modules = (budget_authority, state, component_recovery)
    for module in modules:
        path = Path(module.__file__)
        ordinary(path)
        if sha(path.read_bytes()) != PINS[module.__name__]:
            raise BootstrapHeld('LEDGER_MODULE_DRIFT')
    return modules


def exclusive_json(path, value):
    encoded = canonical(value) + b'\n'
    if len(encoded) > 65536:
        raise BootstrapHeld('CONTROL_TOO_LARGE')
    with Path(path).open('xb') as stream:
        private(path)
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return sha(encoded)


def control(path):
    private(path)
    before = ordinary(path)
    if before.st_size > 65536:
        raise BootstrapHeld('CONTROL_TOO_LARGE')
    with Path(path).open('rb') as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(65537)
        after = os.fstat(stream.fileno())
    end = ordinary(path)
    identities = [(i.st_dev, i.st_ino, i.st_size, i.st_mtime_ns) for i in (before, opened, after, end)]
    if len(set(identities)) != 1 or len(raw) > 65536:
        raise BootstrapHeld('CONTROL_CHANGED')
    value = json.loads(raw)
    if raw != canonical(value) + b'\n':
        raise BootstrapHeld('CONTROL_NOT_CANONICAL')
    return value, sha(raw)


def expected_triggers(authority, state, component):
    import ast
    definitions = {}
    with sqlite3.connect(':memory:') as db:
        db.executescript(state.SCHEMA + authority.SCHEMA + authority.SUPERVISOR_TRIGGERS)
        definitions[LEDGERS[0]] = dict(db.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger'"))
    # Extract the literal schema from the hash-bound constructor, without
    # constructing a component ledger or approximating its SQL whitespace.
    source = ast.parse(Path(component.__file__).read_text(encoding='utf-8'))
    classes = [node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'RecoveryLedger']
    methods = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and node.name == '__init__'] if len(classes) == 1 else []
    if len(methods) != 1:
        raise BootstrapHeld('COMPONENT_SCHEMA_UNRECOGNIZED')
    tree = methods[0]
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == 'executescript']
    literals = [node.value for node in ast.walk(calls[0].args[0])
                if isinstance(node, ast.Constant) and isinstance(node.value, str)] if len(calls) == 1 else []
    if len(literals) != 1:
        raise BootstrapHeld('COMPONENT_SCHEMA_UNRECOGNIZED')
    with sqlite3.connect(':memory:') as db:
        db.executescript(literals[0] + authority.SCHEMA + authority.COMPONENT_TRIGGERS)
        definitions[LEDGERS[1]] = dict(db.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger'"))
    return definitions


def inspect_ledgers(root, evidence, *, recorded_at=None):
    """Inspect only this explicitly supplied unpublished pair; never create it."""
    authority, state, component = dependencies()
    expected = expected_triggers(authority, state, component)
    _, digest = authority._evidence(evidence)
    results = {}
    for name in LEDGERS:
        path = root / name
        private(path)
        # All writer connections are closed and the namespace has no WAL/SHM.
        # Immutable read avoids generating sidecars while verifying the new,
        # unpublished pair. It is never used to read a live/old ledger.
        db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=5)
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            observed = authority.status(db)
            if (observed['state'] != authority.STATE or observed['blocked'] is not True
                    or observed['evidence_sha256'] != digest
                    or observed['historical_spend_verified'] is not False
                    or observed['remaining_legacy_allowance'] is not None
                    or recorded_at is not None and observed['recorded_at'] != recorded_at):
                raise BootstrapHeld('AUTHORITY_ROW_MISMATCH')
            if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise BootstrapHeld('LEDGER_INTEGRITY')
            actual = dict(db.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger'"))
            if any(actual.get(key) != value for key, value in expected[name].items()):
                raise BootstrapHeld('DURABLE_GUARD_CHANGED')
            tables = ('supervisor_incidents', 'supervisor_attempts', 'supervisor_events',
                      'supervisor_model_calls', 'supervisor_review_checkpoints') if name == LEDGERS[0] else ('components', 'recovery_events')
            if name == LEDGERS[1] and not {'recovery_no_update', 'recovery_no_delete'} <= set(actual):
                raise BootstrapHeld('HISTORY_GUARD_MISSING')
            counts = {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in tables}
            if any(counts.values()):
                raise BootstrapHeld('UNPUBLISHED_LEDGER_ALREADY_USED')
            results[name] = {'authority': observed, 'integrity': 'ok', 'new_rows': counts,
                             'guard_names': sorted(expected[name])}
        finally:
            db.close()
    if results[LEDGERS[0]]['authority'] != results[LEDGERS[1]]['authority']:
        raise BootstrapHeld('PAIR_AUTHORITY_MISMATCH')
    for name in LEDGERS:
        results[name]['sha256'] = sha((root / name).read_bytes())
    return results


def assert_namespace(root, *, complete):
    allowed = {INTENT, EVIDENCE, *LEDGERS}
    if complete:
        allowed.add(RECEIPT)
    names = {path.name for path in root.iterdir()}
    # Last SQLite connection close must finish its own newly-created WAL; never
    # erase/checkpoint an unknown sidecar or accept a partial/foreign artifact.
    if names != allowed:
        raise BootstrapHeld('PAIR_NAMESPACE_MISMATCH')
    for path in root.iterdir():
        private(path)


def verify_pair(root, evidence):
    root = Path(root).absolute()
    private(root, directory=True)
    assert_namespace(root, complete=True)
    intent, intent_hash = control(root / INTENT)
    recorded, evidence_file_hash = control(root / EVIDENCE)
    receipt, _ = control(root / RECEIPT)
    authority, _, _ = dependencies()
    _, digest = authority._evidence(evidence)
    if (recorded != evidence or set(intent) != {'schema', 'state', 'nonce', 'evidence_sha256', 'module_sha256'}
            or intent['schema'] != SCHEMA or intent['state'] != 'INCOMPLETE_UNPUBLISHED'
            or intent['evidence_sha256'] != digest or intent['module_sha256'] != PINS
            or not isinstance(intent['nonce'], str) or len(intent['nonce']) != 32
            or any(c not in '0123456789abcdef' for c in intent['nonce'])):
        raise BootstrapHeld('PAIR_INTENT_MISMATCH')
    ledgers = inspect_ledgers(root, evidence)
    expected = {'schema': SCHEMA, 'status': STATUS, 'nonce': intent['nonce'],
                'intent_sha256': intent_hash, 'evidence_file_sha256': evidence_file_hash,
                'evidence_sha256': digest, 'module_sha256': PINS, 'ledgers': ledgers,
                'historical_allowance': None, 'paid_repair_enabled': False,
                'component_recovery_enabled': False, 'activation_ready': False,
                'live_configuration_changed': False, 'old_ledgers_opened': False}
    if receipt != expected:
        raise BootstrapHeld('PAIR_COMPLETION_MISMATCH')
    assert_namespace(root, complete=True)
    return receipt


def bootstrap_pair(root, evidence, *, now=None):
    """No resume: any existing root is a preservation hold, including empty."""
    authority, state, component = dependencies()
    _, digest = authority._evidence(evidence)
    # Validate timestamp before changing the filesystem; record also validates.
    import math
    import time
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise BootstrapHeld('INVALID_TIME')
    root = Path(root).absolute()
    private(root.parent, directory=True, inherit=True)
    # No parents=True, exist_ok, ACL rewrite, unlink or cleanup on any failure.
    root.mkdir()
    private(root, directory=True, inherit=True)
    root_id = (root.stat().st_dev, root.stat().st_ino)
    intent = {'schema': SCHEMA, 'state': 'INCOMPLETE_UNPUBLISHED', 'nonce': uuid.uuid4().hex,
              'evidence_sha256': digest, 'module_sha256': PINS}
    intent_hash = exclusive_json(root / INTENT, intent)
    evidence_hash = exclusive_json(root / EVIDENCE, evidence)
    first = state.Ledger(root / LEDGERS[0])
    first.hold_legacy_budget_authority(evidence, now=now)
    second = component.RecoveryLedger(root / LEDGERS[1])
    second.hold_legacy_budget_authority(evidence, now=now)
    assert_namespace(root, complete=False)
    ledgers = inspect_ledgers(root, evidence, recorded_at=now)
    assert_namespace(root, complete=False)
    if (root.stat().st_dev, root.stat().st_ino) != root_id:
        raise BootstrapHeld('PAIR_ROOT_CHANGED')
    receipt = {'schema': SCHEMA, 'status': STATUS, 'nonce': intent['nonce'],
               'intent_sha256': intent_hash, 'evidence_file_sha256': evidence_hash,
               'evidence_sha256': digest, 'module_sha256': PINS, 'ledgers': ledgers,
               'historical_allowance': None, 'paid_repair_enabled': False,
               'component_recovery_enabled': False, 'activation_ready': False,
               'live_configuration_changed': False, 'old_ledgers_opened': False}
    exclusive_json(root / RECEIPT, receipt)
    return verify_pair(root, evidence)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--new-private-root', required=True, type=Path)
    args = parser.parse_args(argv)
    # Deliberately no command-line Apply. This core is ready for a separately
    # reviewed pinned SYSTEM launcher, not an unprotected ad-hoc execution.
    info = ordinary(args.evidence)
    if info.st_size > 16384:
        raise BootstrapHeld('EVIDENCE_TOO_LARGE')
    with args.evidence.open('rb') as stream:
        data = stream.read(16385)
    if len(data) > 16384:
        raise BootstrapHeld('EVIDENCE_TOO_LARGE')
    authority, _, _ = dependencies()
    _, digest = authority._evidence(json.loads(data))
    print(json.dumps({'schema': SCHEMA, 'mode': 'READ_ONLY_PLAN',
        'new_private_root': str(args.new_private_root.absolute()), 'evidence_sha256': digest,
        'activation_ready': False, 'protected_launcher_required': True,
        'historical_allowance': None, 'source_ledgers_opened': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
