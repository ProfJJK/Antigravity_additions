"""Read-only exact early pre-POST journal reconciliation; no POST routes."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import stat
import time
import types

here = Path(__file__).absolute().parent
source = here / 'run-live-commissioning-r3-v2.py'
raw = source.read_bytes()
source_sha = hashlib.sha256(raw).hexdigest()
assert source_sha == 'cb2c1b834978a310298bb224f526ae880182b00e88f38dec60b7c02d0d45d363'
m = types.ModuleType('prepost_reconcile_original_v2')
m.__file__ = str(source)
exec(compile(raw, str(source), 'exec'), m.__dict__)
with ExitStack() as stack:
    m.pinned(stack, source, source_sha)
    win, private, controller, startup_sha, _ = m.prepare_runtime(stack)
    journal = m.Journal(m.ROOT, private)
    with journal.locked():
        files = journal.inventory()
        names = sorted(path.name for path in files)
        m.require(names == ['intent.json', 'invocation.lock', 'observation-0002.json'], 'not_exact_prepost_inventory')
        m.require((m.ROOT/'invocation.lock').stat().st_size == 0, 'lock_has_contents')
        intent_raw, intent_sha = m.pinned(stack, m.ROOT/'intent.json')
        observation_raw, observation_sha = m.pinned(stack, m.ROOT/'observation-0002.json')
        intent = m.strict_json(intent_raw)
        expected = {'schema':'cochem-live-commissioning-intent/1', 'driver_sha256':source_sha,
                    'runtime_root':str(m.INSTALL), 'pins':m.PINS, 'startup_receipt_sha256':startup_sha,
                    'controller':controller, 'requests':m.requests(), 'no_automatic_resubmission':True}
        m.require(intent == expected, 'original_intent_changed')
        observation = m.strict_json(observation_raw)
        expected_result = {'status':'HELD_OBSERVATION_OR_SUBMISSION_UNCERTAIN', 'error_type':'Held',
                           'code':'controller_not_ready', 'http_status':None,
                           'automatic_resubmission_allowed':False, 'server_work_cancelled':False}
        m.require(set(observation) == {'schema','intent_sha256','recorded_at','result','full_srs_acceptance'} and
                  observation['schema']=='cochem-live-commissioning-observation/1' and
                  observation['full_srs_acceptance'] is False and
                  observation['intent_sha256']==m.digest(expected) and observation['result']==expected_result and
                  type(observation['recorded_at']) in (int,float), 'not_known_readiness_only_failure')
        result = {'schema':'cochem-live-prepost-readonly-reconciliation/1', 'status':'EXACT_PRE_POST_FAILURE_VERIFIED',
                  'recorded_at':time.time(), 'journal_filenames':names, 'intent_sha256':intent_sha,
                  'observation_sha256':observation_sha, 'driver_sha256':source_sha,
                  'original_intent_exact':True, 'original_failure':'controller_not_ready',
                  'post_attempt_markers':0, 'workflow_snapshots':0,
                  'frozen_driver_requires_marker_before_every_post':True,
                  'http_get_calls':0, 'http_post_calls':0, 'model_jobs_submitted':0,
                  'journal_files_changed':0, 'tasks_changed':0, 'token_contents_read':False,
                  'controller':controller, 'startup_receipt_sha256':startup_sha}
out=here/'prepost-journal-r3-v2-readonly-reconciliation-20261008.json'
with out.open('x',encoding='utf-8',newline='\n') as stream:
    json.dump(result,stream,sort_keys=True,indent=2)
    stream.write('\n')
print(json.dumps(result,sort_keys=True))
