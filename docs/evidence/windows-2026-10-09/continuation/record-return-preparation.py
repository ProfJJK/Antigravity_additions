"""Archive completed ordinary Windows work and publish the current handoff only."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
HERE=Path(__file__).resolve().parent
EVIDENCE=REPO/'docs/evidence/windows-2026-10-06'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def new_copy(source, target):
    with target.open('xb') as output:
        output.write(source.read_bytes())
    assert sha(source)==sha(target)

def main():
    initial=json.loads((HERE/'ordinary-regression-20261007-r3/summary.json').read_text())
    intermediate=json.loads((HERE/'ordinary-regression-20261007-r3-followup/summary.json').read_text())
    final=json.loads((HERE/'ordinary-regression-20261007-r3-final/summary.json').read_text())
    assert final['exit_code']==0 and final['source_unchanged']
    count=final['counts']
    assert count['failures']==0 and count['errors']==0
    passed=count['tests']-count['skipped']
    archive=EVIDENCE/'ordinary-regression-followup-2026-10-07'
    archive.mkdir(exist_ok=False)
    for folder in ('ordinary-regression-20261007-r3','ordinary-regression-20261007-r3-followup','ordinary-regression-20261007-r3-final'):
        target=archive/folder
        target.mkdir()
        for name in ('summary.json','source-before.json','tests.xml','pytest.log'):
            new_copy(HERE/folder/name,target/name)
    for name in ('run-ordinary-regression-r3.py','run-ordinary-regression-r3-followup.py','run-ordinary-regression-r3-final.py',
                 'ordinary-regression-remediation-tests.xml','git-explicit-bindings-targeted-tests.xml','service-regression-followup-tests.xml'):
        new_copy(HERE/name,archive/name)
    changed=(
        'scripts/plan_aetherdesk_427_cutover.ps1','pipeline_tests/windows_test_context.py',
        'pipeline_tests/test_coding_git.py','pipeline_tests/test_git_deadline.py',
        'pipeline_tests/test_coding_workflow.py','pipeline_tests/test_service.py',
        'pipeline_tests/test_deployment_revision.py','pipeline_tests/test_ramdisk.py',
        'pipeline_tests/test_upgrade_preview.py','supervisor_tests/test_diagnostics.py',
        'supervisor_tests/test_structural.py','pipeline_tests/test_windows_payload_staging.py',
        'pipeline_tests/test_windows_native_payload_staging.py')
    for relative in changed:
        target=archive/'source'/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        new_copy(REPO/relative,target)
    skips={}
    for case in ET.parse(HERE/'ordinary-regression-20261007-r3-final/tests.xml').getroot().iter('testcase'):
        skip=case.find('skipped')
        if skip is not None:
            reason=skip.get('message','unlabelled')
            skips[reason]=skips.get(reason,0)+1
    manifest={p.relative_to(archive).as_posix():{'sha256':sha(p),'bytes':p.stat().st_size}
              for p in sorted(archive.rglob('*')) if p.is_file()}
    summary={'schema':'cochem-ordinary-regression-followup/1','created_utc':datetime.now(timezone.utc).isoformat(),
        'initial_result':initial,'intermediate_result':intermediate,'final_result':final,'final_passed':passed,'deselected_native_cli_calls':2,
        'skip_reasons':skips,'files':manifest,
        'repairs':['Read-only PowerShell cutover planner uses DirectoryInfo type for raw .NET parent traversal under StrictMode.',
            'Real controller Git fixtures declare their actual SYSTEM prerequisite; explicit trusted Git executable is passed on Windows, including child processes.',
            'Symlink fixtures skip only Windows ERROR_PRIVILEGE_NOT_HELD; unrelated errors still fail.',
            'Fresh payload staging fixtures preserve existing protected roots and test collision refusal. Seven unreachable fresh-root inventory cases explicitly skip.',
            'HTTP connection-cap test accepts Windows ConnectionAbortedError as well as reset/EOF for the rejected excess connection; a fresh authenticated request still proves handler slot recovery.'],
        'scope':'Ordinary Windows3.12.13, real disposable fixtures and simulated boundaries as defined by each test; not SYSTEM, live Docker, provider/model or sustained acceptance.',
        'installed_r3_changed':False,'production_identity_guards_relaxed':False,
        'initial_failed_result_preserved':True,'privileged_coverage_inferred_from_skips':False,
        'linux_tests_run':False,'pipeline_activated':False,
        'independent_review':'host_inspection reviewed capability gates, symlink handling, staging preservation and planner fix without findings; root reviewed explicit Git fixture bindings.'}
    (archive/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n',encoding='utf-8')

    handoff=REPO/'docs/WINDOWS_MORNING_HANDOFF_4.2.7_2026-10-07.md'
    new_copy(handoff,EVIDENCE/'morning-handoff-before-return-series-2026-10-07.md')
    new_copy(HERE/'MORNING_SETUP.txt',HERE/'MORNING_SETUP.before-return-series.txt')
    guide=HERE/'RETURN_SETUP.txt'
    new_copy(guide,EVIDENCE/'return-setup-owner-guide-2026-10-07.txt')
    (HERE/'MORNING_SETUP.txt').write_text(
        'Superseded by RETURN_SETUP.txt in this directory.\n'
        'Use its reviewed five-command order when you return. Do not run the old standalone preflight first.\n'
        'The previous guide is preserved as MORNING_SETUP.before-return-series.txt.\n'
        'Pipeline remains stopped; stop at the first error and preserve all artifacts.\n',encoding='utf-8')

    packs={
        'return_setup_series_r3':('return-setup-series-r3-preparation-2026-10-07',24,'run-return-setup-r3.ps1'),
        'docker_physical_r3_v1':('docker-execution-r3-v1-preparation-2026-10-07',37,'accept-docker-execution-r3-v1.ps1'),
        'attended_native_series_r3':('attended-native-series-r3-preparation-2026-10-07',42,'login-six-workers-attended-r3.ps1'),
        'legacy_continuity_diagnostic':('legacy-continuity-preparation-2026-10-07',13,'inspect-legacy-continuity.ps1')}
    observed_path=REPO/'config/windows/aetherdesk-427.observed-20261007.json'
    observed=json.loads(observed_path.read_text(encoding='utf-8-sig'))
    new_copy(observed_path,EVIDENCE/'host-observation-before-return-series-2026-10-07.json')
    for key,(folder,tests,entry) in packs.items():
        prepared=EVIDENCE/folder/'preparation.json'
        observed['prepared_not_applied'][key]={
            'entrypoint':str(HERE/entry),'entrypoint_sha256':sha(HERE/entry),
            'status':'PREPARED_REVIEWED_FOR_OWNER_RETURN_NOT_EXECUTED',
            'ordinary_windows_preparation_tests_passed':tests,'preparation':prepared.relative_to(REPO).as_posix(),
            'preparation_sha256':sha(prepared),'privileged_phase_executed':False,
            'owner_instructions':str(guide),'activation_ready':False}
    legacy_review=EVIDENCE/'legacy-continuity-independent-review-2026-10-07.json'
    observed['prepared_not_applied']['legacy_continuity_diagnostic']['independent_review']={
        'evidence':legacy_review.relative_to(REPO).as_posix(),'sha256':sha(legacy_review)}
    observed['prepared_not_applied']['execution_prerequisites_r3']['owner_action_status']='IN_RETURN_SERIES_NOT_EXECUTED'
    observed['prepared_not_applied']['execution_foundation_r3']['owner_action_status']='IN_RETURN_SERIES_NOT_EXECUTED'
    observed['prepared_not_applied']['disposable_project_importer']['owner_action_status']='IN_RETURN_SERIES_NOT_EXECUTED'
    observed['ordinary_windows_regression']={
        'status':'PASSED_WITH_EXPLICIT_CAPABILITY_SKIPS','passed':passed,'skipped':count['skipped'],
        'deselected':2,'failures':0,'errors':0,'source_files':final['source_files'],
        'source_unchanged_during_run':True,'evidence':(archive/'summary.json').relative_to(REPO).as_posix(),
        'evidence_sha256':sha(archive/'summary.json'),'initial_failed_result_preserved':True,
        'system_or_docker_or_provider_acceptance_claimed':False,'installed_runtime_changed':False}
    heap=EVIDENCE/'desktop-heap-collector-research-2026-10-07.json'
    observed['prepared_not_applied']['desktop_heap_collector_research']={
        'status':'NATIVE_USAGE_ACQUISITION_CONTRACT_UNVERIFIED','evidence':heap.relative_to(REPO).as_posix(),
        'evidence_sha256':sha(heap),'actual_usage_sample_obtained':False,'collector_implemented':False,
        'capacity_or_gui_object_counts_are_not_used_bytes':True,'boot_debugging_or_tracing_changed':False}
    observed['return_instructions']={'path':str(guide),'sha256':sha(guide),
        'commands':5,'run_one_at_a_time':True,'stop_on_first_failure':True,
        'administrator_required_for_privileged_phases':True,'browser_interaction_required_for_two_provider_series':True,
        'automatic_retries':False,'applied_by_this_preparation':False}
    observed['latest_preparation_utc']=datetime.now(timezone.utc).isoformat()
    observed['remaining']=[
        'Run reviewed return series once: actual SYSTEM Docker/R inspection, scoped RAM/empty registry provisioning, protected disposable project import.',
        'After that success, run prepared physical two-container RED/GREEN acceptance and independently check startup SLA; capacity remains four.',
        'Save bounded administrator legacy metadata diagnostic; resolve historical continuity and exact repair-budget authority without resetting spend.',
        'Complete attended Codex and Claude six-profile sign-ins and exact r3 authentication status receipts; verify account/model/effort controls separately.',
        'Resolve Agy isolated native inference-only/serving-model/effort and subscription-only controls; owner Agy remains available.',
        'Complete Chapter06 routed model planning/coding/different-provider reconciliation/integration/rollback acceptance.',
        'Measure actual four-worker queue at its real database location and perform controlled reboot/recovery.',
        'Verify desktop-heap usage acquisition and process binding, then perform 48-hour resource/handle/heap acceptance before activation.']
    observed_path.write_text(json.dumps(observed,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

    handoff.write_text(f'''# Windows setup handoff — 7 October 2026

## Current state and owner return

The pipeline remains stopped. The repaired r3 runtime is installed, the published
knowledge index passed actual SYSTEM verification, and all six fresh worker denial
receipts passed validation with cleanup verified. Six identities share **four**
execution slots. All model jobs retain canonical Chapter 06 routing.

The owner is at work. No elevated commands, new SYSTEM tasks, Docker containers,
provider sign-ins, model jobs or daemon activation were performed during this
preparation. The next protected steps require the owner's Administrator console.

Use [the ordered return guide](evidence/windows-2026-10-06/return-setup-owner-guide-2026-10-07.txt)
or the live `windows-deployment-next/RETURN_SETUP.txt` in the chat workspace.
It supersedes `MORNING_SETUP.txt` and the old standalone preflight request.
Open Administrator Windows PowerShell as `AETHERDESK\\ansac`, paste one command
at a time and wait. Stop on the first error or held result; preserve all artifacts
and return the sanitized failure. Do not retry, delete a destination, skip a
failed phase or rerun earlier installers/knowledge/worker checks.

The guide contains five commands:

1. `run-return-setup-r3.ps1 -Apply`: exact reviewed Docker/R SYSTEM inspection,
   scoped RAM and empty registry provisioning, then protected disposable Git
   project import. Stops automatically on the first failed phase. Success is
   `THREE_SETUP_PHASES_VERIFIED`. This creates seven scoped RAM directories,
   missing exclusions for six slot paths, a new RAM ledger and an empty four-slot
   registry; SQLite may create transient WAL/SHM in the new registry directory.
   It preserves the R: root, drive settings and startup task.
2. `accept-docker-execution-r3-v1.ps1 -Apply`: after step 1 succeeds, exercises
   at most two owned single-use containers with a fixed RED/GREEN fixture,
   verifies restrictions and production cleanup, and records startup timing.
   Success is `DOCKER_PHYSICAL_ACCEPTANCE_VERIFIED`. A correctness pass with
   `DOCKER_PHYSICAL_CORRECTNESS_VERIFIED_SLA_HELD` still stops the sequence.
   This does not run a Chapter 06 model workflow or leave a warm pool running.
3. Save `inspect-legacy-continuity.ps1` output with `Out-File -NoClobber` to
   `legacy-continuity-admin-20261007.json`. Metadata only; original database
   contents remain unopened. Inaccessible or absent metadata is not zero spend.
4. `login-six-workers-attended-r3.ps1 -Provider codex -Apply -Interactive`:
   six sequential human browser/device sign-ins, then six authentication checks.
5. The same attended series for `-Provider claude`. Follow its browser link;
   if a returned code is requested, use P and the helper's masked input. Never
   paste codes into chat or commands. Both series stop on failure, preserve an
   attempt record and refuse repeats. Expected status for each provider is
   `ALL_SIX_NATIVE_SUBSCRIPTION_AUTHENTICATION_VERIFIED`.

Do not redirect or record the login console. Successful authentication does not
attest exact account identity, paid plan, serving model or inference readiness.
Existing owner sign-ins remain intact. Full [attended instructions](evidence/windows-2026-10-06/attended-native-series-r3-preparation-2026-10-07/HUMAN_NATIVE_LOGIN_SERIES_R3.txt)
are also beside the live workspace guide. Agy is available, signed in and functioning
for the owner; its remaining holds concern isolated pipeline integration.

## Actual Windows deployment evidence

- r3 root: `C:\\Program Files\\CoChem\\Pipeline4.2.7-windows-20261007-r3`.
  [Installed receipt](evidence/windows-2026-10-06/stopped-runtime-r3-installed-receipt.json)
  SHA256 `3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6`.
  The installer verified 15,013 custody entries. Independent public checks bound
  166 frozen source files, 109 assets and unchanged r2 configuration.
  r2 and every previous installation remain preserved; do not reinstall.
- Configuration SHA256: `135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c`.
  It selects future scoped `R:\\CoChem427-windows-20261007`, six identities,
  four shared slots and Chapter 06 catalogue 2 / scoring algorithm 1.
- [Knowledge SYSTEM result](evidence/windows-2026-10-06/published-knowledge-verification-owner-result.json):
  `PUBLISHED_KNOWLEDGE_READ_ONLY_VERIFIED`, 137 documents, 1,708 sections,
  138 corpus files, SQLite integrity OK. Receipt
  `f9a1244d201927b888be0a3e03978e1c19b2c33940d609b6ed1dbec36b54a40b`;
  generation `g-abed1cd030c746559aed6bb30a47093a`, index
  `af8fc1bf83885d4bfdf14c8273d250371578a1ed59c71a7a9f07296d954c0d8d`.
  Do not rebuild or rerun recovery/the successful verifier.
- [All six r3 SYSTEM worker receipts](evidence/windows-2026-10-06/worker-denials-r3-system-2026-10-07/summary.json)
  passed exact nonce/SID/PID/handle checks and reported cleanup verified.
  The archival check read existing evidence and did not rerun workers. The next
  preflight validates exact terminal task definitions before new inspection.
- The old slot1 failure and all earlier roots/tasks/receipts remain preserved.
  Its cleanup field stays false; later evidence does not rewrite it. The r3
  installer checked its task was terminal. The actual Job Object affinity-order
  repair retained required E-core affinity and limits; see
  [repair evidence](WINDOWS_JOB_AFFINITY_FIX_4.2.7_2026-10-07.md).
- PawnIO 2.1.0 and one actual SYSTEM CPU sample (17 sensors, 68 C maximum) passed.
  HWiNFO Free and its settings remain unchanged. One sample is not 48-hour evidence.
- Existing 8 GiB ImDisk R: and `\\Mount_CoChem_RAMDisk` scheduled startup task remain
  preserved. Task XML SHA256
  `1da7687a165ddfb425cb01147f4f5cbd3f7ba035947063ef396e268d7babc928`.
  Original credentials, databases and repair budgets were not replaced/reset.

## Completed ordinary Windows preparation

| Package | Ordinary Windows tests | Actual privileged phase |
| --- | ---: | --- |
| [Three-step return wrapper](evidence/windows-2026-10-06/return-setup-series-r3-preparation-2026-10-07/preparation.json) | 24 passed | Not run |
| [Docker/R inspection leaf](evidence/windows-2026-10-06/execution-prerequisites-r3-preparation-2026-10-07/preparation.json) | 45 passed | Not run |
| [Scoped foundation leaf](evidence/windows-2026-10-06/execution-foundation-r3-preparation-2026-10-07/preparation.json) | 44 passed | Not run |
| [Disposable project importer](evidence/windows-2026-10-06/disposable-project-import-preparation-2026-10-07/preparation.json) | 27 passed | Not run |
| [Physical Docker candidate](evidence/windows-2026-10-06/docker-execution-r3-v1-preparation-2026-10-07/preparation.json) | 37 passed | Not run |
| [r3 native login/status leaves](evidence/windows-2026-10-06/native-r3-login-status-preparation-2026-10-07/preparation.json) | 156 passed | Not run |
| [Attended six-login series](evidence/windows-2026-10-06/attended-native-series-r3-preparation-2026-10-07/preparation.json) | 42 passed | Not run |
| [Legacy metadata diagnostic](evidence/windows-2026-10-06/legacy-continuity-preparation-2026-10-07/preparation.json) | 13 passed | Administrator view pending |

Every package passed independent review and is frozen with hashes. These are
disposable-fixture/control-flow tests, including simulated privileged/provider
boundaries where documented. The Docker candidate executes real production
SQLite/lease/cleanup logic with an explicitly inert Docker transport in its
tests. None of these preparation passes is physical Docker/SYSTEM/model acceptance.
The [legacy diagnostic independent review](evidence/windows-2026-10-06/legacy-continuity-independent-review-2026-10-07.json)
is recorded separately from its original frozen package. Its inventory caps bound
accepted output scope, not a universal execution deadline.

The [repository regression follow-up](evidence/windows-2026-10-06/ordinary-regression-followup-2026-10-07/summary.json)
finished **{passed} passed, {count['skipped']} skipped, 2 deselected, zero failures/errors** on
ordinary Windows Python 3.12.13. Source/test/script inventory stayed unchanged
during the run. Real SYSTEM Git fixtures and unavailable symlink privileges are
explicit skips; seven fresh-install native payload cases cannot run after the
protected destination exists. Two native CLI capability calls were deselected.
These gaps remain unexecuted coverage. Historical Linux results are separate.

The initial broad run's 196 failures and 34 setup errors are preserved, along
with its source inventory/XML/log. The next run had one Windows socket fixture
failure: a deliberately rejected excess connection reported abort instead of
reset/EOF. That result is also preserved. The test now accepts both terminal
connection errors and still requires a fresh authenticated request to prove
handler capacity was released. Production service behavior was unchanged.
Repairs corrected Windows fixture prerequisites,
explicit trusted Git bindings, staging-test preservation expectations and a real
read-only cutover planner bug: raw .NET DirectoryInfo.Parent values lack the
PowerShell PSIsContainer adaptation under StrictMode. Production token/ACL guards
were not relaxed. These changes do not modify the installed r3 runtime or frozen
deployment helper packages.

## Remaining acceptance and implementation dependencies

Legacy continuity remains unresolved. Seven snapshots map 646 jobs (324 historical
completions, 322 held). Repair receipts/attempt counts cannot establish exact spend.
The ordinary bounded metadata diagnostic still saw a legacy task/process and
inaccessible private paths; it does not prove writer quiescence. The proposed
unresolved-budget evidence was checked against r3's schema without opening or
constructing real ledgers. Paired immutable budget-hold bootstrap is design only,
not applied. Keep supervisor migration/installation held; never turn unknown spend
into zero. See [budget preservation](WINDOWS_LEGACY_BUDGET_HOLD_4.2.7_2026-10-07.md).

After protected setup and attended sign-ins, verify exact native account/model,
inference-only and effort contracts, including Agy's subscription-only behavior.
Then perform Chapter 06 routed planning, coding, different-provider reconciliation,
Git integration/rollback and actual four-worker queue acceptance at the real
database location. Fixed Docker RED/GREEN testing does not satisfy that workflow.

The [desktop-heap research](evidence/windows-2026-10-06/desktop-heap-collector-research-2026-10-07.json)
records a remaining native usage collector contract. Capacity APIs and USER/GDI
object counts cannot establish used bytes. Documented debugger output gives a
rounded rate, without verified precision, process/desktop association or a
sustained acquisition method on this host. No collector was fabricated, debugger
attached, trace started, boot setting changed or driver installed by this work.
Obtain a reviewed actual sample/acquisition route before implementing its protected
adapter. The unchanged harness requires samples at most ten seconds old, exact
process creation-time binding and usage strictly below 15 percent.

Controlled reboot/recovery and 48-hour resource/handle/desktop-heap observation
remain separate required Windows acceptance. No six/eight-slot expansion or daemon
activation is authorized by these preparation results. General readiness is not a
read-only substitute: it can refresh knowledge and change state. The older
`initialize-execution-readiness.py` draft remains disabled.

The [machine-readable observation](../config/windows/aetherdesk-427.observed-20261007.json)
distinguishes actual results, failed attempts, prepared steps and remaining work.
The [previous handoff](evidence/windows-2026-10-06/morning-handoff-before-return-series-2026-10-07.md)
is retained for history; its superseded commands must not be followed.
''',encoding='utf-8')
    print(json.dumps({'status':'RETURN_PREPARATION_RECORDED','regression':{'passed':passed,**count},
        'evidence':str(archive/'summary.json'),'evidence_sha256':sha(archive/'summary.json'),
        'guide':str(guide),'guide_sha256':sha(guide),'handoff':str(handoff)},indent=2))

if __name__=='__main__':main()
