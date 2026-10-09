"""Prepare one NEW private corpus and disposable index, never protected/live state.

Source byte copies and private metadata only. A process-local, staging-only
permission fixture exposes production KnowledgeService content/index validation
without claiming Windows SYSTEM or installed ACL acceptance.
"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

STAGE = Path(r'C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006')
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
PRESERVED = STAGE / 'legacy-file-preservation-20261007'
PINS = {
    REPO / 'src/cochem_pipeline/knowledge.py': 'f7b14fc86a7e0739f7788b5710bda09f330d0b1032d403942708fdb415071b76',
    REPO / 'src/cochem_pipeline/knowledge_authority.py': 'e7dd99cb0f9a6710d3a03cff2d983a58879fef0b68bd463c0f38607603ba1f27',
    REPO / 'knowledge/v4.1.2_manifest.json': 'cbde9b4d9d5be3ffdb21603c4cfeabe963a9563ab59dec734b8a125a0fcb7ca6',
    PRESERVED / 'preservation-manifest.json': 'eb98369eadd5a9d0ba7d862f832331d8f2bdf6b15e67021fe300f4cd4d485978',
    STAGE / 'legacy-online-snapshots-20261007/02-knowledge_index.db': '7252d5007734de6c34fe13eb86323352d10121e504e0bff79ed3589848e0305f',
    STAGE / 'legacy-online-snapshots-20261007/04-knowledge_index.db': '7252d5007734de6c34fe13eb86323352d10121e504e0bff79ed3589848e0305f',
    STAGE / 'knowledge-continuation-path-map-20261007T052727Z.json': '87bacc50335a69da0d71e7f1010723d394eb78e8070234fcf4e03667224c582a',
    STAGE / 'knowledge-continuation-missing-search-20261007T052830Z.json': 'ba1ee78a2d5506945a87a1eec073426172f333132a27afadd61e2d325c8fb11c',
}


def stable_bytes(path, limit=8 * 1048576):
    for node in (path, *path.parents):
        info = node.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Source or destination ancestry contains a reparse point')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
        raise ValueError('Source must be a bounded ordinary file')
    raw = path.read_bytes()
    after = path.stat()
    if len(raw) > limit or (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Source changed during copy/hash read')
    return raw


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode()


def main():
    captured = {path: stable_bytes(path) for path in PINS}
    if any(sha(raw) != PINS[path] for path, raw in captured.items()):
        raise ValueError('A reviewed input/source changed; do not rebuild under different custody')
    sys.path.insert(0, str(REPO / 'src'))
    from cochem_pipeline import knowledge
    from cochem_pipeline.knowledge_authority import KnowledgeAuthority
    from cochem_pipeline.planning_governance import canonical_authority
    original = json.loads(captured[REPO / 'knowledge/v4.1.2_manifest.json'])
    preservation = json.loads(captured[PRESERVED / 'preservation-manifest.json'])
    selected = [row for row in preservation['files'] if row['copy'].split('/')[0]
                in ('03-wiki', '07-wiki') and Path(row['copy']).suffix.lower() == '.md']
    if len(original['documents']) != 11 or len(selected) != 221:
        raise ValueError('Reviewed public/private capture count changed')
    historical = {}
    provenance = []
    for row in selected:
        source = PRESERVED / row['copy']
        if PRESERVED not in source.resolve().parents:
            raise ValueError('Preserved source path escapes its private root')
        raw = stable_bytes(source, 1048576)
        if sha(raw) != row['sha256']:
            raise ValueError('Preserved historical capture changed')
        text = raw.decode('utf-8', 'strict')
        if not text.strip() or '\0' in text:
            raise ValueError('Historical capture cannot be represented as registered Markdown')
        historical.setdefault(row['sha256'], raw)
        provenance.append({'original_path': row['source'], 'preserved_copy': row['copy'],
            'sha256': row['sha256'], 'bytes': len(raw),
            'registered_path': '.sources/legacy/sha256/' + row['sha256'] + '.md',
            'authority': 'historical_source', 'authority_revision': 'legacy-preservation-2026-10-07'})
    if len(historical) != 126:
        raise ValueError('Reviewed unique historical capture count changed')
    root = STAGE / ('knowledge-continuation-candidate-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    root.mkdir(exist_ok=False)
    corpus, custody, validation = root / 'corpus', root / 'custody', root / 'validation'
    corpus.mkdir(); custody.mkdir(); validation.mkdir()
    write_new(custody / 'builder.py', stable_bytes(Path(__file__)))
    write_new(custody / 'original-public-manifest.json', captured[REPO / 'knowledge/v4.1.2_manifest.json'])
    for row in original['documents']:
        key = knowledge._key(row['path'])
        raw = stable_bytes(REPO / 'knowledge' / key, 1048576)
        if sha(raw) != row['sha256']:
            raise ValueError('Canonical public document changed')
        write_new(corpus / key, raw)
    candidate = deepcopy(original)
    for digest, raw in sorted(historical.items()):
        key = '.sources/legacy/sha256/' + digest + '.md'
        write_new(corpus / key, raw)
        candidate['documents'].append({'path': key, 'sha256': digest,
            'authority': 'historical_source', 'authority_revision': 'legacy-preservation-2026-10-07'})
    candidate['private_continuation'] = {
        'kind': 'additive_historical_archive_candidate', 'public_catalog_sha256': PINS[REPO / 'knowledge/v4.1.2_manifest.json'],
        'legacy_preservation_manifest_sha256': PINS[PRESERVED / 'preservation-manifest.json'],
        'unique_legacy_captures': 126, 'legacy_indexed_originals_fully_recovered': False,
        'custody': 'Original path mappings and explicit indexed-byte gaps retained privately outside registered corpus',
    }
    write_new(corpus / 'v4.1.2_manifest.json', json_bytes(candidate))
    write_new(custody / 'historical-source-provenance.json', json_bytes({
        'schema': 'cochem-private-historical-capture-provenance/1',
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'preservation_manifest_sha256': PINS[PRESERVED / 'preservation-manifest.json'],
        'logical_capture_count': 221, 'unique_registered_raw_sources': 126,
        'original_databases_modified': False, 'fts_fragment_reconstruction_used': False,
        'records': provenance}))
    for name in ('knowledge-continuation-path-map-20261007T052727Z.json',
                 'knowledge-continuation-missing-search-20261007T052830Z.json'):
        write_new(custody / name, captured[STAGE / name])
    write_new(custody / 'input-lineage.json', json_bytes({
        'schema': 'cochem-private-knowledge-candidate-lineage/1',
        'inputs': [{'path': str(path), 'sha256': digest, 'bytes': len(captured[path])} for path, digest in PINS.items()],
        'snapshots_are_individually_consistent_not_cross_file_cutover': True,
        'source_files_modified': False, 'historical_indexed_bytes_not_recovered_from_fts': True,
        'future_frozen_config_targets_unchanged': {
            'corpus': r'C:\Program Files\CoChem\Knowledge4.2.7-windows-20261006',
            'index_state': r'C:\ProgramData\CoChemPipeline427\private\knowledge-windows-20261006'}}))
    before = {row['path']: sha(stable_bytes(corpus / row['path'], 1048576)) for row in candidate['documents']}
    permission_checks = 0
    def staging_permissions(path, *, directory=False, private=False):
        nonlocal permission_checks
        path = Path(path).absolute()
        if root != path and root not in path.parents:
            raise ValueError('Portable permission fixture cannot inspect outside the NEW private candidate')
        permission_checks += 1
        return knowledge._ordinary(path, directory=directory)
    original_protected = knowledge._protected
    knowledge._protected = staging_permissions
    try:
        config = knowledge.KnowledgeConfig(enabled=True, source_root=str(corpus / '.sources'),
            wiki_root=str(corpus / 'wiki'), manifest_path=str(corpus / 'v4.1.2_manifest.json'),
            state_root=str(validation / 'knowledge-index'))
        service = knowledge.KnowledgeService(config)
        try:
            refreshed = service.refresh()
            accepted = KnowledgeAuthority(service, canonical_authority()).status(force=True)
            pins_before = sha(stable_bytes(service.state / 'sources.json'))
            repeated = service.refresh()
            pins_after = sha(stable_bytes(service.state / 'sources.json'))
            with service._reader() as (generation, database):
                integrity = database.execute('PRAGMA integrity_check').fetchone()[0]
                indexed_documents = database.execute('SELECT count(*) FROM documents').fetchone()[0]
                indexed_sections = database.execute('SELECT count(*) FROM fts_index').fetchone()[0]
            _, index_path = service._current()
            after = {row['path']: sha(stable_bytes(corpus / row['path'], 1048576)) for row in candidate['documents']}
            if (before != after or pins_before != pins_after or repeated['changed_documents'] != 0
                    or indexed_documents != 137 or integrity != 'ok' or not accepted['ready']):
                raise ValueError('Candidate content/index/authority validation failed')
            result = {'schema': 'cochem-private-knowledge-candidate-validation/1',
                'status': 'PRIVATE_CANDIDATE_CONTENT_VALIDATED_NOT_DEPLOYED', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'candidate_root': str(root), 'corpus_root': str(corpus), 'disposable_index_state': str(service.state),
                'system_acceptance': False, 'protected_installation_modified': False, 'live_database_modified': False,
                'permission_boundary': 'Explicit process-local staging-only ordinary-file validation fixture; Windows SYSTEM/ACL acceptance NOT RUN',
                'permission_fixture_checks': permission_checks,
                'public_entries_preserved': candidate['documents'][:11] == original['documents'],
                'public_srs_documents_preserved': candidate['srs_documents'] == original['srs_documents'],
                'public_documents': 11, 'historical_unique_raw_captures': 126, 'historical_logical_captures': 221,
                'documents': indexed_documents, 'sections': indexed_sections,
                'content_bytes': refreshed['corpus_bytes'], 'index_bytes': refreshed['index_bytes'],
                'index_size_sla_met': refreshed['index_size_sla_met'], 'index_integrity_check': integrity,
                'manifest_sha256': sha(stable_bytes(corpus / 'v4.1.2_manifest.json')),
                'source_pins_sha256': pins_after, 'index_sha256': sha(stable_bytes(index_path)),
                'sources_unchanged_after_validation': before == after, 'source_pins_unchanged_on_repeat_refresh': pins_before == pins_after,
                'repeat_refresh_changed_documents': repeated['changed_documents'],
                'canonical_authority_matches_capture': accepted['authority_matches_capture'],
                'canonical_source': accepted['authority_source'],
                'owner_amendment_sources': [row['resolved_source'] for row in accepted['owner_amendments']],
                'legacy_full_continuity_verified': False,
                'indexed_live_originals': {'total': 86, 'exact_hash_match': 47, 'changed_hash': 36, 'missing': 3},
                'missing_indexed_wiki_originals_recovered': 0, 'fts_fragment_reconstruction_used': False,
                'native_model_jobs_executed': 0}
        finally:
            service.close()
    finally:
        knowledge._protected = original_protected
    if any(sha(stable_bytes(path)) != digest for path, digest in PINS.items()):
        raise ValueError('Input custody changed during validation')
    result['reviewed_input_hashes_unchanged'] = True
    result['private_custody_files'] = {path.name: sha(stable_bytes(path)) for path in custody.iterdir() if path.is_file()}
    receipt = root / 'validation-evidence.json'
    write_new(receipt, json_bytes(result))
    print(json.dumps({key: result[key] for key in ('status', 'candidate_root', 'documents', 'sections',
        'content_bytes', 'index_bytes', 'index_size_sla_met', 'manifest_sha256', 'source_pins_sha256',
        'index_sha256', 'canonical_authority_matches_capture', 'canonical_source', 'owner_amendment_sources',
        'legacy_full_continuity_verified', 'system_acceptance')} | {
        'evidence_path': str(receipt), 'evidence_sha256': sha(stable_bytes(receipt))}))


if __name__ == '__main__':
    main()
