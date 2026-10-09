"""Authenticated, read-only projections of controller evidence for Antigravity.

These views never probe providers, repair state, grant leases, or infer a pass
from an implementation claim. Missing captures and launch evidence stay unknown.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from .planning_governance import SPECIFICATION_ID, execution_contract, digest


_MAX_FILE = 8 * 1024 * 1024
_ID = re.compile(r'\bS427-[A-Z]+-\d{3}\b')
_SHA = re.compile(r'[0-9a-f]{64}')


class OperatorViewHistory:
    """Read-only observations cached in memory, separate from execution state."""
    def __init__(self):
        self._lock = threading.Lock()
        self._retention = None

    def retention_snapshot(self, private_root):
        from .operations_policy import storage_forecast
        with self._lock:
            previous = self._retention
            if previous is not None and time.time() - previous['observed_at'] < 60:
                return {**previous, 'cached': True}
            observed = storage_forecast(private_root, previous=previous, max_files=256)
            self._retention = observed
            return {**observed, 'cached': False}


def _document(root, relative):
    """Only local deployment artifacts are readable; no URLs or arbitrary paths."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path.stat().st_size > _MAX_FILE:
        raise ValueError('Artifact must be a bounded file inside the deployment')
    raw = path.read_bytes()
    if len(raw) > _MAX_FILE:
        raise ValueError('Artifact grew beyond the read limit')
    return raw


def _load(root, relative, default):
    try:
        return json.loads(_document(root, relative))
    except (OSError, ValueError, TypeError):
        return default


def _source_freshness(root, candidate, artifact):
    """Check the exact tested files, never a version label or an unbound manifest."""
    capture = artifact if isinstance(artifact, dict) else {}
    relative = candidate.get('tested_source_manifest', capture.get('tested_source_manifest'))
    expected = candidate.get('tested_source_manifest_sha256', capture.get('tested_source_manifest_sha256'))
    if relative is None and candidate.get('artifact') == 'docs/evidence/VALIDATION_4.2.7-r2.json':
        relative = 'docs/evidence/source_4.2.7-r2.json'
    result = {'status': 'unknown', 'verified': False, 'manifest': relative,
              'expected_sha256': expected, 'changed': [], 'missing': [], 'uncommitted_source': []}
    if not isinstance(relative, str) or not isinstance(expected, str) or not _SHA.fullmatch(expected):
        result['reason'] = 'Hash-bound tested source and dependency manifest is missing'
        return result
    try:
        raw = _document(root, relative)
        result['observed_sha256'] = hashlib.sha256(raw).hexdigest()
        if result['observed_sha256'] != expected:
            raise ValueError('Tested source manifest hash mismatch')
        manifest = json.loads(raw)
        files = manifest['files']
        if (not isinstance(files, dict) or not 1 <= len(files) <= 10000
                or 'uv.lock' not in files
                or not any(name.startswith('src/') and name.endswith('.py') for name in files)):
            raise ValueError('Manifest must bind runtime source and the dependency lock')
        content_hash = manifest.get('content_sha256', manifest.get('sha256'))
        if content_hash is not None and content_hash != digest(files):
            raise ValueError('Tested source manifest content commitment mismatch')
        for filename, sha256 in files.items():
            if not isinstance(filename, str) or not isinstance(sha256, str) or not _SHA.fullmatch(sha256):
                raise ValueError('Malformed tested file commitment')
            try:
                actual = hashlib.sha256(_document(root, filename)).hexdigest()
            except (OSError, ValueError):
                result['missing'].append(filename)
                continue
            if actual != sha256:
                result['changed'].append(filename)
        for path in (root / 'src').rglob('*.py'):
            filename = path.relative_to(root).as_posix()
            if filename not in files:
                result['uncommitted_source'].append(filename)
            if len(result['uncommitted_source']) > 10000:
                raise ValueError('Runtime source inventory exceeds the verification limit')
        result['verified'] = not any(result[key] for key in ('changed', 'missing', 'uncommitted_source'))
        result['status'] = 'current' if result['verified'] else 'stale'
        result['files_checked'] = len(files)
        result['dependency_lock_sha256'] = files['uv.lock']
        result['reason'] = ('Tested source and dependency lock bytes match' if result['verified']
                            else 'Installed source or dependency commitments changed or are missing')
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result.update(status='stale', reason=str(exc)[:256])
    return result


