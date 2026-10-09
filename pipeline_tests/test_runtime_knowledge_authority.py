"""Real FTS5/source parity and SQLite admission checks, not Windows attestation."""
from __future__ import annotations

from importlib.resources import files
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

from cochem_pipeline.knowledge import KnowledgeConfig, KnowledgeError, KnowledgeService
from cochem_pipeline.knowledge_authority import KnowledgeAuthority
from cochem_pipeline.planning_governance import canonical_authority
from pipeline_tests.test_service import endpoint


@pytest.fixture
def portable_knowledge_permissions(monkeypatch):
    # These tests exercise real ordinary files, hashes, FTS5 and generation
    # changes. The explicit permission boundary is portable; it is not a
    # Windows SYSTEM/ACL acceptance test or a production guard bypass.
    from cochem_pipeline import knowledge
    monkeypatch.setattr(knowledge, '_protected',
        lambda path, *, directory=False, private=False: knowledge._ordinary(path, directory=directory))
    monkeypatch.setattr(knowledge, '_private_directory',
        lambda path, *, exist_ok=False: path.mkdir(mode=0o700, exist_ok=exist_ok))


@pytest.fixture
def registered(tmp_path, portable_knowledge_permissions):
    services = []

    def build(*, old_spec=False, old_amendment=False, catalog_edit=None, committed=False):
        root = tmp_path / ('corpus-' + str(len(services)))
        private = tmp_path / ('private-' + str(len(services)))
        root.mkdir(); private.mkdir(mode=0o700)
        installed = files('cochem_pipeline').joinpath('specification')
        documents = {'.sources/4.2.7_SRS.md': installed.joinpath('4.2.7_SRS.md').read_bytes(),
            '.sources/SRS_ADDENDUM_4.2.7.md': installed.joinpath('SRS_ADDENDUM_4.2.7.md').read_bytes(),
            'wiki/00_skeleton.md': b'# Canonical\n[SRS](../.sources/4.2.7_SRS.md)\n[Decisions](../.sources/SRS_ADDENDUM_4.2.7.md)\n'}
        if old_spec:
            documents['.sources/4.2.7_SRS.md'] += b'\nOlder incompatible source capture.\n'
        if old_amendment:
            documents['.sources/SRS_ADDENDUM_4.2.7.md'] += b'\nOlder incompatible owner amendment.\n'
        revision = canonical_authority()['specification_revision']
        entries = []
        for path, raw in documents.items():
            target = root / path
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(raw)
            entries.append({'path': path, 'sha256': hashlib.sha256(raw).hexdigest(),
                'authority': 'current_normative' if path.endswith('4.2.7_SRS.md') else
                    'owner_decision' if 'ADDENDUM' in path else 'unclassified', 'authority_revision': revision})
        manifest = root / 'v4.1.2_manifest.json'
        manifest.write_text(json.dumps({'manifest_version': '4.2.7', 'documents': entries,
                                        'srs_documents': ['wiki/00_skeleton.md']}))
        if committed:
            # Copy exact committed archive/manifest bytes into this disposable
            # fixture. Existing historical source pins in the repo never change.
            shutil.rmtree(root)
            shutil.copytree(Path(__file__).resolve().parents[1] / 'knowledge', root)
        if catalog_edit:
            catalog = json.loads(manifest.read_text(encoding='utf-8'))
            catalog_edit(root, catalog)
            manifest.write_text(json.dumps(catalog), encoding='utf-8')
        config = KnowledgeConfig(enabled=True, source_root=str(root / '.sources'),
            wiki_root=str(root / 'wiki'), state_root=str(private / 'knowledge'), manifest_path=str(manifest))
        service = KnowledgeService(config)
        service.refresh()
        services.append(service)
        return SimpleNamespace(service=service, root=root, private=private, manifest=manifest,
            authority=KnowledgeAuthority(service, canonical_authority()))

    yield build
    for service in services:
        service.close()


def test_actual_installed_specification_and_owner_amendment_match_registered_bytes(registered):
    corpus = registered()
    result = corpus.authority.status()
    assert result['ready'] and result['index_ready']
    assert result['authority_matches_capture'] is True
    assert result['owner_amendments'][0]['matches_capture'] is True
    assert result['observed_specification_sha256'] == canonical_authority()['specification_sha256']
    assert result['authority_source'] == '.sources/4.2.7_SRS.md'
    assert result['owner_amendments'][0]['resolved_source'] == '.sources/SRS_ADDENDUM_4.2.7.md'


