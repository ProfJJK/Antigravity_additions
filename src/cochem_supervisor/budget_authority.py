"""An immutable, explicit hold when legacy repair spending cannot be established.

This is not a spend importer or authority attestation. Missing hold records do
not prove zero historical spend. A stopped, reviewed installer must establish
the correct authority decision for both ledgers before activation. This module
only records an unresolved decision and has no release/reset operation.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import stat
import time


STATE = 'UNRESOLVED_LEGACY_AUTHORITY'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS unresolved_budget_authority (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    state TEXT NOT NULL CHECK(state='UNRESOLVED_LEGACY_AUTHORITY'),
    recorded_at REAL NOT NULL,
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    evidence_sha256 TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS unresolved_budget_authority_no_update
BEFORE UPDATE ON unresolved_budget_authority
BEGIN SELECT RAISE(ABORT,'Unresolved budget authority is immutable'); END;
CREATE TRIGGER IF NOT EXISTS unresolved_budget_authority_no_delete
BEFORE DELETE ON unresolved_budget_authority
BEGIN SELECT RAISE(ABORT,'Unresolved budget authority cannot be cleared'); END;
CREATE TRIGGER IF NOT EXISTS unresolved_budget_authority_no_replace
BEFORE INSERT ON unresolved_budget_authority
WHEN EXISTS(SELECT 1 FROM unresolved_budget_authority)
BEGIN SELECT RAISE(ABORT,'Unresolved budget authority cannot be replaced'); END;
'''

# These triggers remain in a copied ledger when older compatible code is run.
# Old code that does not know the new Python guard still cannot add spend or
# renew a running review lease. Cleanup/terminal results and history stay usable.
SUPERVISOR_TRIGGERS = '''
CREATE TRIGGER IF NOT EXISTS unresolved_authority_attempt_insert
BEFORE INSERT ON supervisor_attempts
WHEN EXISTS(SELECT 1 FROM unresolved_budget_authority)
BEGIN SELECT RAISE(ABORT,'UNRESOLVED_LEGACY_AUTHORITY'); END;
CREATE TRIGGER IF NOT EXISTS unresolved_authority_model_call_insert
BEFORE INSERT ON supervisor_model_calls
WHEN EXISTS(SELECT 1 FROM unresolved_budget_authority)
BEGIN SELECT RAISE(ABORT,'UNRESOLVED_LEGACY_AUTHORITY'); END;
CREATE TRIGGER IF NOT EXISTS unresolved_authority_lease_renewal
BEFORE UPDATE OF lease_expires_at ON supervisor_attempts
WHEN NEW.status='RUNNING' AND EXISTS(SELECT 1 FROM unresolved_budget_authority)
BEGIN SELECT RAISE(ABORT,'UNRESOLVED_LEGACY_AUTHORITY'); END;
CREATE TRIGGER IF NOT EXISTS unresolved_authority_incident_unblock
BEFORE UPDATE OF status ON supervisor_incidents
WHEN OLD.status='BLOCKED' AND NEW.status='OPEN'
 AND EXISTS(SELECT 1 FROM unresolved_budget_authority)
BEGIN SELECT RAISE(ABORT,'UNRESOLVED_LEGACY_AUTHORITY'); END;
'''

COMPONENT_TRIGGERS = '''
CREATE TRIGGER IF NOT EXISTS unresolved_authority_component_insert
BEFORE INSERT ON components
WHEN NEW.attempts>coalesce((SELECT attempts FROM components WHERE component=NEW.component),0)
 AND EXISTS(SELECT 1 FROM unresolved_budget_authority)
BEGIN SELECT RAISE(ABORT,'UNRESOLVED_LEGACY_AUTHORITY'); END;
CREATE TRIGGER IF NOT EXISTS unresolved_authority_component_increment
BEFORE UPDATE OF attempts ON components
WHEN NEW.attempts>OLD.attempts AND EXISTS(SELECT 1 FROM unresolved_budget_authority)
BEGIN SELECT RAISE(ABORT,'UNRESOLVED_LEGACY_AUTHORITY'); END;
'''


def _evidence(value):
    if (not isinstance(value, dict) or set(value) != {'schema', 'reason', 'sources'}
            or value['schema'] != 'cochem-unresolved-budget-evidence/1'
            or not isinstance(value['reason'], str) or not 1 <= len(value['reason']) <= 2000
            or any(ord(char) < 32 for char in value['reason'])
            or not isinstance(value['sources'], list) or not 1 <= len(value['sources']) <= 32):
        raise ValueError('Unresolved budget authority requires bounded provenance evidence')
    seen = set()
    for source in value['sources']:
        if (not isinstance(source, dict) or set(source) != {'kind', 'sha256'}
                or not isinstance(source['kind'], str) or not re.fullmatch('[a-z][a-z0-9_-]{0,63}', source['kind'])
                or not isinstance(source['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', source['sha256'])
                or (source['kind'], source['sha256']) in seen):
            raise ValueError('Invalid or duplicate unresolved budget provenance digest')
        seen.add((source['kind'], source['sha256']))
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    if len(raw.encode('utf-8')) > 16384:
        raise ValueError('Unresolved budget evidence exceeds its byte bound')
    return raw, hashlib.sha256(raw.encode('utf-8')).hexdigest()


def status(connection):
    rows = connection.execute('SELECT state,recorded_at,evidence_json,evidence_sha256 FROM unresolved_budget_authority LIMIT 2').fetchall()
    if not rows:
        return {'state': 'NO_UNRESOLVED_HOLD_RECORDED', 'blocked': False,
                'historical_spend_verified': False, 'remaining_legacy_allowance': None}
    if len(rows) != 1:
        raise ValueError('Ambiguous unresolved budget authority')
    state, timestamp, raw, captured = rows[0]
    if state != STATE or not isinstance(raw, str) or len(raw.encode('utf-8')) > 16384:
        raise ValueError('Invalid unresolved budget authority')
    encoded, observed = _evidence(json.loads(raw))
    if raw != encoded or observed != captured or type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError('Unresolved budget authority evidence changed')
    return {'state': STATE, 'blocked': True, 'recorded_at': timestamp, 'evidence_sha256': captured,
            'historical_spend_verified': False, 'remaining_legacy_allowance': None}


def record(connection, evidence, *, now=None):
    """Call inside the caller's BEGIN IMMEDIATE transaction; never grants spend."""
    raw, digest = _evidence(evidence)
    timestamp = time.time() if now is None else now
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError('Authority hold time must be finite and nonnegative')
    current = status(connection)
    if current['blocked']:
        if current['evidence_sha256'] != digest:
            raise ValueError('Existing unresolved authority evidence must be preserved')
        return current
    connection.execute('INSERT INTO unresolved_budget_authority VALUES(1,?,?,?,?)', (STATE, timestamp, raw, digest))
    return status(connection)


