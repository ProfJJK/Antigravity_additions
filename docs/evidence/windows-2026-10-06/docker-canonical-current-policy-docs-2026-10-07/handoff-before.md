# Windows setup handoff - 7 October 2026

## Current state - Docker physical correctness passed; startup SLA held

The actual protected SYSTEM receipt returned
`DOCKER_PHYSICAL_CORRECTNESS_VERIFIED_SLA_HELD`. The
[exact receipt, inputs and reconstructed safe owner result](evidence/windows-2026-10-06/docker-physical-sla-held-system-2026-10-07/summary.json)
are archived; receipt SHA256 is
`2ca594fdca714ff48ba8f9a8201f5f27c0e71ba833a5aa5a23cbbe3266ad2e74`.

**Physical boundary checks and cleanup passed; startup timing did not.** Two
distinct single-use containers ran the fixed RED/GREEN fixtures. RED produced
its expected one failing test; GREEN passed both tests. Source verification and
cleanup passed for each. Actual Windows measurements were:

| Trial | Startup seconds | Test-cycle seconds | Startup SLA |
| --- | ---: | ---: | --- |
| RED | 2.696504831314087 | 4.562000000005355 | Failed |
| GREEN | 2.658830165863037 | 4.23499999998603 | Failed |

The receipt explicitly reports final physical census empty and zero owned,
active, preparing, warm, quarantined and unknown-owned registry counts, with
published capacity four. These are completion-time SYSTEM receipt claims, not
a fresh private database query or current Docker census. Registry ownership,
RAM ledger, R: volume/startup task, credentials and repair budgets were preserved.
The acceptance's new lease/receipt history remains in the registry.

Task result 2 comes from the owner wrapper report. This ordinary collector
verified public receipt/input bytes and bindings without querying COM task state,
opening private databases or invoking Docker. The safe owner result is an
allowlisted reconstruction, not a captured raw console transcript.

**Do not rerun acceptance, clear quarantine, reset the registry or skip the hold.**
Preserve every task/root/receipt and review startup timing before a separately
reviewed next action. The owner guide is maintained separately and its Docker
command now records an attempted held phase; later commands remain held.

The [foundation/private-project continuation](evidence/windows-2026-10-06/foundation-project-continuation-owner-success-2026-10-07/summary.json)
remains complete: six scoped RAM roots and a new four-slot registry were verified
by the directly read foundation receipt; private bare-project success is verified
by the owner wrapper (ordinary private receipt access is denied, not absent).
The original foundation failure remains preserved and its exact cause unknown.

The pipeline remains stopped. No provider sign-in, model job or Chapter 06 routed
coding workflow was tested. Six identities still share four slots and Chapter 06
routing remains unchanged. Ordinary Windows preparation tests and prior Linux
results remain separate from this actual physical correctness result. Historical
preparation tables below retain their earlier scope.

## Actual Windows deployment evidence

- r3 root: `C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3`.
  [Installed receipt](evidence/windows-2026-10-06/stopped-runtime-r3-installed-receipt.json)
  SHA256 `3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6`.
  The installer verified 15,013 custody entries. Independent public checks bound
  166 frozen source files, 109 assets and unchanged r2 configuration.
  r2 and every previous installation remain preserved; do not reinstall.
- Configuration SHA256: `135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c`.
  It selects future scoped `R:\CoChem427-windows-20261007`, six identities,
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
- Existing 8 GiB ImDisk R: and `\Mount_CoChem_RAMDisk` scheduled startup task remain
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
finished **2708 passed, 387 skipped, 2 deselected, zero failures/errors** on
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