def acceptance_dashboard(source_root):
    """Join every normative ID to physical evidence and a permanent checklist row."""
    root = Path(source_root)
    ledger = _load(root, 'docs/requirements_4.2.7.json', None)
    evidence = _load(root, 'docs/acceptance_4.2.7.json', None)
    # Wheels carry exact canonical assets, while receipt artifacts may remain
    # solely in the deployment/release bundle. Missing physical artifacts fail
    # verification even when their ledger entries were packaged successfully.
    if ledger is None:
        ledger = _load(root, 'requirements_4.2.7.json', {})
    if evidence is None:
        evidence = _load(root, 'acceptance_4.2.7.json', {})
    definitions = ledger.get('requirements', []) if isinstance(ledger, dict) else ledger
    records = evidence.get('evidence', evidence.get('requirements', [])) if isinstance(evidence, dict) else evidence
    definitions = definitions if isinstance(definitions, list) else []
    records = records if isinstance(records, list) else []
    try:
        specification = _document(root, '4.2.7_SRS.md')
        normative = set(_ID.findall(specification.decode('utf-8')))
        specification_sha256 = hashlib.sha256(specification).hexdigest()
    except (OSError, ValueError, UnicodeError):
        normative, specification_sha256 = set(), None
    catalog = {row['id']: row for row in definitions if isinstance(row, dict) and isinstance(row.get('id'), str)}
    duplicate_ids = len(catalog) != len(definitions)
    ids = sorted(normative | set(catalog))
    revision = ledger.get('revision') if isinstance(ledger, dict) else None
    ledger_specification = ledger.get('specification_sha256') if isinstance(ledger, dict) else None
    catalog_source_verified = specification_sha256 is not None and ledger_specification == specification_sha256
    evidence_specification = evidence.get('specification_sha256') if isinstance(evidence, dict) else None
    rows = []
    artifact_hashes = {}
    source_checks = {}
    for identifier in ids:
        definition = catalog.get(identifier, {})
        artifacts = []
        for record in records:
            if not isinstance(record, dict) or record.get('id', record.get('requirement_id')) != identifier:
                continue
            for candidate in record.get('evidence', [record]):
                if not isinstance(candidate, dict):
                    continue
                item = {key: candidate.get(key) for key in ('artifact', 'sha256', 'platform', 'revision', 'status', 'recorded_at')}
                item['specification_sha256'] = candidate.get('specification_sha256', evidence_specification)
                item['verified'] = False
                item['verification'] = 'Artifact, SHA-256, platform, revision and passed status are required'
                expected = item.get('sha256')
                if (isinstance(item['artifact'], str) and isinstance(expected, str) and _SHA.fullmatch(expected)
                        and item['platform'] and item['revision'] and item['status'] == 'passed'):
                    try:
                        if item['artifact'] not in artifact_hashes:
                            artifact_hashes[item['artifact']] = hashlib.sha256(_document(root, item['artifact'])).hexdigest()
                        actual = artifact_hashes[item['artifact']]
                        item['observed_sha256'] = actual
                        item['verified'] = (actual == expected and revision is not None and item['revision'] == revision
                                            and catalog_source_verified and item['specification_sha256'] == specification_sha256)
                        item['verification'] = ('Verified deployment artifact and revision' if item['verified']
                                                else 'Artifact hash, governing specification or revision mismatch')
                        if item['verified']:
                            binding = digest([item['artifact'], candidate.get('tested_source_manifest'),
                                              candidate.get('tested_source_manifest_sha256')])
                            if binding not in source_checks:
                                source_checks[binding] = _source_freshness(
                                    root, candidate, _load(root, item['artifact'], {}))
                            item['source_freshness'] = source_checks[binding]
                            item['historical_artifact_verified'] = True
                            item['verified'] = item['source_freshness']['verified']
                            if not item['verified']:
                                item['verification'] = item['source_freshness']['reason']
                    except (OSError, ValueError):
                        item['verification'] = 'Artifact is missing, outside deployment, or exceeds the read limit'
                artifacts.append(item)
        required = definition.get('required_platforms', [])
        required = required if isinstance(required, list) else []
        # A newer failed/missing artifact cannot be hidden by an older pass.
        # The protected evidence ledger is an ordered append-only history.
        latest_by_platform = {item['platform']: item for item in artifacts if isinstance(item['platform'], str)}
        passed = {platform for platform, item in latest_by_platform.items() if item['verified']}
        verified = bool(passed) and set(required) <= passed and identifier in normative and bool(definition) and catalog_source_verified
        latest = artifacts[-1] if artifacts else None
        rows.append({'id': identifier, 'chapter': definition.get('chapter'),
            'requirement': definition.get('requirement'), 'status': 'verified' if verified else 'unverified',
            'code': definition.get('code', definition.get('code_refs', [])),
            'owner_decisions': definition.get('owner_decisions', []),
            'required_platforms': required, 'evidence': artifacts, 'latest_artifact': latest,
            'remaining_action': None if verified else definition.get('remediation', 'Collect and hash current-revision acceptance evidence'),
            'catalog_present': bool(definition), 'canonical': identifier in normative})
    complete = bool(normative) and normative == set(catalog) and not duplicate_ids and catalog_source_verified
    verified = sum(row['status'] == 'verified' for row in rows)
    return {'specification_id': SPECIFICATION_ID, 'specification_sha256': specification_sha256,
        'revision': revision, 'catalog_complete': complete, 'duplicate_requirement_ids': duplicate_ids,
        'catalog_specification_sha256': ledger_specification, 'catalog_source_verified': catalog_source_verified,
        'source_freshness': list(source_checks.values()),
        'verification_scope': 'Exact tested deployment bytes and recorded platforms; not full semantic or target-host certification',
        'all_verified': complete and bool(rows) and verified == len(rows),
        'counts': {'mandatory': len(ids), 'verified': verified, 'unverified': len(ids) - verified},
        'requirements': rows, 'release_checklist': [{'id': row['id'], 'chapter': row['chapter'],
            'status': row['status'], 'owner_decisions': row['owner_decisions'],
            'closing_artifacts': [item['artifact'] for item in row['evidence'] if item['verified']],
            'remaining_action': row['remaining_action']} for row in rows]}