def test_actual_committed_versioned_catalog_resolves_current_bytes_without_rewriting_history(registered):
    corpus = registered(committed=True)
    manifest = corpus.manifest.read_bytes()
    pins = (corpus.service.state / 'sources.json').read_bytes()
    old_sources = {name: (corpus.root / '.sources' / name).read_bytes()
        for name in ('4.2.7_SRS.md', 'SRS_ADDENDUM_4.2.7.md')}
    result = corpus.authority.status()
    assert result['ready'] and result['authority_matches_capture'] is True
    assert result['authority_source'] == '.sources/4.2.7_SRS.model-routing-2026-10-07.md'
    assert result['owner_amendments'][0]['resolved_source'] == '.sources/SRS_ADDENDUM_4.2.7.model-routing-2026-10-07.md'
    assert corpus.service.read('.sources/4.2.7_SRS.md')['authority'] == 'historical_source'
    assert corpus.manifest.read_bytes() == manifest
    assert (corpus.service.state / 'sources.json').read_bytes() == pins
    assert all((corpus.root / '.sources' / name).read_bytes() == raw for name, raw in old_sources.items())


@pytest.mark.parametrize('source', ['4.2.7_SRS.md', 'SRS_ADDENDUM_4.2.7.md'])
def test_duplicate_exact_captured_source_is_ambiguous_and_blocks_admission(registered, source):
    def duplicate(root, catalog):
        row = next(row for row in catalog['documents'] if row['path'] == '.sources/' + source)
        alias = '.sources/duplicate-' + source
        (root / alias).write_bytes((root / row['path']).read_bytes())
        catalog['documents'].append({**row, 'path': alias})
    corpus = registered(catalog_edit=duplicate)
    result = corpus.authority.status()
    assert result['index_ready'] and not result['ready']
    assert result['authority_matches_capture'] is False
    assert 'no unique captured source authority' in result['authority_reason']


@pytest.mark.parametrize('field,value', [('authority', 'historical_source'), ('authority_revision', 'prior-revision')])
def test_current_amendment_requires_its_exact_authority_and_revision(registered, field, value):
    def change(_, catalog):
        next(row for row in catalog['documents'] if 'ADDENDUM' in row['path'])[field] = value
    result = registered(catalog_edit=change).authority.status()
    assert result['index_ready'] and not result['ready']
    assert result['authority_matches_capture'] is False


def test_matching_wiki_copy_cannot_replace_missing_archive_authority(registered):
    raw = b'# Captured fixture requirement\nThis source exists only as a wiki entry.\n'
    captured = canonical_authority()
    captured['specification_sha256'] = hashlib.sha256(raw).hexdigest()
    def wiki_only(root, catalog):
        row = next(row for row in catalog['documents'] if row['path'] == '.sources/4.2.7_SRS.md')
        alias = 'wiki/current-copy.md'
        (root / alias).write_bytes(raw)
        catalog['documents'].append({**row, 'path': alias, 'sha256': captured['specification_sha256']})
        row['authority'] = 'historical_source'
    corpus = registered(catalog_edit=wiki_only)
    result = KnowledgeAuthority(corpus.service, captured).status()
    assert result['index_ready'] and not result['ready']
    assert result['authority_matches_capture'] is False


@pytest.mark.parametrize('metadata_field,changed', [('authority', 'historical_source'), ('authority_revision', 'older-revision')])
def test_refresh_generation_rechecks_changed_authority_metadata_immediately(registered, metadata_field, changed):
    corpus = registered()
    initial = corpus.authority.status()
    manifest = json.loads(corpus.manifest.read_text())
    manifest['documents'][0][metadata_field] = changed
    corpus.manifest.write_text(json.dumps(manifest))
    corpus.service.refresh()
    result = corpus.authority.status()  # No forced/timed check; generation drift is sufficient.
    assert result['generation'] != initial['generation']
    assert result['index_ready'] is True
    assert not result['ready'] and result['authority_matches_capture'] is False


@pytest.mark.parametrize('setting', ['old_spec', 'old_amendment'])
def test_valid_but_older_source_corpus_stays_unready_without_overwriting_pins(registered, setting):
    corpus = registered(**{setting: True})
    pins = (corpus.service.state / 'sources.json').read_bytes()
    result = corpus.authority.status()
    assert result['index_ready'] and not result['ready']
    assert result['authority_matches_capture'] is False
    assert 'fresh versioned corpus' in result['authority_reason']
    assert (corpus.service.state / 'sources.json').read_bytes() == pins


def test_physical_source_tampering_is_unknown_and_never_an_inherited_ready_state(registered):
    corpus = registered()
    assert corpus.authority.status()['ready']
    source = corpus.root / '.sources/4.2.7_SRS.md'
    source.write_bytes(source.read_bytes() + b'\nUnratified changed bytes.\n')
    result = corpus.authority.status(force=True)
    assert result['authority_matches_capture'] is None
    assert not result['ready']
    assert 'cannot be verified' in result['authority_reason']


