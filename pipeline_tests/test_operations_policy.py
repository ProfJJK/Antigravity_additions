"""Actual protected SQLite, file copying, receipts and statistical policy checks."""
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest

from cochem_pipeline.operations_policy import RetentionPolicy, WorkloadObjectives, archive_evidence, storage_forecast
from cochem_pipeline.containers import DockerRunner, ContainerCapacityError
from cochem_pipeline.container_policy import DockerPolicy
from pipeline_tests.test_knowledge_private_directories import actual_windows_private_fixture


def policy(**changes):
    image='sha256:'+'a'*64
    return DockerPolicy.from_dict({'enabled':True,'image':image,'allowed_images':[image],
        'commands':[{'name':'unit','argv':['python','-m','pytest','-q']}],**changes})


@pytest.fixture
def private(tmp_path,request):
    if os.name == 'nt':
        # Real private ACL inheritance, with only the explicit non-SYSTEM test
        # principal mapping documented by the disposable Windows fixture.
        return request.getfixturevalue('actual_windows_private_fixture').root
    path=tmp_path/'private'
    path.mkdir(mode=0o700)
    return path


def old_receipt(private):
    path=private/'actual.receipt.json'
    path.write_text('{"execution_kind":"receipt-storage-test"}')
    os.utime(path,(1,1))
    return path


def test_archive_copies_exact_receipt_with_manifest_and_preserves_original(private):
    source=old_receipt(private)
    before=source.read_bytes()
    result=archive_evidence(private,[source.name])
    archive=Path(result['archive_directory'])
    assert source.read_bytes()==before==(archive/source.name).read_bytes()
    assert result['entries'][0]['archive_sha256']==hashlib.sha256(before).hexdigest()
    assert result['manifest_sha256']==hashlib.sha256((archive/'manifest.json').read_bytes()).hexdigest()
    assert result['originals_preserved'] is True


def test_archive_online_backup_includes_committed_wal_and_keeps_live_database(private):
    path=private/'telemetry.db'
    with closing(sqlite3.connect(path)) as live:
        live.execute('PRAGMA journal_mode=WAL')
        live.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, detail TEXT)')
        live.execute('INSERT INTO events VALUES(1,?)',('retained evidence',))
        live.commit()
        result=archive_evidence(private,[path.name])
        copy=Path(result['archive_directory'])/path.name
        with closing(sqlite3.connect(copy)) as archived:
            assert archived.execute('SELECT * FROM events').fetchall()==[(1,'retained evidence')]
        assert live.execute('SELECT count(*) FROM events').fetchone()[0]==1
    assert result['entries'][0]['source_sha256'] is None
    assert result['entries'][0]['kind']=='sqlite_online_backup'


def test_archive_rejects_new_receipts_secrets_escaping_and_pruning(private,tmp_path):
    fresh=private/'fresh.receipt.json'
    fresh.write_text('{}')
    with pytest.raises(ValueError,match='younger'):
        archive_evidence(private,[fresh.name])
    (private/'credentials.json').write_text('secret')
    with pytest.raises(ValueError,match='Only immutable'):
        archive_evidence(private,['credentials.json'])
    with pytest.raises(ValueError,match='within live'):
        archive_evidence(private,['../out.receipt.json'])
    with pytest.raises(ValueError,match='Pruning'):
        RetentionPolicy(prune_enabled=True)


def test_archive_rejects_symlinks(private,tmp_path):
    external=tmp_path/'out.receipt.json'
    external.write_text('{}')
    try:
        (private/'link.receipt.json').symlink_to(external)
    except OSError as error:
        if os.name == 'nt' and error.winerror == 1314:
            pytest.skip('Windows token lacks symlink creation privilege; junction/hardlink checks are separate')
        raise
    with pytest.raises(ValueError,match='symlinks'):
        archive_evidence(private,['link.receipt.json'])


def test_forecast_uses_measured_storage_delta_and_never_counts_secrets_or_archive_twice(private):
    receipt=old_receipt(private)
    (private/'credentials.json').write_text('do not inventory')
    first=storage_forecast(private,now=100)
    assert first['growth_bytes_per_second'] is None
    with receipt.open('a') as stream:
        stream.write('x'*120)
    second=storage_forecast(private,previous=first,now=160)
    assert second['growth_bytes_per_second']==2
    assert second['projected_additional_bytes']==2*30*86400
    assert all(row['path']!='credentials.json' for row in second['files'])
    assert receipt.exists()