def _blocked(job, jobs, hardware):
    status = job['status']
    if status in {'COMPLETED', 'FAILED'}:
        return {'state': 'terminal', 'reason': job.get('error')}
    if job['kind'] in {'CODE_REQUEST', 'MACRO_PLANNING_REQUEST'} and status == 'IN_PROGRESS':
        return {'state': 'workflow_active', 'reason': 'Aggregate workflow; inspect its execution jobs for the current prerequisite'}
    if status == 'IN_PROGRESS':
        return {'state': 'executing', 'reason': 'A controller lease is active; completion evidence is pending'}
    if job.get('error') and status == 'BLOCKED':
        return {'state': 'blocked', 'reason': job['error']}
    route = job.get('routing') or {}
    if route.get('state') in {'BLOCKED', 'WAITING'}:
        return {'state': 'blocked' if route['state'] == 'BLOCKED' else 'waiting',
                'reason': route.get('wait_reason'), 'until': route.get('next_eligible_at')}
    if job['kind'] == 'SYNTHESIS' and status == 'BLOCKED':
        chapters = [row for row in jobs if row['kind'] == 'CHAPTER_DRAFT']
        pending = [row['job_id'] for row in chapters if row['status'] != 'COMPLETED']
        return {'state': 'blocked', 'reason': 'Accepted chapter artifacts are required' if chapters
                else 'An accepted manifest must create the chapter jobs', 'prerequisite_job_ids': pending}
    if hardware.get('capacity') == 0:
        return {'state': 'admission_paused', 'reason': hardware.get('reasons', ['No admission capacity'])}
    return {'state': 'queued', 'reason': 'Awaiting a controller claim and live Chapter 06 availability check'}


def _queue(job, holds, now, current_policy):
    state = job.get('routing') or {}
    candidates = state.get('candidates', [])
    cursor = state.get('cursor', 0)
    index = cursor if isinstance(cursor, int) and 0 <= cursor < len(candidates) else 0
    target = candidates[index] if candidates else None
    relevant = [hold for hold in holds if any(
        hold['resource_key'] == candidate.get({'model': 'key', 'provider': 'provider', 'pool': 'quota_pool'}[hold['scope']])
        for candidate in candidates)]
    return {'job_id': job['job_id'], 'workflow_id': job['workflow_id'], 'kind': job['kind'],
        'status': job['status'], 'score': state.get('score_details'), 'captured_candidates': candidates,
        'next_candidate_to_evaluate': target, 'candidate_is_reserved': False,
        'selected_route': {key: (job.get('route') or {}).get(key) for key in
                           ('provider', 'model', 'reasoning_effort', 'candidate_index', 'reservation_sha256',
                            'policy_digest', 'admission_policy_digest')},
        'next_eligible_at': state.get('next_eligible_at'),
        'wait_seconds': max(0, state.get('next_eligible_at', 0) - now),
        'wait_reason': state.get('wait_reason'), 'cycle': state.get('cycle'),
        'dispatches': state.get('dispatches'), 'failure_count': state.get('failure_count'),
        'observed_holds': relevant,
        'quota': {'remaining': None, 'knowledge': 'unknown',
            'explanation': 'Subscription balances are not measured; holds are observed failures, not balance estimates'},
        'captured_limits': {key: state.get('policy', {}).get(key) for key in
                            ('provider_limits', 'quota_pool_limits', 'model_limits')},
        'current_admission_limits': {key: (current_policy or {}).get(key) for key in
                                     ('provider_limits', 'quota_pool_limits', 'model_limits')}}


