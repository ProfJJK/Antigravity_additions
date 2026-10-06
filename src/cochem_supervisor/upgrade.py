"""Copy durable repair budgets during an administrator-managed version upgrade.

The Windows entry point verifies SYSTEM, private ACLs, and a stopped, disabled
supervisor. This filesystem/SQLite layer is independently testable without
claiming Windows isolation. It never executes candidate code or resets history.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .io import write_json
from .releases import _plain_ancestors, _stat_plain


def _checked_json(path: Path) -> dict:
    _stat_plain(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Upgrade evidence exceeds its size limit')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Upgrade evidence must be a JSON object')
    return value


def _ledger_digest(path: Path, *, component: bool = False) -> str:
    _stat_plain(path)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Supervisor ledger integrity check failed')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        required = {'components', 'recovery_events'} if component else {'supervisor_incidents', 'supervisor_attempts', 'supervisor_events'}
        if not required <= tables:
            raise ValueError('Source is not a supervisor budget ledger')
        digest = hashlib.sha256()
        for line in db.iterdump():
            digest.update(line.encode('utf-8'))
            digest.update(b'\n')
    return digest.hexdigest()


def _copy_ledger(old: Path, new: Path, expected: str, *, component: bool = False) -> str:
    if new.exists() or new.is_symlink():
        if _ledger_digest(new, component=component) != expected:
            raise ValueError('Existing destination ledger differs; budgets will not be overwritten')
        return 'IDENTICAL_LEDGER_PRESERVED'
    temporary = new.parent / ('.ledger-upgrade-' + uuid.uuid4().hex + '.db')
    try:
        with closing(sqlite3.connect(old.as_uri() + '?mode=ro', uri=True, timeout=5)) as origin:
            with closing(sqlite3.connect(temporary, timeout=5)) as destination:
                origin.backup(destination)
        if _ledger_digest(temporary, component=component) != expected:
            raise ValueError('Source ledger changed during upgrade; stop its writer first')
        with temporary.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, new)
    finally:
        temporary.unlink(missing_ok=True)
    return 'LEDGER_COPIED'


def migrate_budget_state(source_private: Path, target_private: Path) -> dict:
    """Preserve all attempts/events and quarantine; reject unresolved deployment.

    Call only after verifying that neither supervisor can write either ledger.
    A repeated copy succeeds only if the destination still has identical
    logical contents. A destination that has advanced is preserved and rejected.
    """
    source, target = Path(source_private).absolute(), Path(target_private).absolute()
    existing = source
    while not existing.exists() and not existing.is_symlink():
        existing = existing.parent
    _plain_ancestors(existing)
    _stat_plain(existing, directory=True)
    _plain_ancestors(target)
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('Upgrade private directories must be disjoint')
    _stat_plain(target, directory=True)
    if source.exists():
        _stat_plain(source, directory=True)
    journal = source / 'release-journal.json'
    if journal.exists() or journal.is_symlink():
        transaction = _checked_json(journal)
        if transaction.get('state') not in ('COMMITTED', 'ROLLED_BACK'):
            raise ValueError('Recover the previous release transaction before upgrading')
    quarantine = source / 'repair-quarantine.json'
    target_quarantine = target / quarantine.name
    quarantine_value = _checked_json(quarantine) if quarantine.exists() or quarantine.is_symlink() else None
    if quarantine_value is not None and (target_quarantine.exists() or target_quarantine.is_symlink()):
        if _checked_json(target_quarantine) != quarantine_value:
            raise ValueError('Existing destination quarantine differs; preserve both records')
    old, new = source / 'supervisor.db', target / 'supervisor.db'
    result = {'source_private': str(source), 'target_private': str(target),
              'status': 'NO_PRIOR_LEDGER', 'quarantine_preserved': quarantine_value is not None}
    copies = []
    for filename, component in (('supervisor.db', False), ('component-recovery.db', True)):
        origin, destination = source / filename, target / filename
        if origin.exists() or origin.is_symlink():
            expected = _ledger_digest(origin, component=component)
            # Preflight every destination before publishing either database.
            if destination.exists() or destination.is_symlink():
                if _ledger_digest(destination, component=component) != expected:
                    raise ValueError('Existing destination ledger differs; budgets will not be overwritten')
            copies.append((origin, destination, expected, component))
    for origin, destination, expected, component in copies:
        status = _copy_ledger(origin, destination, expected, component=component)
        if component:
            result.update(component_status=status, component_ledger_digest=expected)
        else:
            result.update(status=status, ledger_digest=expected)
    if quarantine_value is not None:
        write_json(target_quarantine, quarantine_value)
    write_json(target / 'budget-upgrade.json', result)
    return result