def blocked(connection):
    return status(connection)['blocked']


def require_unheld(connection):
    if blocked(connection):
        raise ValueError(STATE + ': preserve historical budgets; reviewed authority is required')


def read_status(path):
    """Read an optional peer ledger without initializing or upgrading its schema."""
    from cochem_pipeline.ramdisk import RamdiskError, ordinary_tree
    path = Path(path).absolute()
    # Missing leaf is allowed, but every existing ancestor must be ordinary.
    # Permission errors and dangling/reparse ancestors are never absence.
    try:
        ordinary_tree(path, allow_missing=True)
    except RamdiskError:
        raise ValueError('Budget authority ledger ancestry contains a reparse point or symlink') from None
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {'state': 'NO_UNRESOLVED_HOLD_RECORDED', 'blocked': False,
                'historical_spend_verified': False, 'remaining_legacy_allowance': None}
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('Budget authority ledger must be an ordinary single-link file')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('BEGIN')
        table = connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='unresolved_budget_authority'").fetchone()
        if table is None:
            return {'state': 'NO_UNRESOLVED_HOLD_RECORDED', 'blocked': False,
                    'historical_spend_verified': False, 'remaining_legacy_allowance': None}
        return status(connection)
    finally:
        connection.close()


def verify_preserved(old, new):
    """Require every source hold and SQL guard to survive advanced migration."""
    name = 'unresolved_budget_authority'
    old_tables = {row[0] for row in old.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    if name not in old_tables:
        return
    source = status(old)
    if not source['blocked']:
        return
    new_tables = {row[0] for row in new.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
    if name not in new_tables or status(new) != source:
        raise ValueError('Destination lost or changed unresolved budget authority')
    old_row = tuple(old.execute('SELECT * FROM unresolved_budget_authority').fetchone())
    new_row = tuple(new.execute('SELECT * FROM unresolved_budget_authority').fetchone())
    if old_row != new_row:
        raise ValueError('Destination changed immutable authority provenance')
    triggers = old.execute("SELECT name,sql FROM sqlite_schema WHERE type='trigger' AND name LIKE 'unresolved_%'").fetchall()
    for name, definition in triggers:
        observed = new.execute("SELECT sql FROM sqlite_schema WHERE type='trigger' AND name=?", (name,)).fetchone()
        if observed is None or observed[0] != definition:
            raise ValueError('Destination lost or changed a durable unresolved-budget guard')
