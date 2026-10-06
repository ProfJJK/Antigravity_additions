# 4.2.4: Persisted complexity-based model routing

4.2.4 applies the user's corrected route order: complexity 1–3 uses Flash →
Haiku → Luna; 4–6 uses Sonnet → Sol → Gemini Pro; 7–9 uses Opus → Astra with
`low` effort → Gemini Pro; complexity 10 has only Fable → Astra with `ultra`
effort. Earlier complexity bands and route orderings are superseded.

The controller persists route selections and binds native receipts to the
selected provider/model/effort and owned attempt. The existing Oracle, four
active-worker ceiling, isolated chapters, leases/fencing, immutable artifacts
and Gemini 3.1 Pro synthesis barrier remain required. See the
[routing and requirements guide](ROUTING_4.2.4.md).
Synthesis accepts only the exact native model ID `gemini-3.1-pro`; aliases are
not silently substituted.

The upgrade requires a fresh protected 4.2.4 supervisor and external acceptance
snapshot. The frozen 4.2.3 supervisor cannot be reused to approve changed
routing contracts. Installed worker identities and native subscription
credentials must be preserved rather than renamed for the new package version.
The installer refuses active/enabled managed tasks, copies reviewed routing
configuration into the new protected installation, and migrates the old
supervisor ledger with SQLite backup. Attempts, cooldowns, pending charges and
quarantine survive the upgrade; the previous ledger and release pointer remain
available. Unresolved release recovery and differing destination ledgers block
migration instead of resetting budgets.
Registration enables the reviewed task actions without starting them; activation
follows a full Windows Restart. Manual return to 4.2.3 requires compatible
restored or drained/migrated database state, not just the previous source pointer.

## Validation boundary

The final Linux development suite completed with **1,247 passed, 11 skipped**
using `python -m pytest pipeline_tests supervisor_tests mcp_tests -q`.
The skipped tests require native Windows SYSTEM/process/ACL/Task Scheduler
execution. The independent acceptance gate separately completed with **602
passed, 6 Windows skips**, loading a copied candidate from separate protected
bootstrap and test snapshots whose manifests remained unchanged.

The frozen dependency installation, all four 4.2.4 CLI version checks, wheel
build, and isolated wheel imports passed. Physical subprocess fixtures and real
SQLite contention verify the scheduling contracts without claiming paid model
inference. Historical 4.2.3 totals remain historical.

No completed Windows deployment or live three-provider workflow is claimed.
The Agy native headless/auth/result contract still requires target-host
verification. Codex 0.159.0-alpha.3 accepted `low` and `ultra` effort settings
in an offline configuration check; this was not inference or model/account
availability evidence.
