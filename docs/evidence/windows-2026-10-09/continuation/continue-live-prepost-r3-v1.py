"""One explicit continuation of an exactly proved pre-POST readiness failure.

Default is a no-I/O plan. Existing POST markers, snapshots, additional evidence
or a prior continuation fence never grant submission authority. All model POSTs
remain inside the immutable v2 driver with its original durable attempt markers.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import http.client
import json
import math
from pathlib import Path
import re
import time
import types

HERE = Path(__file__).absolute().parent
DRIVER = HERE / 'run-live-commissioning-r3-v2.py'
DRIVER_SHA = 'cb2c1b834978a310298bb224f526ae880182b00e88f38dec60b7c02d0d45d363'
ORIGINAL_INTENT_SHA = '294bfc7cfc8bccd38c883600876751862c35d562409352c36c2f576b9e5159d3'
ORIGINAL_OBSERVATION_SHA = '2a6796e91c20fadf26602355b50213ce9883b2b65ab008d02120bf4dc2774eb2'
ORIGINAL_NAMES = {'intent.json', 'invocation.lock', 'observation-0002.json'}
FENCE = 'prepost-continuation-intent.json'
RESULT = 'prepost-continuation-result.json'


def load_driver(stack):
    raw = DRIVER.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DRIVER_SHA:
        raise ValueError('frozen_driver_changed')
    m = types.ModuleType('exact_live_prepost_driver_v2')
    m.__file__ = str(DRIVER)
    exec(compile(raw, str(DRIVER), 'exec'), m.__dict__)
    held, _ = m.pinned(stack, DRIVER, DRIVER_SHA)
    m.require(held == raw, 'frozen_driver_replaced')
    return m


def expected_intent(m, controller, startup_sha):
    return {'schema': 'cochem-live-commissioning-intent/1', 'driver_sha256': DRIVER_SHA,
            'runtime_root': str(m.INSTALL), 'pins': m.PINS, 'startup_receipt_sha256': startup_sha,
            'controller': controller, 'requests': m.requests(), 'no_automatic_resubmission': True}


def reconcile(m, journal, stack, expected):
    """Caller holds the existing exclusive lock; retain original file handles."""
    rows = journal.inventory()
    m.require({p.name for p in rows} == ORIGINAL_NAMES and len(rows) == 3,
              'not_exact_original_prepost_inventory')
    m.require((journal.root / 'invocation.lock').stat().st_size == 0, 'invocation_lock_not_empty')
    intent_raw, _ = m.pinned(stack, journal.root / 'intent.json', ORIGINAL_INTENT_SHA)
    observation_raw, _ = m.pinned(stack, journal.root / 'observation-0002.json', ORIGINAL_OBSERVATION_SHA)
    m.require(m.strict_json(intent_raw) == expected, 'original_intent_not_exact')
    observation = m.strict_json(observation_raw)
    failure = {'status': 'HELD_OBSERVATION_OR_SUBMISSION_UNCERTAIN', 'error_type': 'Held',
               'code': 'controller_not_ready', 'http_status': None,
               'automatic_resubmission_allowed': False, 'server_work_cancelled': False}
    m.require(set(observation) == {'schema', 'intent_sha256', 'recorded_at', 'result', 'full_srs_acceptance'} and
              observation['schema'] == 'cochem-live-commissioning-observation/1' and
              observation['intent_sha256'] == m.digest(expected) and observation['result'] == failure and
              observation['full_srs_acceptance'] is False and
              type(observation['recorded_at']) in (int, float) and math.isfinite(observation['recorded_at']),
              'not_exact_known_prepost_readiness_failure')
    return {'original_intent_sha256': ORIGINAL_INTENT_SHA,
            'original_observation_sha256': ORIGINAL_OBSERVATION_SHA,
            'original_filenames': sorted(ORIGINAL_NAMES), 'post_attempt_markers': 0,
            'workflow_snapshots': 0, 'original_intent_exact': True}


class MissingWorkflowProbe:
    """Exact missing-ID consistency check, never resubmission authority."""
    def __init__(self, m, client):
        self.m, self.client = m, client

    def check(self, phase, workflow_id):
        m = self.m
        m.require(phase in ('planning', 'coding') and workflow_id == m.requests()[phase]['workflow_id'],
                  'fixed_workflow_probe_required')
        path = ('/workflow/' if phase == 'planning' else '/coding/workflow/') + workflow_id
        connection = http.client.HTTPConnection('127.0.0.1', self.client.port, timeout=20)
        try:
            connection.request('GET', path, headers={'Authorization': 'Bearer ' + self.client.token,
                               'Connection': 'close'})
            response = connection.getresponse()
            deadline = time.monotonic() + 20
            parts, count = [], 0
            while True:
                remaining = deadline - time.monotonic()
                m.require(remaining > 0, 'workflow_probe_deadline')
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read1(min(1024, 4097-count))
                if not chunk:
                    break
                parts.append(chunk); count += len(chunk)
                m.require(count <= 4096, 'workflow_probe_size_bound')
            raw = b''.join(parts)
            m.require(response.status == 400 and m.strict_json(raw) == {'error': 'Unknown job_id'},
                      'workflow_probe_not_exact_missing')
            return 'EXACT_UNKNOWN_JOB_ID'
        finally:
            connection.close()


class StableFirstAdmission:
    """Wait only for the first health read, before the old driver's POST marker."""
    def __init__(self, m, client, controller, wait_seconds, poll_seconds,
                 clock=time.monotonic, sleep=time.sleep, wall=time.time):
        self.m, self.client, self.controller = m, client, controller
        self.wait_seconds, self.poll_seconds = wait_seconds, poll_seconds
        self.clock, self.sleep, self.wall = clock, sleep, wall
        self.first = True
        self.health_get_count = 0
        self.stable_samples = 0

    def call(self, operation, data=None):
        if not self.first:
            return self.client.call(operation, data)
        m = self.m
        m.require(operation == '/health' and data is None, 'first_operation_must_be_health')
        self.first = False
        deadline = self.clock() + self.wait_seconds
        last_samples = None
        stable = 0
        while self.clock() < deadline:
            health = self.client.call('/health')
            self.health_get_count += 1
            m.health_binding(health, self.controller)
            if self.clock() >= deadline:
                break
            try:
                m.health_binding(health, self.controller, ready=True, phase='planning')
                ready = True
            except m.Held as error:
                m.require(str(error) in ('controller_not_ready', 'required_component_not_healthy'),
                          'unexpected_readiness_failure')
                ready = False
            checked = health.get('components', {}).get('warden_controller', {}).get('checked_at')
            measured = health.get('hardware', {}).get('measured_at')
            now = self.wall()
            fresh = all(type(value) in (int, float) and math.isfinite(value) and 0 <= now-value <= 5
                        for value in (checked, measured))
            samples = (checked, measured)
            advanced = last_samples is None or (fresh and checked > last_samples[0] and measured > last_samples[1])
            if ready and fresh and advanced:
                stable += 1
                last_samples = samples
            else:
                stable = 0
                last_samples = None
            self.stable_samples = stable
            if stable >= 3:
                return health
            self.sleep(min(self.poll_seconds, max(0, deadline-self.clock())))
        raise m.Held('stable_readiness_timeout_before_first_post')


