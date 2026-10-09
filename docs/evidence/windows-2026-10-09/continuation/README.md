# Preserved Windows deployment continuation

Read [the current source publication](../../../WINDOWS_SOURCE_PUBLICATION_4.2.7_2026-10-09.md)
and [deployment handoff](../../../WINDOWS_AGENT_HANDOFF_4.2.7_2026-10-09.md) first.
This directory preserves selected continuation source, tests and evidence from
the Codex workstation workspace. It is an archive and engineering starting
point, not a sequence of installer commands to rerun.

`continuation-manifest.json` records the original and published SHA256 of each
preserved artifact. Historical evidence retains original paths, installed-root
bindings and failure states. Adjacent `*.redaction-audit.json` records explain
the three sanitized failure reports in this bundle. The fourth redaction is
under the older `windows-2026-10-06` evidence directory.

The `resource-observer-r3-v4` source package is the commissioned independent
recorder. The held-supervisor candidate bytes were installed into its independent
runtime, while Pipeline r3 remains frozen. The telemetry repair comparison is
still an uninstalled experiment. See [the continuation map](portable-continuation-audit-20261009.md)
for exact source groups, path dependencies and completed phases.

No environments, dependency binaries, generated fixture databases, production
databases, OAuth/native sessions or credential stores are included. The actual
private submission journal and protected task/installation state remain on the
workstation. `owner-wrappers` preserves the earlier standalone scripts and tests;
it does not make their original private/protected paths portable.

Never run `RETURN_SETUP*.txt`, a highest-numbered recovery script or an archived
commissioning series without reviewing its documented state, one-shot intent,
source/dependency pins and previous receipts. Prepare an additive successor
under the new checkout for any continuing action. Keep the canonical 50 MiB/5%
guard and all existing histories and budget holds.
