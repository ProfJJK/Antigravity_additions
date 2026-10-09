# Windows legacy repair-budget hold

This follow-up implements an **explicit unresolved-authority hold**, not a conversion of legacy telemetry into known spend. It changes repository source only. The frozen installed package, installed configuration, real budget databases, accounts, credentials, services and tasks were not modified or activated.

S427-SRE-004/005/006 and S427-DEPLOY-002/004 require component history and charged generation/review budgets to survive upgrades and rollback. The inspected legacy job attempts, quota row and recovery receipts do not establish a complete modern ledger. Unknown remaining allowance must stay unknown. A missing source directory or empty replacement ledger cannot establish that no prior allowance was consumed.

## Implemented behavior

`cochem_supervisor.budget_authority` defines a singleton immutable `UNRESOLVED_LEGACY_AUTHORITY` record in each modern budget ledger. Evidence contains a bounded reason and source-artifact SHA-256 references. The record stores its canonical evidence digest and original recording time. Repeating identical evidence preserves the original row; differing evidence, SQL UPDATE, DELETE and INSERT OR REPLACE are rejected. There is no resolution, clearing, reset or allowance-granting API.

`Ledger.hold_legacy_budget_authority(evidence)` and the corresponding `RecoveryLedger` method install the hold inside `BEGIN IMMEDIATE`. Each spend decision checks its own ledger in the same write transaction:

- Initial repair reservation and additional routed reviewer calls return no reservation.
- Review lease resume and incident unblock reject the operation; heartbeat cannot renew execution authority.
- Component observations remain durable, including healthy observations, but return `authority_hold` instead of consuming an automatic recovery attempt.
- Existing paid attempts, component attempt counts, caps, cooldowns, checkpoints, histories and terminal cleanup outcomes remain preserved. No fake paid attempt is inserted to encode uncertainty.

SQLite triggers independently prevent new generation/review spend, running-lease renewal, blocked-incident reopening and component attempt increments. They remain in SQLite backups and continue to reject the corresponding SQL even when compatible older code does not know the new Python guard. This is not a claim that arbitrary privileged code which drops triggers/schema can be contained. Rollback to an incompatible ledger implementation remains a stopped, reviewed migration decision.

The supervisor checks both ledgers before repair, review continuation, component recovery, observation-loop actuation, automatic release recovery, Warden activation and live probing. An explicit hold in either ledger blocks these paths visibly. Invalid/unreadable authority evidence also holds. It does not initialize the peer ledger merely to check its status. Modern ledgers without an explicit unresolved hold keep their existing behavior; status explicitly says `historical_spend_verified: false` and `remaining_legacy_allowance: null`. Absence of a hold is not a legacy budget attestation.

Ordinary SQLite backup includes the hold and its SQL guards. Advanced migration additionally verifies the source hold, exact provenance row and guard definitions in the target, rejecting a lost/changed hold or a removed rollback guard. Existing 2-per-incident, 4-per-day and 1,800-second minimum deployment policy remains unchanged.

## Deployment remains separate

The current Windows supervisor remains uninstalled/unregistered for this continuation. No helper in this change opens a production ledger or resolves the real host's unknown history. A future protected bootstrap must, while both supervisors are stopped, require an explicit authority decision and verify matching unresolved provenance in **both** ledgers before activation can be considered. It must never infer `NO_PRIOR_LEDGER` from an absent/default path. A crash while preparing one ledger cannot authorize activation of the other. Current engine checks either recorded hold, but direct ledger consumers must use the correctly prepared corresponding ledger.

A future resolution/importer needs its own reviewed, append-only authority decision preserving all original unknown incidents and component histories. This change deliberately supplies no way to approve an allowance by editing configuration or unblocking an incident. The original databases and preservation artifacts remain the source of historical evidence.

## Actual Windows verification

The final actual Windows run passed **114 tests with two skips in 27.82 seconds**, using the absolute staged `venv-copy\Scripts\python.exe` interpreter, Python **3.12.13**. It includes all 28 new tests plus the existing state, component, budget-upgrade, review-continuation, replay and engine subset. These exercise original charge/cap preservation, pending review/heartbeat/unblock refusal, component observations without new attempts, SQL guards against old-style callers, direct immutable-row tampering, concurrent callers, an uncommitted hold serializing with a waiting reservation, SQLite backup, advanced migration, and engine exits before native/board/lifecycle actions. A real temporary Windows junction is rejected even when the leaf database is absent, and permission errors never become false absence. The tests use temporary databases and explicit downstream sentinels; they do not run model jobs or claim native Windows isolation.

Earlier system-Python 3.14 checks passed 26 new tests and 111 tests/two skips in the associated then-current regression subset. Those results are retained as earlier evidence, not substituted for the final staged-interpreter run. The final skips were the intentionally non-Windows-only service rejection test and an existing symbolic-link test requiring a privilege unavailable to this account. The new real Windows junction test passed; no skipped test is presented as a pass.

An earlier regression run had 84 passes, two skips and two failures because subprocess fixtures could not import the repository from system Python. The corrected invocation set `PYTHONPATH` to the repository `src` only for that process and restored the previous environment value afterward. The first staged Python 3.12 run had 113 passes, two skips and one failure: the junction was rejected with `RamdiskError`, which was outside the engine's handled authority-error classes. The narrow fix translates only this reparse rejection into a sanitized `ValueError`, allowing the engine to report a visible hold; permission errors still propagate. The full final subset then passed. All reports remain preserved:

- `docs/evidence/windows-2026-10-06/unresolved-budget-authority-tests.xml`
- `docs/evidence/windows-2026-10-06/unresolved-budget-authority-followup-tests.xml`
- `docs/evidence/windows-2026-10-06/unresolved-budget-regression-tests.xml`
- `docs/evidence/windows-2026-10-06/unresolved-budget-regression-followup-tests.xml`
- `docs/evidence/windows-2026-10-06/unresolved-budget-python312-tests.xml`
- `docs/evidence/windows-2026-10-06/unresolved-budget-python312-followup-tests.xml`

These results are actual Windows fixture/test evidence, separate from prior Linux results. Production migration, paired hold bootstrap, model authentication/inference, rollback, reboot and sustained deployment acceptance remain unperformed by this change.
