# 4.2.3: Independent pipeline supervisor

4.2.3 adds a supervisor outside the pipeline's protected installation and
Python environment. It observes native heartbeat and read-only database
progress, attempts one restart for heartbeat/native-status failures, and can
escalate eligible defects to a configured native subscription CLI repair worker.

## Changes

- Added a separate incident/attempt ledger and bounded repair policy: two
  attempts per incident, four per day, and a 30-minute cooldown by default.
- Added configurable native repair routing: Codex Astra by default, with
  Claude Fable as an explicit alternative. Repair runs use a standard Windows
  identity and its subscription login rather than API-key fallback.
- Added isolated candidate preparation and source allowlists, validated by
  immutable acceptance tests outside the repair workspace.
- Added protected versioned release activation through an atomic pointer,
  restart checks using workflow state and heartbeat progress, rollback on
  failure, and journal-based crash recovery.
- Added operator update requests through the same bounded repair and acceptance
  path. Disabled automatic deployment leaves a validated candidate BLOCKED;
  there is no manual promotion command.
- Added a durable repair cleanup quarantine: unverified process/profile cleanup
  holds further normal work until an administrator verifies cleanup and clears
  the protected marker. Restarting the supervisor alone does not clear it.
- Added native Warden ownership records and verified Job Object shutdown.
  Same-boot uncertain launcher crashes stop automatic deployment; prior-boot
  records allow recovery after a full Windows Restart.
- Repair CLI help incompatibility now requires administrator configuration
  repair. Pipeline worker protocol defects can still qualify for bounded code
  repair. Removed the unsupported Claude `--permission-prompts` argument after
  checking the installed Claude 2.1.101 help; this is not live inference evidence.
- Added the `cochem-supervisor` entry point, supervisor test collection in CI,
  and matching checks in the locally preserved cloud setup proposal.

See [the supervisor guide](SUPERVISOR_4.2.3.md) for its boundaries and
[the 4.2.2 release notes](RELEASE_4.2.2.md) for the underlying pipeline.

## Validation boundaries

The integrated Linux command `python -m pytest supervisor_tests pipeline_tests mcp_tests -q -p no:cacheprovider`
completed with **866 passed, 7 skipped**, two Authlib deprecation warnings, and
43.15 seconds elapsed. All seven skips require native Windows. The uv wheel
build produced `cochem-4.2.3-py3-none-any.whl`. Codex 0.142.0 and Claude 2.1.101
version/help contracts were checked without inference.

The final isolated acceptance run copied candidate source and the protected
test policy into separate directories: **357 passed, 3 Windows-only skips**,
process exit 0, and all 360 JUnit test identities validated.

Component/contract tests do not prove successful Windows deployment or live
provider repairs. Native Windows ACLs, independent installation behavior,
subscription authentication, accepted candidate deployment and rollback still
need target-host acceptance. The unresolved native Agy headless/auth/result
contract from 4.2.2 remains a prerequisite for Gemini synthesis.

The supervisor cannot update its own protected installation, acceptance tests,
configuration or dependencies through this repair path. Database/schema
migration and data rollback are outside its release mechanism. Source tests
and live workflow checks establish bounded regression evidence, not arbitrary
code attestation. The candidate's live smoke workflow also consumes quota
beyond the repair model call.

Interrupted attempts remain charged. A crash between committed release
activation and recording the attempt outcome can allow a later bounded retry;
the release does not promise exactly-once repair execution.

The reusable environment recipe remains in
[`config/cloud-environment.proposed.json`](../config/cloud-environment.proposed.json).
Its earlier draft save failed with `stale_base`; updating this local proposal
does not claim that the cloud environment draft was saved. No retry of that
known-stale draft is part of this release.
