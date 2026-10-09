"""Create a new evidence archive of candidate-only supervision corrections."""
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
WORK = Path(r'C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next')
INSTALLED = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\Lib\site-packages\cochem_supervisor')
TARGET = REPO / 'docs/evidence/windows-2026-10-06/supervision-authority-candidate-2026-10-07'
BEFORE = {'engine.py': 'c2c34601eb5299dc47306e894b7d55743b56d39e79035534198df9bf6c742d64',
          'replay.py': '0ef1ef3c23cb7484edaac347c7af6a04d43b095789f16daae6642db244344728'}

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def exclusive(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)

def main():
    source = {}
    for name, expected in BEFORE.items():
        before = (INSTALLED / name).read_bytes()
        if sha(before) != expected:
            raise ValueError('Installed baseline changed; preserve instead of repinning')
        after = (REPO / 'src/cochem_supervisor' / name).read_bytes()
        source[name + '.before-r3'] = before
        source[name + '.candidate'] = after
        source[name + '.narrow.diff'] = ''.join(difflib.unified_diff(
            before.decode().splitlines(keepends=True), after.decode().splitlines(keepends=True),
            fromfile='installed-r3/' + name, tofile='candidate/' + name)).encode()
    for name in ('test_held_observation.py', 'test_replay_budget.py', 'test_legacy_budget_authority.py'):
        source[name] = (REPO / 'supervisor_tests' / name).read_bytes()
    for name in ('inspect-supervision-authority-gaps-r3-v1.py',
                 'supervision-authority-gaps-r3-reproduction-20261007.json',
                 'supervision-authority-candidate-tests-initial.xml',
                 'supervision-authority-candidate-tests-followup.xml',
                 'supervision-authority-candidate-tests-final.xml'):
        source[name] = (WORK / name).read_bytes()
    review_name = 'supervision-authority-root-review-20261008.json'
    review_raw = (REPO / 'docs/evidence/windows-2026-10-06' / review_name).read_bytes()
    if sha(review_raw) != '5b70a725f6f3f9c997af672c1999a4991d1d91765e68a974d3130b3cef4e1122':
        raise ValueError('Independent review changed')
    source[review_name] = review_raw
    suite = ET.fromstring(source['supervision-authority-candidate-tests-final.xml']).find('testsuite')
    tests = {field: int(suite.attrib[field]) for field in ('tests', 'failures', 'errors', 'skipped')}
    if tests != {'tests': 86, 'failures': 0, 'errors': 0, 'skipped': 0}:
        raise ValueError('Expected candidate verification result is unavailable')
    tests['passed'] = tests['tests']; tests['junit_seconds'] = float(suite.attrib['time'])
    report = {
        'schema': 'cochem-supervision-authority-candidate/1',
        'captured_utc': datetime.now(timezone.utc).isoformat(),
        'state': 'CANDIDATE_SOURCE_NOT_INSTALLED',
        'source_changes': [
            {'path': 'src/cochem_supervisor/engine.py', 'before_sha256': BEFORE['engine.py'],
             'candidate_sha256': sha(source['engine.py.candidate']),
             'change': 'Continue sterile observation and authenticated controller health under an explicit or unreadable authority hold, publish the hold and real observation, and return before every actuator, route/CLI probe or budget-row write.'},
            {'path': 'src/cochem_supervisor/replay.py', 'before_sha256': BEFORE['replay.py'],
             'candidate_sha256': sha(source['replay.py.candidate']),
             'change': 'Read and validate own and existing component-peer immutable authority holds through stable disposable SQLite snapshots. Held or malformed authority keeps allowance unknown; missing peer is unverified. A projection never verifies activation or historical completeness.'},
        ],
        'tests': {**tests, 'python': '3.12.13', 'platform': 'Windows ordinary user',
                  'command_scope': ['test_held_observation.py', 'test_legacy_budget_authority.py',
                                    'test_replay_budget.py', 'test_engine.py'],
                  'fixture_boundaries': 'New disposable real SQLite, real monitor parsing and actual engine/replay decisions. Privileged constructor, authenticated HTTP, and native lifecycle/provider boundaries are inert. No SYSTEM claim.',
                  'prior_failures_retained': 'Initial 79 passed/6 failed: synthetic heartbeat omitted its required version. Follow-up 19 passed/2 failed: byte assertion did not account for existing SQLite read-only coordination sidecars. Production authority reader was not changed.',
                  'byte_preservation_scope': 'Replay preserves source names and bytes, including the component peer. Held tick preserves every existing main DB/history byte; unchanged mode=ro authority reader may create normal empty WAL and SHM coordination files. No budget, attempt, charge, recovery, or history row is changed.'},
        'independent_review': {'reviewer': 'root', 'status': 'PASS_WITH_SCOPE_LIMITS',
            'file': review_name, 'sha256': sha(review_raw),
            'scope': 'Direct installed-to-candidate diff, final source pins and XML inventory; installed runtime and frozen owner startup preserved.'},
        'canonical': {
            'srs_path': 'knowledge/.sources/4.2.7_SRS.model-routing-2026-10-07.md',
            'srs_sha256': sha((REPO / 'knowledge/.sources/4.2.7_SRS.model-routing-2026-10-07.md').read_bytes()),
            'requirements': ['S427-SRE-001', 'S427-SRE-003', 'S427-SRE-004', 'S427-SRE-005',
                             'S427-SRE-006', 'S427-SRE-007', 'S427-OPS-014', 'S427-DEPLOY-002', 'S427-DEPLOY-004']},
        'remaining_prerequisites': {
            'ordinary_new_workflows': 'Do not wait for repair-history reconciliation. The separately reviewed six-profile authentication/controller commissioning and live planning/coding acceptance remain their own path.',
            'independent_detection': 'Candidate makes the established supervisor observe under an authority hold. Deployment still requires a separate protected supervisor interpreter, exact observer/psutil tree and tests, distinct repair identity/layout, protected baseline/release pointer and paired held ledger custody. A pure detector-only commissioning path can omit repair authentication; current full Supervisor constructor still validates its independent installation and repair layout.',
            'paired_uncertainty_bootstrap': {'source': 'bootstrap-unresolved-budget-pair.py',
                'sha256': sha((WORK / 'bootstrap-unresolved-budget-pair.py').read_bytes()),
                'existing_tests': 31,
                'state': 'Reviewed new unpublished construction primitive; no protected launcher or installed pair',
                'missing_implementation': 'A fresh pinned SYSTEM staging wrapper binding complete runtime, exact new private root, current source/metadata and stopped activation; verify both immutable rows/triggers and completion before any future configuration references it. No old root reuse, missing-source zero-spend interpretation, retry/reset, or automatic release.'},
            'non_model_recovery': 'Canonical component recovery is separately bounded: three failures, at least 30 seconds and at most one durable component reservation that survives health/restart/upgrade. It does not spend a model call, but unknown component-attempt history is not permission for a fresh reservation. Current paired uncertainty holds intentionally block service start, Warden restart/recovery and release recovery; this candidate does not release them.',
            'paid_repair': 'Requires authoritative preserved incident/day generation and review charges plus original fingerprints, attempt identities, cooldowns and pending reviews; Chapter 06 routing; a separate authenticated repair account; primary containment/quiescence; protected candidate/reviewer/outer acceptance and bounded smoke. Limits remain two calls per incident, four per UTC day and 1800-second cooldown; smoke retains its separate three-dispatch bound.',
            'history_reconciliation': 'Use already recorded source locations and preserved snapshots. Later reviewed metadata inspection and consistent paused-writer capture may resolve exact modern ledger authority. Preserve original DB/WAL, snapshots and lineage. Legacy job attempts/completions do not prove paid launches. Missing or incomplete paid/recovery evidence stays unknown and held; no fabricated modern charges, zero-spend claim, allowance reset or automatic continuation of old requests.',
            'installation_boundary': 'Do not invoke generic install_supervisor_windows.ps1 against the commissioned Warden: it provisions execution and uses earlier migration/task paths. Prepare a new independent staging/cutover with current r3 bindings, preserving active Warden/config/pointer, all six accounts, four seats, Chapter 06, R volume/task, databases and budgets. No auto-deploy activation before reviewed ownership transfer.',
        },
        'human_required': {
            'now': 'No additional repair/history action or spend estimate. Agent engineering and source/evidence reconciliation can continue.',
            'later_admin': 'One reviewed independent supervisor installation/cutover, preferably bundled with other necessary setup; no manual test sequence.',
            'later_auth': 'Only when paid independent repair is enabled: status-first sign-ins for its distinct repair profile, up to two conditional Codex/Claude browser approvals. Read-only detection needs no provider login.',
            'policy_decision': 'Only if authoritative evidence remains incomplete or the owner chooses a particular old request for continuation. Keep the existing hold until a concrete reviewed decision; never ask the owner to guess historical spend.'},
        'frozen_installed_runtime_changed': False, 'frozen_startup_or_bootstrap_changed': False,
        'legacy_databases_or_tokens_read': False, 'protected_tasks_or_configuration_changed': False,
        'system_or_provider_or_docker_calls': 0, 'paid_jobs_submitted': 0,
        'linux_results_used': False,
        'artifacts': {name: {'sha256': sha(raw), 'bytes': len(raw)} for name, raw in source.items()},
    }
    TARGET.mkdir(exist_ok=False)
    for name, raw in source.items():
        exclusive(TARGET / name, raw)
    raw = (json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()
    exclusive(TARGET / 'preparation.json', raw)
    exclusive(WORK / 'supervision-authority-candidate-preparation-20261007.json', raw)
    print(json.dumps({'path': str(TARGET / 'preparation.json'), 'sha256': sha(raw),
                      'final_xml_sha256': report['artifacts']['supervision-authority-candidate-tests-final.xml']['sha256'],
                      'tests': tests}, indent=2))

if __name__ == '__main__':
    main()