def continue_locked(m, journal, stack, expected, client, probe, helper_sha, validate, *,
                    wait_seconds=120, observe_seconds=120, poll_seconds=5,
                    clock=time.monotonic, sleep=time.sleep, wall=time.time):
    """No POST after any ambiguous prior state; the new fence is never reused."""
    proof = reconcile(m, journal, stack, expected)
    for phase in ('planning', 'coding'):
        m.require(probe.check(phase, expected['requests'][phase]['workflow_id']) == 'EXACT_UNKNOWN_JOB_ID',
                  'workflow_probe_consistency_failed')
    # Recheck namespace immediately before exclusive CreateNew. Original input
    # handles remain held with no write/delete sharing throughout this call.
    m.require({p.name for p in journal.inventory()} == ORIGINAL_NAMES, 'prepost_inventory_changed')
    fence = {'schema': 'cochem-live-prepost-continuation-intent/1', 'helper_sha256': helper_sha,
             'driver_sha256': DRIVER_SHA, **proof, 'original_intent_digest': m.digest(expected),
             'controller': expected['controller'], 'original_requests': expected['requests'],
             'wait_seconds': wait_seconds, 'observe_seconds': observe_seconds, 'poll_seconds': poll_seconds,
             'required_consecutive_fresh_samples': 3, 'one_shot_only': True,
             'missing_workflow_get_is_submission_authority': False,
             'automatic_resubmission_allowed': False}
    journal.write(FENCE, fence)
    admitted = StableFirstAdmission(m, client, expected['controller'], wait_seconds, poll_seconds,
                                    clock=clock, sleep=sleep, wall=wall)
    try:
        outcome = m.run_workflows(admitted, journal, expected, True,
                                  validate, observe_seconds=observe_seconds,
                                  poll_seconds=poll_seconds, clock=clock, sleep=sleep)
    except (Exception, KeyboardInterrupt) as error:
        outcome = {'status': 'HELD_PREPOST_CONTINUATION_UNCERTAIN', 'error_type': type(error).__name__,
                   'code': str(error) if isinstance(error, m.Held) else 'bounded_operation_failed',
                   'http_status': getattr(error, 'http_status', None),
                   'automatic_resubmission_allowed': False, 'server_work_cancelled': False}
    journal.write(RESULT, {'schema': 'cochem-live-prepost-continuation-result/1',
                          'fence_sha256': m.digest(fence), 'recorded_at': wall(), 'result': outcome,
                          'readiness_health_gets': admitted.health_get_count,
                          'consecutive_ready_samples': admitted.stable_samples,
                          'full_srs_acceptance': False})
    return {'schema': 'cochem-live-prepost-continuation-summary/1', 'status': outcome['status'],
            'phase': outcome.get('phase'), 'error_type': outcome.get('error_type'), 'code': outcome.get('code'),
            'http_status': outcome.get('http_status'), 'workflow_ids': {k:v['workflow_id'] for k,v in expected['requests'].items()},
            'readiness_health_gets': admitted.health_get_count,
            'consecutive_ready_samples': admitted.stable_samples,
            'automatic_resubmission_allowed': False, 'server_work_cancelled': False,
            'full_srs_acceptance': False}


