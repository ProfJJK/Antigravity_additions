"""Publish reviewed operator guidance and preserve prior status; no deployment."""
from pathlib import Path
import datetime
import hashlib
import json
import xml.etree.ElementTree as ET

WORK = Path(r'C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next')
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
EVIDENCE = REPO / 'docs/evidence/windows-2026-10-06'
ARCHIVE = EVIDENCE / 'pipeline-commissioning-series-preparation-2026-10-07'
PINS = {
    'run-pipeline-commissioning-r3-v1.ps1': '44ed0b7d79b33afee6e35b36ddf2d37056e1e51f31806f05a21c2038c98d9bf1',
    'test_pipeline_commissioning_series_r3.py': '7791ca2c19a903170e0ff48f4379a8e4e6a91677835eab5af3ecbfad356e63e9',
    'pipeline-commissioning-series-tests-scope-guards.xml': 'cc8eccfb433735e88213e068518c0bd7ebc7e4143f58b28dfed6672e15adf885',
    'pipeline-commissioning-series-preview-final.json': '844fff031313302d557b8489e23ab94973271ad4330a939718e54c70a50e54e4',
    'commission-first-warden-r3-v1.ps1': '75efe248449fa9be0317a85feb277d82a961d05594ee754d11b3b9d0ec4dfd1e',
    'commission-first-warden-r3-v1.py': 'bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c',
    'authenticate-native-profiles-status-first-r3-v1.ps1': '85801beaa1bfaaaa41ad0727162e2e6ac244051078465c0d892c66a32c34b44f',
}
PREPARATIONS = {
    'authentication': ('six-profile-status-first-auth-2026-10-07/preparation.json', 'd27318e6bb63820896d03a817b9158407567ec4d975884864ddd54e5398df518'),
    'first_start': ('first-warden-commissioning-preparation-2026-10-07/preparation.json', '6f70d3a5a8087e472ae260f0bcb0c0358364e5584f881423008ad35f59e95f8e'),
    'live_workflows': ('live-commissioning-driver-preparation-2026-10-07/preparation.json', 'b2f07fa2b2305365a3ffd4b03ebd6e4e229875c176089e4a116d09a4cecf539d'),
    'codex_live_bridge': ('codex-live-bridge-r3-preparation-20261007/preparation.json', '9b5fa42ebdab6b2cfeb1ac727de0fc3c69632a26a03d42b8c40e0fe9161dee2b'),
}
OBSERVED = REPO / 'config/windows/aetherdesk-427.observed-20261007.json'
HANDOFF = REPO / 'docs/WINDOWS_MORNING_HANDOFF_4.2.7_2026-10-07.md'
PRIOR_PINS = {
    OBSERVED: '0d3293aa412e2fd0310a0332e19e54e0b1583a8c147f9ec3000b933ba8a5705d',
    HANDOFF: '7438ecfd5bdc8d4221eb9646f623375fc2abc2501a372f3ed373e35fb2c9ffd8',
    WORK / 'RETURN_SETUP.txt': '33ddd4c1d54d723aed220ba0ae50877808d223229abcf2b82ee4927d81f81c56',
    WORK / 'DEPLOYMENT_REMAINING_20261007.txt': '33ddd4c1d54d723aed220ba0ae50877808d223229abcf2b82ee4927d81f81c56',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def create(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def serialized(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode()


def main():
    sources = {}
    for name, pin in PINS.items():
        raw = (WORK / name).read_bytes()
        if sha(raw) != pin:
            raise ValueError('Prepared source changed; publish nothing.')
        sources[name] = raw
    prior = {}
    for path, pin in PRIOR_PINS.items():
        raw = path.read_bytes()
        if sha(raw) != pin:
            raise ValueError('Prior guidance changed; publish nothing.')
        prior[path] = raw
    for name, (relative, pin) in PREPARATIONS.items():
        if sha((EVIDENCE / relative).read_bytes()) != pin:
            raise ValueError('Reviewed supporting archive changed.')
    before_pin = sources['run-pipeline-commissioning-r3-v1.ps1'].replace(
        PINS['commission-first-warden-r3-v1.ps1'].encode(), b'NOT_FROZEN_DO_NOT_APPLY')
    assert sha(before_pin) == 'd16347c8e175767cc17378b16eaffd2d4fbdb5f62289924a428d3fa7d6a7d2f7'
    suite = ET.fromstring(sources['pipeline-commissioning-series-tests-scope-guards.xml']).find('testsuite')
    assert int(suite.attrib['tests']) == 52
    assert all(int(suite.attrib[k]) == 0 for k in ('failures', 'errors', 'skipped'))
    preview = json.loads(sources['pipeline-commissioning-series-preview-final.json'])
    assert preview['phases'] == ['native_authentication', 'first_controller_start']
    assert preview['identity_count'] == 6 and preview['shared_capacity'] == 4
    assert preview['model_jobs_submitted'] == 0 and preview['tasks_registered'] == 0
    expected_holds = [f'native_authentication: {provider}: Worker slot{n} root metadata is inaccessible in this token; Administrator preflight must establish its ordinary directory identity.'
                      for provider in ('codex', 'claude') for n in range(1, 7)]
    assert preview['holds'] == expected_holds
    first = preview['phase_previews'][1]
    assert first['system_preflight_deferred'] is True
    assert first['holds'] == ['Completed status-first authentication is required before first start.']
    assert first['monitoring_scope'] == 'heartbeat_and_queue_only'

    command = ('C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe -NoProfile -File "'
               + str(WORK / 'run-pipeline-commissioning-r3-v1.ps1') + '" -Apply -Interactive')
    guide = f'''WINDOWS PIPELINE: ONE ATTENDED FIRST-START SESSION
7 October 2026 - AETHERDESK

The reviewed sign-in and first-start series is ready. This is the attended
step for getting the controller running, not a claim of full SRS acceptance.
No Docker timing test or completed installation needs repeating.

WHAT TO DO WHEN CONVENIENT

1. Open Start, type Windows PowerShell, choose Run as administrator, and accept
   UAC under your ansac account. Use that interactive window for this session.
2. Paste this entire single command and press Enter:

{command}

3. Follow browser/device instructions only when a profile is explicitly logged
   out. Existing verified worker sessions are reused. There are six isolated
   profiles with Codex/Claude coverage, so 0-12 approvals may be needed; the
   actual number is unknown until their SYSTEM status checks run. Use your
   existing subscriptions. No Agy sign-in, worker password, or credential copy
   is requested. Do not paste login codes, tokens or provider output into chat.
4. Let the command finish, then return its final JSON. If it stops or prints a
   hold, return the failure instead and leave tasks/files/running state in place.
   Do not rerun this consumed series or the earlier return/Docker commands.

WHY THIS NEEDS YOU

Windows administrator rights are needed for protected files and SYSTEM tasks;
this chat runs as an ordinary ansac process. Provider browser consent is yours.
The command performs preservation and checks automatically in that session.

WHAT THE COMMAND DOES

- Checks both providers in all six existing isolated profiles. It signs in only
  profiles proved logged out; unknown status stops for engineering diagnosis.
- Consumes pinned authentication proofs, preserves the four exact prior Docker
  test files into new private retention, and checks untouched private state.
- Registers one new Warden task and starts it once. Collects actual SYSTEM
  process/launcher identity, progressing heartbeat/API and queue-observer proof.
- Keeps four shared slots and all pipeline model routing on Chapter 06.
- Leaves automatic repair disabled. It does not migrate old jobs or repair
  budgets, reset credentials, recreate/format R:, alter its startup task, repeat
  the Docker trial, or submit model jobs in this attended session.

AFTER THE FIRST START

Codex can run the prepared ordinary-process MCP bridge check and routed planning
and coding driver without another administrator command for those checks.
They have not run yet. Codex MCP registration and 15-tool discovery are already
verified; the current chat tool catalog has not been reloaded.

Do not reboot as part of this command. One later convenient controlled reboot
is still needed for full recovery acceptance after recovery setup is ready.
The required 48-hour observation is unattended machine availability, not
48 hours of console supervision; normal work should remain possible.

Automatic-repair historical spending authority and genuine desktop-heap usage
acquisition remain engineering gaps. Unknown spending stays held. Legacy
databases/history are preserved; old requests are not silently resumed.
Full Windows workflow/recovery/resource/stability acceptance remains open.

WINDOWS EVIDENCE, SEPARATE FROM LINUX

Prepared ordinary Windows checks: authentication68; first-start47; combined
sequencing52; live-workflow driver38; MCP live-bridge verifier42. Each has its
own evidence scope; these are not actual SYSTEM startup/model acceptance.
Actual combined preview completed with zero tasks/model jobs and only the
expected ordinary-token worker-metadata holds. Private SYSTEM checks are
explicitly deferred to the reviewed command.
The physical Docker isolation/cleanup and <=30-second test cycles already
passed. Its 2.697/2.659-second startup remains a nonblocking optimization miss.
Existing credentials, databases, repair budgets, 8 GiB R: and
Mount_CoChem_RAMDisk remain preserved.
'''
    guide_raw = guide.encode()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    review = {
        'schema': 'cochem-commissioning-series-independent-review/1',
        'status': 'REVIEWED_PREPARED_NOT_APPLIED', 'recorded_utc': now,
        'reviewer': 'host_inspection', 'logic_reviewed_source_sha256': sha(before_pin),
        'published_source_sha256': PINS['run-pipeline-commissioning-r3-v1.ps1'],
        'only_change_after_peer_logic_review': 'Bind reviewed final first-start wrapper SHA256.',
        'findings': [], 'ordinary_windows_tests': 52,
        'checks': ['Held source handles span preview and both child phases.',
                   'Only one exact missing-auth prerequisite may be deferred.',
                   'Authentication proves twelve provider/profile outcomes.',
                   'Any nonzero/ambiguous/unverified phase stops later phases.',
                   'Receipt validates SYSTEM process identity and heartbeat progress.',
                   'Monitoring is heartbeat/queue only; no repair or full acceptance claim.'],
        'system_apply_executed': False,
    }
    preparation = {
        'schema': 'cochem-pipeline-commissioning-series-preparation/1',
        'status': 'REVIEWED_ATTENDED_COMMAND_READY_NOT_APPLIED', 'recorded_utc': now,
        'entrypoint': str(WORK / 'run-pipeline-commissioning-r3-v1.ps1'),
        'entrypoint_sha256': PINS['run-pipeline-commissioning-r3-v1.ps1'],
        'operator_command': command, 'phases': preview['phases'],
        'identities': 6, 'shared_slots': 4, 'chapter06_routing_unchanged': True,
        'maximum_browser_authorizations': 12, 'actual_browser_authorizations_required': None,
        'ordinary_windows_tests': {'passed': 52, 'errors': 0, 'failed': 0, 'skipped': 0},
        'preview': {'sha256': PINS['pipeline-commissioning-series-preview-final.json'],
                    'holds': expected_holds, 'system_checks_deferred': True,
                    'tasks_registered': 0, 'model_jobs_submitted': 0},
        'files_sha256': PINS,
        'supporting_preparations': {k: {'path': v[0], 'sha256': v[1]} for k, v in PREPARATIONS.items()},
        'guide_sha256': sha(guide_raw), 'controller_started': False,
        'automatic_repair_enabled': False, 'full_srs_acceptance': False,
        'installed_runtime_changed': False, 'credentials_changed': False,
        'databases_modified': False, 'repair_budgets_modified': False,
        'ram_volume_or_startup_task_changed': False, 'linux_evidence_included': False,
    }
    ARCHIVE.mkdir()
    for name, raw in sources.items():
        create(ARCHIVE / name, raw)
    for path, raw in prior.items():
        create(ARCHIVE / ('prior-' + path.name), raw)
    create(ARCHIVE / 'independent-review.json', serialized(review))
    create(ARCHIVE / 'owner-guide.txt', guide_raw)
    create(ARCHIVE / 'preparation.json', serialized(preparation))
    state = json.loads(prior[OBSERVED])
    state['return_instructions_history'].append({'recorded_utc': now, 'scope': 'Prior under-implementation guide retained before reviewed first-start command.', 'return_instructions': state['return_instructions']})
    state['return_instructions'] = {
        'status': preparation['status'], 'guide': str(WORK / 'DEPLOYMENT_REMAINING_20261007.txt'),
        'guide_sha256': sha(guide_raw), 'current_command': command,
        'administrator_series_ready': True, 'human_console_required': True,
        'entrypoint_sha256': preparation['entrypoint_sha256'],
        'preparation_evidence': str((ARCHIVE / 'preparation.json').relative_to(REPO)).replace('\\', '/'),
        'preparation_sha256': sha(serialized(preparation)),
        'maximum_browser_authorizations': 12, 'actual_browser_authorizations_required': None,
        'owner_timing_projection_required': False, 'physical_fixture_rerun_required': False,
        'repeat_completed_setup_required': False, 'controller_started': False,
        'automatic_repair_enabled': False, 'full_srs_acceptance': False,
        'human_participation': ['One elevated attended first-start session with conditional browser approvals.',
                               'One later convenient controlled reboot after recovery setup.',
                               'Unattended 48-hour host availability for full stability acceptance.'],
    }
    state['prepared_not_applied']['consolidated_first_start_series'] = preparation
    state['remaining'][0] = 'Run the now reviewed consolidated attended first-start command when convenient. Six immutable chapter identities share four concurrent slots. Browser approval is needed only for explicitly logged-out Codex/Claude worker sessions: zero to twelve approvals, actual count unknown until SYSTEM checks. Existing owner Agy sign-in is preserved.'
    state['remaining'][1] = 'Agent work after controller start: execute the reviewed ordinary MCP and routed planning/coding drivers, complete recovery/repair and truthful resource monitoring. First-start orchestration is reviewed and ready but not applied. Preserve legacy jobs/ledgers and exact repair-budget authority; unresolved historical spending keeps automatic repair held.'
    state['human_involvement_clarification']['current_owner_command_ready'] = True
    state['human_involvement_clarification']['reason'] = 'Reviewed consolidated status-first authentication and initial controller commissioning are now prepared. Full recovery/repair/monitoring remain open.'
    state['latest_preparation_utc'] = now
    observed_raw = serialized(state)
    section = f'''## Reviewed first-start command ready; not yet applied

The [consolidated commissioning package](evidence/windows-2026-10-06/pipeline-commissioning-series-preparation-2026-10-07/preparation.json)
now combines conditional isolated-profile sign-ins and one controlled Warden start.
The owner guide contains one attended administrator command. It keeps six chapter
identities, four shared slots and Chapter 06 routing. Valid worker sign-ins are
reused; zero to twelve browser approvals may be needed. No Agy login is requested.

Actual ordinary Windows preparation: authentication68, first-start47, combined
sequencing52, live-workflow driver38 and MCP bridge verifier42 checks passed in
their separate scopes. The combined default preview performed no registration,
model submission or activation; only ordinary-token worker-metadata holds remain
visible. SYSTEM private checks are deferred rather than claimed passed.

The first-start helper retains the four exact prior Docker source files before
normal scratch cleanup, holding six production worker exclusions through startup
proof. The [reviewed first-start package](evidence/windows-2026-10-06/first-warden-commissioning-preparation-2026-10-07/preparation.json)
preserves all old roots/tasks/receipts. No completed setup or Docker timing test
needs repeating. No pipeline model jobs have run yet.

After the owner returns successful startup evidence, Codex can use the ordinary
[MCP bridge verifier](evidence/windows-2026-10-06/codex-live-bridge-r3-preparation-20261007/preparation.json)
and [native workflow driver](evidence/windows-2026-10-06/live-commissioning-driver-preparation-2026-10-07/preparation.json)
without another administrator command for those checks. MCP is registered; this
chat's tool catalog has not been reloaded.

This is initial controller commissioning. Automatic repair remains disabled while
historical spending authority is unresolved. Genuine desktop-heap acquisition,
legacy continuity, applicable recovery/repair/rollback and unattended48-hour
acceptance remain engineering dependencies. No reboot is requested by this
command; schedule the later controlled recovery reboot when its setup is ready.

'''
    handoff_raw = prior[HANDOFF]
    first_line, rest = handoff_raw.decode('utf-8-sig').split('\n', 1)
    updated_handoff = (first_line + '\n\n' + section + rest.lstrip('\n')).encode()
    for path, old in prior.items():
        if path.read_bytes() != old:
            raise ValueError('Guidance changed during publication; retained archive is authoritative.')
    OBSERVED.write_bytes(observed_raw)
    HANDOFF.write_bytes(updated_handoff)
    for name in ('RETURN_SETUP.txt', 'DEPLOYMENT_REMAINING_20261007.txt'):
        (WORK / name).write_bytes(guide_raw)
    assert json.loads(OBSERVED.read_bytes())['return_instructions']['administrator_series_ready'] is True
    assert all((WORK / name).read_bytes() == guide_raw for name in ('RETURN_SETUP.txt', 'DEPLOYMENT_REMAINING_20261007.txt'))
    print(json.dumps({'archive': str(ARCHIVE), 'preparation_sha256': sha(serialized(preparation)),
                      'observed_sha256': sha(observed_raw), 'handoff_sha256': sha(updated_handoff),
                      'guide_sha256': sha(guide_raw), 'deployment_changed': False}))


if __name__ == '__main__':
    main()