def test_same_generation_source_is_rechecked_after_thirty_seconds(registered, monkeypatch):
    from cochem_pipeline import knowledge_authority
    corpus = registered()
    assert corpus.authority.status()['ready']
    source = corpus.root / '.sources/SRS_ADDENDUM_4.2.7.md'
    source.write_bytes(source.read_bytes() + b'\nUnratified changed owner decision.\n')
    elapsed = time.monotonic() + 31
    monkeypatch.setattr(knowledge_authority, 'time', SimpleNamespace(time=time.time, monotonic=lambda: elapsed))
    assert corpus.authority.status()['authority_matches_capture'] is None


@pytest.mark.parametrize('phase', ['before_read', 'after_read', 'between_sources'])
def test_generation_publication_during_resolution_never_inherits_ready(registered, monkeypatch, phase):
    corpus = registered()
    original_read = corpus.service.read
    reads = 0

    def publish_during_read(path):
        nonlocal reads
        reads += 1
        if phase == 'before_read' and reads == 1:
            corpus.service.refresh(full=True)
        result = original_read(path)
        if (phase == 'after_read' and reads == 1) or (phase == 'between_sources' and reads == 2):
            corpus.service.refresh(full=True)
        return result

    monkeypatch.setattr(corpus.service, 'read', publish_during_read)
    result = corpus.authority.status(force=True)
    assert not result['ready'] and result['authority_matches_capture'] is None
    assert 'cannot be verified' in result['authority_reason']


def test_resolver_refuses_stale_generation_before_reading_source(registered, monkeypatch):
    corpus = registered()
    before = corpus.service.status()
    corpus.service.refresh(full=True)
    monkeypatch.setattr(corpus.service, 'read', lambda _: pytest.fail('Stale selection must not read source'))
    captured = canonical_authority()
    with pytest.raises(KnowledgeError, match='generation changed before'):
        corpus.service.read_authority_source(sha256=captured['specification_sha256'],
            authority='current_normative', authority_revision=captured['specification_revision'],
            generation=before['generation'], manifest_sha256=before['manifest_sha256'])


def test_versioned_captured_source_tamper_remains_closed(registered):
    corpus = registered(committed=True)
    assert corpus.authority.status()['ready']
    path = corpus.root / '.sources/4.2.7_SRS.model-routing-2026-10-07.md'
    path.write_bytes(path.read_bytes() + b'\nUnratified source replacement.\n')
    result = corpus.authority.status(force=True)
    assert not result['ready'] and result['authority_matches_capture'] is None


def test_authenticated_knowledge_status_reports_production_authority_hold(endpoint, registered):
    corpus = registered(old_amendment=True)
    endpoint.controller.knowledge = corpus.service
    endpoint.controller.knowledge_authority = corpus.authority
    assert corpus.service.status()['ready'] is True  # Domain index itself remains valid.
    result = endpoint.client.call('/knowledge/status')
    assert result['index_ready'] is True
    assert result['authority_matches_capture'] is False
    assert result['ready'] is False


def test_actual_controller_tick_holds_real_queue_when_active_rag_disagrees(registered, tmp_path, monkeypatch):
    from cochem_pipeline.admission import JointAdmission
    from cochem_pipeline.oracle import ContextEngine, Oracle
    from cochem_pipeline.runtime import BASELINE, Runtime
    from cochem_pipeline.store import JobStore
    from cochem_pipeline import oracle as oracle_module
    # Explicit portable SQLite tracking boundary, not a SYSTEM identity claim.
    monkeypatch.setattr(oracle_module, '_protect_tracking_path', lambda path: path)
    corpus = registered(old_spec=True)
    store = JobStore(tmp_path / 'actual-jobs.db', governing_requirements=canonical_authority())
    workflow = store.submit('Keep this real queued task unclaimed', ['REQ-1'], 1)
    runtime = Runtime.__new__(Runtime)
    runtime.store = store
    runtime.config = SimpleNamespace(job_db=store.path)
    runtime.knowledge, runtime.knowledge_authority = corpus.service, corpus.authority
    runtime.components = {}
    runtime.active = {}
    runtime.lock = threading.RLock()
    runtime._context_drain_lock = threading.Lock()
    runtime.event_cursor = 0
    # Explicit admission stimulus only: this is not a physical hardware probe.
    runtime.guard = SimpleNamespace(evaluate=lambda: {'capacity': 4, 'state': 'normal', 'action': 'none', 'reasons': []})
    runtime.admission = JointAdmission(store, None, capacity=4)
    with Oracle(ContextEngine(BASELINE, 16384, .25), tmp_path / 'oracle.db', lambda job: None) as oracle:
        runtime.oracle = oracle
        runtime.tick()
        assert runtime.last_capacity == 0
        assert runtime.components['knowledge']['state'] == 'unhealthy'
        assert 'fresh versioned corpus' in store.transition_telemetry['reasons'][0]
        jobs = store.workflow(workflow['workflow_id'])['jobs']
        assert all(job.get('attempt_id') is None for job in jobs)
        assert store.routing_status()['active_reservations'] == []
    store.close()
