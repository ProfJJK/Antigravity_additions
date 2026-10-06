# 4.2.5: Coding execution, container tests and RAM workspaces

4.2.5 addresses the missing connection between planning, coding workspaces and
real isolated test execution. It adds the Docker/RAM-disk/hardware components
and deployment checks while retaining native Windows subscription CLIs,
controller-owned SQLite authority, the four-worker limit and the user's exact
complexity routing. Final planning synthesis remains Gemini 3.1 Pro.

The [execution guide](EXECUTION_4.2.5.md) describes deployment and command
interfaces. The [requirements ledger](REQUIREMENTS_4.2.5.md) records the source
clauses, discovered omissions, historical conflicts and remaining evidence.
Neither document certifies Windows or live-model acceptance merely because
source code or a command-line contract exists.

Windows installation uses fresh protected `Pipeline4.2.5` and
`Supervisor4.2.5` directories and frozen dependencies from `uv.lock`, with a
separate protected standalone uv executable. Registered coding projects require
explicit reviewed RAM-disk, Docker, hardware and native execution-limit policy.
A SYSTEM provisioning task checks execution prerequisites before task
registration. Existing worker/repair identities, native credentials and managed
task names are preserved. The previous supervisor ledger is migrated from
`CoChemSupervisor424` into fresh `CoChemSupervisor425` using SQLite backup,
retaining charged attempts, cooldowns, history and quarantine.

No completed Windows installation or live three-provider coding/planning run
is claimed. The Agy native headless/auth/result contract and actual account
model availability still require target-host verification. ImDisk/AWE backing,
SYSTEM Docker access and real hardware sensor availability cannot be validated
by Linux policy tests.

The final combined Linux suite completed with **1,826 passed, 29 skipped,
zero failures or errors** in 270.404s. This run included actual Docker and
offline native Codex/Claude contract checks; it did not perform provider
inference. These totals come from one run, not overlapping component suites.
Seven PowerShell helpers were syntax-parsed under Microsoft PowerShell 7.4.13
without executing their Windows provisioning commands.

An independent acceptance run against a fresh copied source snapshot completed
with **1,102 passed, 45 skipped, zero failures or errors** across 35 protected
test targets. The source, external tests and bootstrap trees retained their
original hashes. Its actual-Docker cases were deliberately disabled because
the repair identity must not have Docker-engine authority; the full suite
checks actual Docker separately. The 4.2.5 wheel was built and all 103 packaged
Python modules matched the release source byte for byte. Its SHA-256 is
`7691c1789f398673e6a4329455a11d49e021e28344128203ba6291f3d9618721`.
The [validation record](VALIDATION_4.2.5.json) preserves report hashes, exact
skip reasons, environment versions, and the remaining acceptance boundaries.

Real Linux Docker runs exercised network denial, read-only root/tmpfs,
nonroot/capability/resource constraints, actual pytest/JUnit results, rejection
of empty or forged test evidence, immutable source, timeout/OOM137 handling and
owned-orphan cleanup. One historical requirement remains unmet in those
measurements: cold container startup took 4.85s (earlier loaded runs 3.8–6.9s),
above the 1.5s target. A subsequently added single-use warm pool reached test
readiness in 0.4446s for a real fixture. Warm-pool/red-baseline checks executed
against actual containers. Prepared containers remain within the shared native
and container admission and memory limits. Receipts distinguish warm/cold execution and report the SLA
result. This is a measured warm-path pass, not a blanket latency guarantee;
Windows, larger inputs and representative load remain pending.