def execute(*, inspect_only=False, wait_seconds=120, observe_seconds=120, poll_seconds=5):
    with ExitStack() as stack:
        m = load_driver(stack)
        win, private, controller, startup_sha, validate = m.prepare_runtime(stack)
        journal = m.Journal(m.ROOT, private)
        expected = expected_intent(m, controller, startup_sha)
        with journal.locked():
            reconcile(m, journal, stack, expected)
            if inspect_only:
                return {'schema': 'cochem-live-prepost-inspection/1', 'status': 'EXACT_PRE_POST_FAILURE_VERIFIED',
                        **reconcile(m, journal, stack, expected), 'http_calls': 0, 'model_jobs_submitted': 0,
                        'journal_files_changed': 0}
            _, helper_sha = m.pinned(stack, Path(__file__).absolute())
            m.ordinary(m.TOKEN)
            owner, protected, rules = win._acl(m.TOKEN)
            m.require(owner == win.SYSTEM_SID and protected and set(rules) == {
                (win.SYSTEM_SID, win.FULL_CONTROL, 0), (win.ADMIN_SID, win.FULL_CONTROL, 0),
                (private.sid, 0x120089, 0)}, 'controller_token_acl_changed')
            token_raw, _ = m.pinned(stack, m.TOKEN, maximum=256)
            token = token_raw.decode('ascii').strip(); del token_raw
            m.require(32 <= len(token) <= 256 and re.fullmatch('[A-Za-z0-9_-]+', token), 'controller_token_format')
            client = m.BoundedClient(token)
            try:
                return continue_locked(m, journal, stack, expected, client, MissingWorkflowProbe(m, client), helper_sha, validate,
                                       wait_seconds=wait_seconds, observe_seconds=observe_seconds, poll_seconds=poll_seconds)
            finally:
                client.token = ''; token = None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--continue-pre-submission', action='store_true')
    mode.add_argument('--inspect-existing', action='store_true')
    parser.add_argument('--wait-seconds', type=float, default=120)
    parser.add_argument('--observe-seconds', type=float, default=120)
    parser.add_argument('--poll-seconds', type=float, default=5)
    args = parser.parse_args(argv)
    if not all(math.isfinite(value) for value in (args.wait_seconds, args.observe_seconds, args.poll_seconds)) or not (
            10 <= args.wait_seconds <= 120 and args.wait_seconds <= args.observe_seconds <= 120 and 1 <= args.poll_seconds <= 60):
        parser.error('bounded_wait_observation_and_poll_required')
    if not args.continue_pre_submission and not args.inspect_existing:
        print(json.dumps({'schema': 'cochem-live-prepost-continuation-plan/1', 'mode': 'PREVIEW_NO_IO',
                          'driver_sha256': DRIVER_SHA, 'original_intent_sha256': ORIGINAL_INTENT_SHA,
                          'original_observation_sha256': ORIGINAL_OBSERVATION_SHA,
                          'new_workflow_scope_created': False, 'http_calls': 0, 'tokens_read': 0,
                          'files_created': 0, 'model_jobs_submitted': 0, 'automatic_resubmission': False}))
        return 0
    try:
        result = execute(inspect_only=args.inspect_existing, wait_seconds=args.wait_seconds,
                         observe_seconds=args.observe_seconds, poll_seconds=args.poll_seconds)
    except (Exception, KeyboardInterrupt) as error:
        result = {'schema': 'cochem-live-prepost-continuation-summary/1',
                  'status': 'HELD_PREPOST_PROOF_OR_OPERATION_UNCERTAIN', 'error_type': type(error).__name__,
                  'code': str(error) if isinstance(error, ValueError) and re.fullmatch('[a-z_]{1,100}', str(error))
                          else 'private_proof_or_bounded_operation_failed',
                  'automatic_resubmission_allowed': False, 'server_work_cancelled': False}
    print(json.dumps(result))
    return 0 if result['status'] in ('TWO_LIVE_WORKFLOWS_INDEPENDENTLY_VERIFIED', 'EXACT_PRE_POST_FAILURE_VERIFIED') else 3 if result['status'] == 'PENDING' else 2


if __name__ == '__main__':
    raise SystemExit(main())