def resource_plot(samples):
    """Render separately labelled capacity and measured pressure; never mix units."""
    points = []
    seen = set()
    for sample in samples:
        if not isinstance(sample, dict) or sample.get('measured_at') is None:
            continue
        stamp = sample['measured_at']
        if stamp in seen:
            continue
        seen.add(stamp)
        cpu = sample.get('measurements', {}).get('cpu', {})
        memory = sample.get('measurements', {}).get('memory', {})
        points.append({'at': stamp, 'capacity': sample.get('capacity'), 'state': sample.get('state'),
            'cpu_percent': cpu.get('percent', sample.get('cpu_percent')),
            'available_memory_mb': memory.get('available_mb', sample.get('memory_available_mb')),
            'limiting_reasons': sample.get('reasons', []),
            'measurements': sample.get('measurements', {})})
    points = sorted(points, key=lambda row: row['at'])[-200:]
    panels = [('capacity', 'Admitted capacity (agents)', '#2563eb'),
              ('cpu_percent', 'Measured CPU (%)', '#b45309'),
              ('available_memory_mb', 'Available RAM (MB)', '#15803d')]
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 330" role="img" aria-label="Admission capacity and measured host resources">',
           '<rect width="800" height="330" fill="white"/>']
    for panel, (key, title, color) in enumerate(panels):
        values = [(index, point[key]) for index, point in enumerate(points)
                  if type(point[key]) in (int, float) and math.isfinite(point[key])]
        scale = 100 if key == 'cpu_percent' else max([value for _, value in values] or [1]) or 1
        top = panel * 110
        svg.append(f'<text x="12" y="{top + 18}" fill="#111827" font-size="13">{title}</text>')
        svg.append(f'<text x="12" y="{top + 38}" fill="#6b7280" font-size="11">0 to {scale:g}</text>')
        svg.append(f'<path d="M 100 {top + 90} H 790" stroke="#d1d5db"/>')
        coordinates = ' '.join(f'{100 + index * 680 / max(1, len(points) - 1):.1f},{top + 90 - 65 * value / scale:.1f}' for index, value in values)
        if coordinates:
            svg.append(f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="2"/>')
            for index, value in values:
                svg.append(f'<circle cx="{100 + index * 680 / max(1, len(points) - 1):.1f}" cy="{top + 90 - 65 * value / scale:.1f}" r="2" fill="{color}"/>')
        else:
            svg.append(f'<text x="100" y="{top + 60}" fill="#6b7280" font-size="12">No physical samples available</text>')
    svg.append('</svg>')
    return {'samples': points, 'sample_scope': 'retained transition telemetry plus latest completed host sample',
            'continuous_monitoring_claimed': False, 'svg': ''.join(svg)}


def _topology_capture(runtime):
    from .deployment import attestation_source_hashes, topology_configuration
    private = getattr(runtime.config, 'private_root', None)
    report = _load(Path(private), 'execution-readiness.json', {}) if private else {}
    if not isinstance(report, dict):
        report = {}
    checked = report.get('checked_at')
    state, reason = 'unknown', 'No protected readiness capture'
    if report:
        sources = report.get('attestation_source_sha256')
        boot = getattr(runtime, 'boot_id', None)
        if (type(checked) not in (int, float) or not math.isfinite(checked)
                or not 0 <= time.time() - checked <= 300):
            state, reason = 'stale', 'Readiness capture is older than 300 seconds or has an invalid timestamp'
        elif boot is None or report.get('boot_id') != boot:
            state, reason = 'stale', 'Readiness capture does not identify the current controller boot'
        elif not isinstance(sources, dict) or sources != attestation_source_hashes():
            state, reason = 'stale', 'Readiness collector source commitments are missing or changed'
        elif report.get('topology_configuration') != topology_configuration(runtime.config):
            state, reason = 'drift', 'Trust-boundary configuration differs from the captured prerequisite checks'
        else:
            state, reason = 'current', 'Current boot, collector bytes and bounded observation age match'
    sources = report.get('attestation_source_sha256')
    public_sources = {name: value for name, value in sources.items()
                      if name in {'cochem_pipeline.windows', 'cochem_pipeline.ramdisk',
                                  'cochem_pipeline.containers', 'cochem_pipeline.deployment'}
                      and isinstance(value, str) and _SHA.fullmatch(value)} if isinstance(sources, dict) else {}
    return report, {'state': state, 'reason': reason, 'checked_at': checked,
                    'source_sha256': public_sources,
                    'capture_sha256': digest(report) if report else None,
                    'max_age_seconds': 300}


def _fields(value, allowed):
    return {key: value[key] for key in allowed if key in value} if isinstance(value, dict) else {}


def deployment_view(runtime, jobs, health):
    config = runtime.config
    workers = getattr(config, 'workers', {})
    report, capture = _topology_capture(runtime)
    checks = report.get('checks', {})
    checks = checks if isinstance(checks, dict) else {}
    controller = {}
    controller_state = 'unknown'
    if health.get('pid') == os.getpid():
        try:
            from .deployment import attest_controller_identity
            controller = attest_controller_identity()
            controller_state = 'current' if controller['is_system'] else 'drift'
        except (OSError, ValueError, RuntimeError):
            pass
    nodes = [{'id': 'controller', 'kind': 'controller', 'configured_identity': 'SYSTEM',
              'configured_token_sid': 'S-1-5-18', 'observed_identity': controller,
              'attestation_state': controller_state,
              'observed_pid': health.get('pid'), 'instance_id': health.get('instance_id')},
             {'id': 'database', 'kind': 'private_database', 'configured_path': str(getattr(config, 'job_db', runtime.store.path))}]
    edges = [{'from': 'controller', 'to': 'database', 'transport': 'SQLite'}]
    drift = []
    if controller and controller.get('token_sid') != 'S-1-5-18':
        drift.append({'node': 'controller', 'field': 'token_sid', 'configured': 'S-1-5-18',
                      'observed': controller.get('token_sid')})
    receipts = [job['receipt'] for job in jobs if isinstance(job.get('receipt'), dict)]
    for number, (slot, identity) in enumerate(sorted(workers.items())):
        captured = [receipt for receipt in receipts if receipt.get('worker_slot') == slot
                    and receipt.get('execution_kind') == 'native_cli'
                    and receipt.get('process_identity_source') == 'owned_windows_process_handle']
        accounts = sorted({receipt['worker_account'] for receipt in captured if receipt.get('worker_account')})
        expected = identity.get('name')
        node_id = f'worker{number}'
        nodes.append({'id': node_id, 'kind': 'worker', 'slot': slot, 'configured_identity': expected,
            'configured_path': str(getattr(config, 'slot_roots', {}).get(slot, '')),
            'observed_accounts': accounts, 'attestation': 'native_receipt' if captured else 'not_observed',
            'observed_prompt_transports': sorted({item['prompt_transport'] for item in captured if item.get('prompt_transport')})})
        edges.append({'from': 'controller', 'to': node_id, 'transport': 'protected prompt transport'})
        if expected and any(account.casefold() != expected.casefold() for account in accounts):
            drift.append({'slot': slot, 'field': 'worker_account', 'configured': expected, 'observed': accounts})
    docker = getattr(runtime, 'docker', None)
    if docker is not None:
        trusted_sids = {'S-1-5-18'}
        operator = getattr(config, 'operator_name', None)
        operator_sid_known = not operator
        if operator:
            try:
                from . import windows as win
                trusted_sids.add(win._sid_text(win._account_sid(operator)))
                operator_sid_known = True
            except (OSError, ValueError, RuntimeError):
                pass
        docker_check = checks.get('docker', {})
        docker_check = docker_check if isinstance(docker_check, dict) else {}
        evidence = docker_check.get('evidence', {})
        evidence = evidence if isinstance(evidence, dict) else {}
        boundary = evidence.get('worker_access', {})
        boundary = boundary if isinstance(boundary, dict) else {}
        pipes = []
        for endpoint, records in list(boundary.items())[:16]:
            if not isinstance(records, list):
                continue
            safe = []
            for record in records[:256]:
                if not isinstance(record, dict):
                    continue
                safe.append({'slot': record.get('slot'), 'access_denied': record.get('access_denied') is True,
                    'denied_modes': [mode for mode in ('read', 'write', 'read_write')
                                     if mode in (record.get('denied_modes') or [])],
                    'server': _fields(record.get('server'),
                        ('pid', 'process_created_filetime', 'token_sid', 'executable', 'checked_at'))})
            pipes.append({'endpoint': endpoint, 'workers': safe})
        docker_state = capture['state'] if evidence else 'unknown'
        observed_engine = getattr(docker, '_daemon_identity', None)
        captured_engine = evidence.get('daemon_id')
        if captured_engine and observed_engine and captured_engine != observed_engine:
            drift.append({'node': 'docker', 'field': 'engine_id', 'configured': observed_engine, 'observed': captured_engine})
            docker_state = 'drift'
        if pipes and docker.policy.endpoint not in boundary:
            drift.append({'node': 'docker', 'field': 'endpoint', 'configured': docker.policy.endpoint,
                          'observed': [pipe['endpoint'] for pipe in pipes]})
            docker_state = 'drift'
        for pipe in pipes:
            for worker in pipe['workers']:
                server = worker['server']
                if server.get('token_sid') not in trusted_sids:
                    if operator_sid_known:
                        drift.append({'node': 'docker', 'field': 'pipe_server_token_sid',
                                      'configured': sorted(trusted_sids), 'observed': server.get('token_sid')})
                        docker_state = 'drift'
                    elif docker_state == 'current':
                        docker_state = 'unknown'
                allowed = getattr(docker.policy, 'pipe_server_executables', ())
                if allowed and server.get('executable') not in allowed:
                    drift.append({'node': 'docker', 'field': 'pipe_server_executable',
                                  'configured': list(allowed), 'observed': server.get('executable')})
                    docker_state = 'drift'
                if worker.get('access_denied') is not True or set(worker.get('denied_modes') or ()) != {'read', 'write', 'read_write'}:
                    drift.append({'node': 'docker', 'field': 'worker_access_denial',
                                  'configured': True, 'observed': worker})
                    docker_state = 'drift'
        observed_slots = {record.get('slot') for pipe in pipes for record in pipe['workers']}
        if docker_state == 'current' and (docker_check.get('ready') is not True
                or not captured_engine or not observed_engine
                or not pipes or not set(workers) <= observed_slots):
            docker_state = 'unknown'
        nodes.append({'id': 'docker', 'kind': 'container_engine', 'configured_endpoint': docker.policy.endpoint,
                      'observed_engine_id': observed_engine, 'captured_engine_id': captured_engine,
                      'pipe_access_evidence': pipes, 'attestation_state': docker_state,
                      'configured_server_token_sids': sorted(trusted_sids),
                      'repair_access_state': 'captured' if any(str(slot).startswith('repair') for slot in observed_slots) else 'unknown',
                      'capture': capture,
                      'attestation': 'completed_engine_preflight' if observed_engine else 'not_observed'})
        edges.append({'from': 'controller', 'to': 'docker', 'transport': docker.policy.endpoint})
    ram = getattr(config, 'ramdisk', None)
    if ram is not None:
        ram_check = checks.get('ramdisk', {})
        ram_check = ram_check if isinstance(ram_check, dict) else {}
        evidence = ram_check.get('evidence', {})
        evidence = evidence if isinstance(evidence, dict) else {}
        observed = _fields(evidence.get('observed'), ('device_number', 'target', 'size_bytes', 'backing',
            'nonpageable', 'drive_letter', 'filesystem', 'volume_serial'))
        ram_state = capture['state'] if observed else 'unknown'
        comparisons = [('mount_root', ram.mount_root, evidence.get('mount_root')),
                       ('size_bytes', ram.size_mb * 1024 * 1024, observed.get('size_bytes')),
                       ('filesystem', 'NTFS', observed.get('filesystem'))]
        backing = getattr(ram, 'backing', 'auto')
        if backing != 'auto':
            comparisons.append(('backing', backing, observed.get('backing')))
        for field, expected, actual in comparisons:
            if actual is not None and str(actual).casefold().rstrip('\\/') != str(expected).casefold().rstrip('\\/'):
                drift.append({'node': 'ram', 'field': field, 'configured': expected, 'observed': actual})
                ram_state = 'drift'
        if ram_state == 'current' and (ram_check.get('ready') is not True or
                any(key not in observed for key in ('device_number', 'target', 'size_bytes', 'backing', 'filesystem', 'volume_serial'))):
            ram_state = 'unknown'
        nodes.append({'id': 'ram', 'kind': 'ram_volume', 'configured_path': ram.mount_root,
                      'configured_size_mb': ram.size_mb, 'configured_backing': backing,
                      'observed_path': evidence.get('mount_root'), 'observed_device': observed,
                      'attestation_state': ram_state, 'capture': capture,
                      'runtime_verified': ram_state == 'current'})
        edges.append({'from': 'controller', 'to': 'ram', 'transport': 'protected scratch'})
    def label(value):
        return str(value).replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', ' ')[:240]
    diagram = ['flowchart LR']
    for node in nodes:
        detail = node.get('observed_engine_id') or node.get('configured_path') or node.get('configured_identity') or node['kind']
        state = node.get('attestation_state', node.get('attestation', 'unknown'))
        diagram.append(f'  {node["id"]}["{label(node["kind"])}: {label(detail)} ({label(state)})"]')
    diagram.extend(f'  {edge["from"]} -->|"{label(edge["transport"])}"| {edge["to"]}' for edge in edges)
    return {'nodes': nodes, 'edges': edges, 'mermaid': '\n'.join(diagram), 'observed_drift': drift,
            'receipt_window_complete': False, 'unobserved_is_healthy': False}


def operator_snapshot(runtime, workflow_id=None, *, job_id=None, after_event_id=None, source_root=None, history=None):
    """Build a bounded projection under existing HTTP bearer authentication."""
    for identifier in (workflow_id, job_id):
        if identifier is not None and (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', identifier)):
            raise ValueError('Invalid workflow or job ID')
    if workflow_id is not None and job_id is not None:
        raise ValueError('Select a workflow or a job, not both')
    if after_event_id is not None and (type(after_event_id) is not int or after_event_id < 0):
        raise ValueError('Event cursor must be a nonnegative integer')
    now = time.time()
    health = runtime.status()
    hardware = health.get('admission') or health.get('hardware', {})
    with runtime.store._connection() as conn:
        conn.execute('BEGIN')
        if job_id is not None:
            workflow_id = runtime.store._get(conn, job_id)['workflow_id']
        if workflow_id is not None:
            root = runtime.store._get(conn, workflow_id)
            if root['kind'] not in {'CODE_REQUEST', 'MACRO_PLANNING_REQUEST'}:
                raise ValueError('ID does not identify a workflow root')
        where, params = (('WHERE job_id=?', (job_id,)) if job_id else
                         ('WHERE workflow_id=?', (workflow_id,)) if workflow_id else ('', ()))
        count = conn.execute(f'SELECT count(*) FROM pipeline_jobs {where}', params).fetchone()[0]
        identifiers = conn.execute(f'''SELECT job_id FROM pipeline_jobs {where} ORDER BY
            CASE WHEN kind IN ('CODE_REQUEST','MACRO_PLANNING_REQUEST') THEN 3
                 WHEN status='IN_PROGRESS' THEN 0
                 WHEN status IN ('PENDING','PENDING_RETRY','BLOCKED') THEN 1 ELSE 2 END,
            CASE WHEN status IN ('PENDING','PENDING_RETRY','BLOCKED') THEN created_at ELSE -created_at END,
            job_id LIMIT 200''', params).fetchall()
        jobs = [runtime.store._get(conn, row['job_id']) for row in identifiers]
        from .routing_store import preview_next_route
        route_previews = {job['job_id']: preview_next_route(conn, job, runtime.store.routing_policy, now=now)
                          for job in jobs if job['kind'] not in {'MACRO_PLANNING_REQUEST', 'CODE_REQUEST'}}
        event_where = where + (' AND ' if where else ' WHERE ') + 'id>?' if after_event_id is not None else where
        event_params = (*params, after_event_id) if after_event_id is not None else params
        event_count = conn.execute(f'SELECT count(*) FROM pipeline_events {event_where}', event_params).fetchone()[0]
        event_order = 'ASC' if after_event_id is not None else 'DESC'
        events = [runtime.store._decode_event(row) for row in conn.execute(
            f'SELECT * FROM pipeline_events {event_where} ORDER BY id {event_order} LIMIT 200', event_params)]
        events.sort(key=lambda event: event['id'])
        state_rows = conn.execute('SELECT workflow_id,state_json FROM coding_workflows' +
                                  (' WHERE workflow_id=?' if workflow_id else ' WHERE workflow_id IN (SELECT workflow_id FROM pipeline_jobs ORDER BY created_at DESC LIMIT 200)'),
                                  (workflow_id,) if workflow_id else ()).fetchall()
        coding = {row['workflow_id']: json.loads(row['state_json']) for row in state_rows}
        synthesis_blocks = {}
        for job in jobs:
            if job['kind'] == 'SYNTHESIS' and job['status'] == 'BLOCKED':
                chapters = conn.execute("SELECT count(*) FROM pipeline_jobs WHERE workflow_id=? AND kind='CHAPTER_DRAFT'", (job['workflow_id'],)).fetchone()[0]
                pending = conn.execute("SELECT job_id FROM pipeline_jobs WHERE workflow_id=? AND kind='CHAPTER_DRAFT' AND status!='COMPLETED' ORDER BY job_id LIMIT 200", (job['workflow_id'],)).fetchall()
                pending_count = conn.execute("SELECT count(*) FROM pipeline_jobs WHERE workflow_id=? AND kind='CHAPTER_DRAFT' AND status!='COMPLETED'", (job['workflow_id'],)).fetchone()[0]
                synthesis_blocks[job['job_id']] = {'state': 'blocked', 'reason': 'Accepted chapter artifacts are required' if chapters
                    else 'An accepted manifest must create the chapter jobs', 'prerequisite_job_ids': [row['job_id'] for row in pending],
                    'prerequisite_count': pending_count, 'prerequisite_ids_truncated': pending_count > len(pending)}
    governing = []
    for job in jobs:
        capture = (coding.get(job['workflow_id'], {}).get('planning_evidence')
                   or job.get('payload', {}).get('governing_requirements') or {})
        transition = job.get('payload', {}).get('execution_transition', {})
        governing.append({'job_id': job['job_id'], 'workflow_id': job['workflow_id'],
            'requirement_ids': job.get('payload', {}).get('requirements', []),
            'captured_specification_id': capture.get('specification_id'),
            'captured_specification_revision': capture.get('specification_revision'),
            'captured_specification_sha256': capture.get('specification_sha256'),
            'captured_contract_sha256': transition.get('contract_sha256', capture.get('contract_sha256')),
            'captured_contract_type': capture.get('contract_type') or ('coding' if job['workflow_id'] in coding else None),
            'captured_contract_schema': capture.get('contract', {}).get('schema'),
            'captured_source_manifest_sha256': capture.get('source_manifest_sha256'),
            'captured_project_policy_sha256': capture.get('policy_sha256'),
            'captured_routing_policy_sha256': (job.get('routing') or {}).get('policy_digest'),
            'latest_admission_policy_sha256': (job.get('route') or {}).get('admission_policy_digest'),
            'captured_owner_amendments': capture.get('owner_amendments'),
            'capture_note': 'Null fields were not captured; current specification is not substituted for historical evidence'})
    timeline = [{'id': event['id'], 'at': event['timestamp'], 'job_id': event['job_id'], 'event': event['event'],
                 'reason': event['details'].get('reason', event['details'].get('error'))} for event in events]
    samples = [event['details'].get('telemetry', {}).get('host_sample') for event in events] + [hardware]
    if source_root is None:
        source_root = Path(__file__).resolve().parents[2]
        if not (source_root / '4.2.7_SRS.md').is_file():
            source_root = Path(__file__).resolve().parent / 'specification'
    dashboard = acceptance_dashboard(source_root)
    current_contract = digest(execution_contract())
    from .document_governance import execution_contract as document_contract
    current_contracts = {'coding': current_contract,
                         **{kind: digest(document_contract(kind)) for kind in ('document_plan', 'provider_preflight')}}
    current_routing = digest(runtime.store.routing_policy) if runtime.store.routing_policy is not None else None
    for row in governing:
        for key, captured, current in (
                ('matches_current_specification', row['captured_specification_sha256'], dashboard['specification_sha256']),
                ('matches_current_contract', row['captured_contract_sha256'], current_contracts.get(row['captured_contract_type'])),
                ('matches_current_routing_policy', row['captured_routing_policy_sha256'], current_routing),
                ('latest_admission_matches_current_policy', row['latest_admission_policy_sha256'], current_routing)):
            row[key] = captured == current if captured is not None and current is not None else None
    result = {'schema': 'cochem-operator/4.2.7', 'read_only': True, 'generated_at': now,
        'workflow_id': workflow_id, 'job_id': job_id, 'window': {'job_limit': 200, 'event_limit': 200,
            'jobs_total': count, 'jobs_truncated': count > len(jobs), 'events_available': event_count,
            'job_order': 'executing, oldest waiting, recent terminal, workflow aggregates',
            'events_truncated': event_count > len(events), 'after_event_id': after_event_id,
            'next_event_cursor': max((event['id'] for event in events), default=after_event_id or 0)},
        'current_specification': {'id': SPECIFICATION_ID, 'sha256': dashboard['specification_sha256'],
                                  'contract_sha256': current_contract, 'routing_policy_sha256': current_routing,
                                  'execution_contract_hashes': current_contracts,
                                  'revision': dashboard['revision']},
        'governing_requirements': governing, 'timeline': timeline,
        'coding_states': [{'workflow_id': identifier, **{key: state.get(key) for key in
            ('status', 'planning_hold', 'leaf_index', 'cycle', 'integration_reconciliation_required')}}
            for identifier, state in coding.items()],
        'prerequisites': [{'job_id': job['job_id'], **(synthesis_blocks.get(job['job_id']) or _blocked(job, jobs, hardware))} for job in jobs],
        'queue': [_queue(job, health.get('routing', {}).get('holds', []), now, runtime.store.routing_policy) for job in jobs
                  if job['kind'] not in {'MACRO_PLANNING_REQUEST', 'CODE_REQUEST'}],
        'resources': resource_plot(samples), 'deployment': deployment_view(runtime, jobs, health),
        'acceptance': dashboard}
    for row in result['queue']:
        preview = route_previews[row['job_id']]
        preview['host_admission_capacity'] = hardware.get('capacity')
        preview['host_admission_reasons'] = hardware.get('reasons', [])
        preview['host_admission_available'] = (hardware['capacity'] > 0 if type(hardware.get('capacity')) is int else None)
        row['eligibility_preview'] = preview
    oracle = getattr(runtime, 'oracle', None)
    if oracle is not None and hasattr(oracle, 'decision_log'):
        try:
            result['oracle_decisions'] = oracle.decision_log(limit=100)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            result['oracle_decisions'] = {'available': False, 'reason': type(error).__name__}
    docker = getattr(runtime, 'docker', None)
    if docker is not None and hasattr(docker, 'pool_target_snapshot'):
        try:
            result['prepared_pool'] = docker.pool_target_snapshot()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            result['prepared_pool'] = {'available': False, 'reason': type(error).__name__}
    ram = getattr(runtime, 'ramdisk', None)
    if ram is not None and hasattr(ram, 'observation'):
        try:
            result['ram_volume'] = ram.observation()
        except (OSError, ValueError, RuntimeError) as error:
            result['ram_volume'] = {'available': False, 'reason': type(error).__name__,
                                    'native_volume_attested': False}
    objectives = getattr(runtime, 'objectives', None)
    if objectives is not None and hasattr(objectives, 'snapshot'):
        try:
            result['workload_objectives'] = objectives.snapshot()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            result['workload_objectives'] = {'available': False, 'reason': type(error).__name__}
    private_root = getattr(runtime.config, 'private_root', None)
    if private_root is not None:
        from .operations_policy import storage_forecast
        try:
            result['retention'] = (history.retention_snapshot(private_root) if history is not None
                                   else storage_forecast(private_root, max_files=256))
        except (OSError, ValueError) as error:
            result['retention'] = {'available': False, 'reason': type(error).__name__,
                                   'pruning_supported': False}
    return result