def test_workload_objectives_separate_interactive_background_and_unknown_targets(private):
    ledger=WorkloadObjectives(private/'operations')
    for number in range(100):
        ledger.record('interactive','knowledge',number/1000,True,'interactive-'+str(number),now=100+number)
        ledger.record('background','CODE_EDIT',number,number!=99,'background-'+str(number),now=100+number)
    snapshot=ledger.snapshot(now=200)
    assert snapshot['workloads']['interactive']['p95_seconds']==.094
    assert snapshot['workloads']['background']['p95_seconds']==94
    assert snapshot['workloads']['interactive']['status']=='awaiting_owner_ratified_launch_targets'
    ledger.ratify('interactive',.1,.02,'owner-reviewed-launch','retained-launch-artifact')
    ledger.ratify('background',90,.05,'owner-reviewed-launch','retained-launch-artifact')
    snapshot=WorkloadObjectives(private/'operations').snapshot(now=200)
    assert snapshot['workloads']['interactive']['status']=='within_objective'
    assert snapshot['workloads']['background']['status']=='objective_exceeded'
    assert snapshot['workloads']['background']['bad_event_fraction']==.09
    assert snapshot['correctness_and_containment_remain_hard'] is True


def test_workload_observation_idempotency_and_invalid_evidence(private):
    ledger=WorkloadObjectives(private/'operations')
    ledger.record('background','CODE_EDIT',1,True,'actual-attempt')
    ledger.record('background','CODE_EDIT',2,False,'actual-attempt')
    assert ledger.snapshot()['workloads']['background']['samples']==1
    with pytest.raises(ValueError):
        ledger.record('interactive','knowledge',float('nan'),True,'invalid')
    with pytest.raises(ValueError):
        ledger.ratify('interactive',1,1,'review','artifact')


def test_pool_targets_stay_fixed_without_sufficient_real_handoff_observations(private):
    runner=DockerRunner(policy(adaptive_pool_enabled=True),private/'containers')
    result=runner.pool_target_snapshot()
    assert result['mode']=='fixed' and result['selected_target']==4
    assert not result['evidence_sufficient']
    # Storage-layer demand test only: no Docker execution or native claims.
    with pytest.raises(ContainerCapacityError):
        runner.reserve_attempt('waiting-job','attempt')
    assert runner.pool_target_snapshot()['demand_count']==1


def test_pool_target_adaptation_requires_window_and_respects_fixed_bound(private):
    runner=DockerRunner(policy(adaptive_pool_enabled=True,warm_pool_size=4),private/'containers')
    now=time.time()
    with closing(runner._connect()) as db:
        for index in range(32):
            db.execute('INSERT INTO pool_demand VALUES(?,?,?,?,?)',
                       (runner.policy.pool_digest,'job-'+str(index),now-100+index,now-50+index,2))
        db.commit()
    result=runner.pool_target_snapshot(now=now)
    assert result['mode']=='adaptive' and result['selected_target']==2
    assert result['completed_handoffs']==32
    assert result['preparation_p95_seconds']==2
    assert result['hardware_can_only_lower_target'] is True
    fixed=DockerRunner(replace(runner.policy,adaptive_pool_enabled=False),runner.state_root)
    assert fixed.pool_target_snapshot(now=now)['selected_target']==4
    assert fixed.pool_target_snapshot(now=now)['recommended_target']==2


def test_async_request_metrics_are_drained_and_missing_samples_remain_visible(private):
    ledger=WorkloadObjectives(private/'operations')
    for index in range(200):
        assert ledger.record_async('interactive','knowledge',.004,True,'http-'+str(index))
    ledger.close()
    result=ledger.snapshot()
    assert result['observation_queue']['complete'] is True
    assert result['workloads']['interactive']['samples']==200
    assert not ledger.record_async('interactive','knowledge',.004,True,'after-close')
    assert ledger.snapshot()['observation_queue']['dropped']==1


def test_forecast_never_creates_root_and_bounds_scan(private,tmp_path):
    with pytest.raises(FileNotFoundError):
        storage_forecast(tmp_path/'absent')
    for index in range(6):
        (private/(str(index)+'.receipt.json')).write_text('{}')
    result=storage_forecast(private,max_files=2)
    assert result['scan_complete'] is False
    assert result['growth_bytes_per_second'] is None
    assert len(result['files'])<=2


def test_objective_ratification_requires_measured_samples(private):
    with pytest.raises(ValueError,match='64 measured'):
        WorkloadObjectives(private/'operations').ratify('interactive',.1,.01,'review','launch-artifact')


def test_archive_rejects_windows_drive_relative_and_duplicate_paths(private):
    source=old_receipt(private)
    for invalid in ('C:private.receipt.json','receipts/file.receipt.json:alternate'):
        with pytest.raises(ValueError,match='relative POSIX'):
            archive_evidence(private,[invalid])
    with pytest.raises(ValueError,match='unique'):
        archive_evidence(private,[source.name,'./'+source.name])
    assert source.exists()
