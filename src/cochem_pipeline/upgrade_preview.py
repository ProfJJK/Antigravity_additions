"""Read-only, bounded upgrade impact inspection; never opens a mutable JobStore."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .ramdisk import ordinary_tree


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _configuration(filename):
    path = Path(filename).absolute()
    ordinary_tree(path)
    if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 1048576:
        raise ValueError('Upgrade preview requires a bounded ordinary configuration file')
    raw = path.read_bytes()
    value = json.loads(raw.decode('utf-8-sig'))
    from .config import load_config
    config = load_config(str(path))
    if path.read_bytes() != raw:
        raise ValueError('Configuration changed while its upgrade preview was being captured')
    return config, value, {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def _paths(config, filename):
    result = {'configuration': filename, 'private_root': str(config.private_root),
              'job_board': str(config.job_db), 'controller_token': str(config.token_file),
              'git_executable': config.git_executable,
              **{'worker_root.' + slot: str(path) for slot, path in config.slot_roots.items()},
              **{'provider_executable.' + key: value['executable'] for key, value in config.providers.items()}}
    if config.ramdisk.enabled:
        result['ram_volume'] = config.ramdisk.mount_root
    if config.knowledge.enabled:
        result.update({'knowledge.' + key: str(getattr(config.knowledge, key))
                       for key in ('source_root', 'wiki_root', 'state_root', 'manifest_path')})
    result.update({'project.' + key: str(value.repository) for key, value in config.coding_projects.items()})
    return result


def _changes(old, new):
    return [{'key': key, 'before': old.get(key), 'after': new.get(key),
             'changed': old.get(key) != new.get(key)} for key in sorted(old.keys() | new.keys())]


def _schema(conn):
    return {row[0] + ':' + row[1]: ' '.join((row[2] or '').split()) for row in
            conn.execute("SELECT type,name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")}


def _expected_schema():
    from .store import SCHEMA
    from .coding_store import SCHEMA as CODING_SCHEMA
    from .routing_store import SCHEMA as ROUTING_SCHEMA
    with closing(sqlite3.connect(':memory:')) as conn:
        conn.executescript(SCHEMA + CODING_SCHEMA + ROUTING_SCHEMA)
        return _schema(conn)


def _captured_database(path):
    if not path.exists():
        return {'exists': False, 'workflows': [], 'schema': {}, 'user_version': None}
    ordinary_tree(path)
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError('Upgrade preview database must be an ordinary single-link file')
    with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA trusted_schema=OFF')
        conn.execute('BEGIN')
        schema = _schema(conn)
        if 'table:pipeline_jobs' not in schema:
            raise ValueError('Existing database is not the pipeline job board')
        workflows = conn.execute(
            'SELECT job_id,kind,status FROM pipeline_jobs WHERE job_id=workflow_id ORDER BY created_at,job_id LIMIT 10001').fetchall()
        if len(workflows) > 10000:
            raise ValueError('Upgrade preview exceeds its 10000-workflow bound; export a protected full inventory first')
        result = []
        for identifier, kind, status in workflows:
            item = {'workflow_id': identifier, 'kind': kind, 'status': status,
                    'captured_policy_action': 'preserve_immutable_capture', 'automatic_rewrite': False}
            if 'table:pipeline_routing_workflows' in schema:
                routing = conn.execute('SELECT policy_json FROM pipeline_routing_workflows WHERE workflow_id=?',
                                       (identifier,)).fetchone()
                item['routing_policy_sha256'] = _digest(json.loads(routing[0])) if routing else None
            if 'table:coding_workflows' in schema:
                row = conn.execute('SELECT state_json FROM coding_workflows WHERE workflow_id=?', (identifier,)).fetchone()
                if row:
                    if len(row[0]) > 16 * 1048576:
                        raise ValueError('Captured workflow exceeds the preview bound')
                    state = json.loads(row[0])
                    item.update(state=state.get('status'), baseline_commit=state.get('baseline_commit'),
                                planning_evidence_sha256=_digest(state.get('planning_evidence')),
                                project_policy_sha256=_digest(state.get('project')),
                                docker_policy_sha256=_digest(state.get('docker')),
                                execution_compatibility='requires_current_contract_check_before_resume')
            result.append(item)
        return {'exists': True, 'workflows': result, 'schema': schema,
                'user_version': conn.execute('PRAGMA user_version').fetchone()[0]}


def upgrade_preview(current_config, proposed_config):
    """Compare reviewed files and captured DB state without mutating deployment.

    No provider is invoked and no credential/token contents enter the output.
    Backups and rollback readiness remain unverified until actually supplied.
    """
    current, old_raw, old_info = _configuration(current_config)
    proposed, new_raw, new_info = _configuration(proposed_config)
    captured = _captured_database(current.job_db)
    expected = _expected_schema()
    actual = captured.pop('schema')
    identities = _changes(current.workers, proposed.workers)
    paths = _changes(_paths(current, old_info['path']), _paths(proposed, new_info['path']))
    changes = [{'key': key, 'before_sha256': _digest(old_raw.get(key)), 'after_sha256': _digest(new_raw.get(key))}
               for key in sorted(old_raw.keys() | new_raw.keys()) if old_raw.get(key) != new_raw.get(key)]
    schema_changes = [{'object': key, 'action': 'create_on_initialization' if key not in actual
                       else 'preserve_unmanaged_object' if key not in expected
                       else 'definition_differs_requires_compatibility_review',
                       'before_sha256': _digest(actual.get(key)), 'after_sha256': _digest(expected.get(key))}
                      for key in sorted(actual.keys() | expected.keys()) if actual.get(key) != expected.get(key)]
    prerequisites = [
        {'id': 'quiesced_workers', 'required': True, 'verified': False,
         'detail': 'Stop and disable managed controller/supervisor tasks; prove native workers and leases are drained'},
        {'id': 'job_board_online_backup', 'required': captured['exists'], 'verified': False,
         'path': str(current.job_db), 'detail': 'Use SQLite backup including committed WAL; verify integrity and recovery'},
        {'id': 'independent_supervisor_ledgers', 'required': True, 'verified': False,
         'detail': 'Preserve exact supervisor/component recovery budgets, quarantine and release journal'},
        {'id': 'previous_protected_release', 'required': True, 'verified': False,
         'path': str(Path(old_info['path']).parent), 'detail': 'Retain previous protected code, dependency lock and reviewed configuration'},
        {'id': 'native_credentials', 'required': True, 'verified': False,
         'detail': 'Preserve worker account SIDs, Credential Manager targets and native subscription profiles'},
        {'id': 'knowledge_generation', 'required': current.knowledge.enabled, 'verified': False,
         'detail': 'Retain captured source/catalog hashes and prior protected index generation'},
        {'id': 'adopted_ram_volume', 'required': current.ramdisk.enabled, 'verified': False,
         'detail': 'Retain existing startup task and fixed RAM drive; never format it as an upgrade step'},
    ]
    return {'schema': 'cochem-upgrade-preview/1', 'checked_at': time.time(),
            'read_only': True, 'native_models_executed': False,
            'current': old_info, 'proposed': new_info, 'protected_paths': paths, 'identities': identities,
            'configuration_changes': changes,
            'database': {**captured, 'path': str(current.job_db), 'proposed_path': str(proposed.job_db),
                'actual_schema_sha256': _digest(actual), 'proposed_schema_sha256': _digest(expected),
                'proposed_user_version': captured['user_version'] if captured['exists'] else 0,
                'schema_versioning': 'DDL content hash; initialization preserves existing user_version',
                'schema_changes': schema_changes, 'migration_executed': False},
            'rollback_prerequisites': prerequisites,
            'ready_to_deploy': False,
            'remaining_action': 'Validate backups, protected deployment, schema compatibility and captured workflow contracts on the actual host'}
