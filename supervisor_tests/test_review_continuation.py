"""Unavailable-review continuation with real durable state and subprocess fixtures.

These tests do not execute native models or attest Windows process isolation.
"""
from copy import copy, deepcopy
import json
import sqlite3
import time
from pathlib import Path
import threading

from cochem_supervisor.engine import Supervisor
from cochem_supervisor.io import write_json
from cochem_supervisor.releases import ReleaseStore, tree_manifest
from cochem_supervisor.state import Ledger
from supervisor_tests.test_engine import LocalProtocolDriver, LocalSupervisor, incident


class DeferredReviewSupervisor(LocalSupervisor):
    def _reconcile_repair(self, *args, **kwargs):
        # Keep only the producing provider available initially. Production
        # exclusion/backoff and continuation code runs without replacement.
        return Supervisor._reconcile_repair(self, *args, **kwargs)


def reopen(supervisor):
    restarted=copy(supervisor)
    restarted.config=deepcopy(supervisor.config)
    restarted.ledger=Ledger(supervisor.ledger.path)
    restarted.releases=ReleaseStore(Path(supervisor.config['release_root']),
        Path(supervisor.config['pointer_file']),supervisor.private/'release-journal.json')
    restarted.runner=LocalProtocolDriver(acceptance='passed')
    restarted.stop_event=threading.Event()
    restarted.current_attempt=None
    restarted.smoke_candidate=None
    restarted.smoke_report=None
    restarted.clear_calls=0
    restarted.lifecycle=[]
    restarted.protected=[]
    restarted.stage='starting'
    return restarted


def enable_asymmetric_reviewer(supervisor):
    supervisor.config['providers'].append({'provider':'claude','model':'claude-fable-5-1',
        'executable':supervisor.config['providers'][0]['executable']})
    contracts=json.loads((supervisor.private/'cli-contracts.json').read_text())
    contracts['providers']['claude']={'available':True,'missing_flags':[],'exit_code':0,'help_exit_code':0}
    write_json(supervisor.private/'cli-contracts.json',contracts)


def test_unspent_review_survives_restart_and_resumes_exact_candidate_without_new_generation(tmp_path):
    supervisor=DeferredReviewSupervisor(tmp_path,LocalProtocolDriver(acceptance='passed'))
    supervisor.config.update(auto_deploy=True,repair_timeout_seconds=.03)
    value=incident(supervisor)
    original_release=supervisor.releases.current()
    supervisor._repair(value)
    state=supervisor.ledger.get_incident(value['fingerprint'])
    assert state['model_calls']==1 and state['attempts']==1
    assert supervisor.runner.repair_calls==['codex']
    assert supervisor.releases.current()==original_release
    attempts=[row for row in supervisor.ledger.history(value['fingerprint']) if row['event']=='ATTEMPT_RESERVED']
    assert len(attempts)==1
    attempt_id=attempts[0]['attempt_id']
    checkpoint=supervisor.ledger.review_checkpoint(attempt_id)
    assert checkpoint is not None
    frozen=Path(supervisor.config['release_root'])/('.validation-'+attempt_id)
    frozen_hashes=tree_manifest(frozen)

    # Simulate a controller crash long enough for its execution lease to
    # expire; the paid reservation and durable review checkpoint remain.
    with sqlite3.connect(supervisor.ledger.path) as connection:
        connection.execute('UPDATE supervisor_attempts SET lease_expires_at=? WHERE attempt_id=?',
                           (time.time()-1,attempt_id))
    restarted=reopen(supervisor)
    assert restarted.ledger.review_checkpoint(attempt_id)==checkpoint
    enable_asymmetric_reviewer(restarted)
    restarted.config['repair_timeout_seconds']=10
    restarted._repair(value)

    completed=restarted.ledger.get_incident(value['fingerprint'])
    assert completed['model_calls']==2 and completed['attempts']==1
    assert restarted.runner.repair_calls==['claude']
    assert restarted.clear_calls==0
    assert restarted.ledger.get_attempt(attempt_id)['status']=='SUCCEEDED'
    assert restarted.releases.current()!=original_release
    assert tree_manifest(frozen)==frozen_hashes
    assert restarted._repair(value) is False
    assert restarted.ledger.get_incident(value['fingerprint'])['model_calls']==2
