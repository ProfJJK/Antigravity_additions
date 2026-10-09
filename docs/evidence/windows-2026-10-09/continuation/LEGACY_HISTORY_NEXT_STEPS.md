# Legacy job, Oracle and repair continuity — read-only review

No legacy/live database, installed configuration, task, or budget was changed. Five preserved snapshots were reopened using SQLite `mode=ro&immutable=1`, `query_only`, and `trusted_schema=OFF`. Their exact hashes still match the original snapshot receipt, integrity checks return `ok`, and the bytes were unchanged afterward. Queries selected only schema/counts/status/attempt totals, never job payloads, rule text, credentials or private document content.

The authoritative evidence for this inspection is private:

`C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\legacy-history-review-20261007T055000Z.json`

SHA-256: `6d748dd9b1595aeefea8dfb5b40c8f40b663c3a036893b42c8531527357b808c`.

These counts describe snapshots captured at **2026-10-07 02:44 UTC**, not current live state. Their backups are individually consistent; they are not a coordinated cross-database cutover.

| Snapshot | Captured history |
|---|---|
| `01-job_board.db` | 136 completed jobs; 72 recovery telemetry rows; 18 blackboard events; 322 layer-one queue rows |
| `03-job_board.db` | 486 jobs: 188 completed, 291 pending, seven running; 72 recovery telemetry rows; 18 blackboard events; 1,377 layer-one queue rows; one quota row; 152 file-stability rows |
| `05-job_board.db` | 24 pending jobs |
| `06-oracle_tracking.db` | Legacy `watermarks(task_id, rule_hash)`, zero rows |
| `07-wikirag.db` | 12 legacy rule rows, text not read |

All three job databases have legacy `jobs` tables and **no modern `pipeline_jobs` schema**. They must not be passed to `JobStore` as if its additive schema initialization were an importer: that would create an unrelated empty modern queue alongside the old rows. `upgrade_preview._captured_database` correctly refuses them as incompatible. Existing native attempts, parent/fencing information, quota rows, event history, result paths, and source DB identity must survive any eventual conversion.

## Concrete next implementation and cutover

1. Preserve the current snapshots and exact original metadata. At a planned cutover, identify and stop only actual legacy ingress/job writers and drain or explicitly hold the seven captured running records after fresh observation. `COCHEM_DISABLE_SRE=1` is not an ingress stop. Preserve the separate `CoChemHostWarden_V412` Hyper-V service and the R: startup task. Take fresh SQLite online backups after quiescence, recording all WAL/lease/worker observations and a consistent cutover boundary; today's seven individually captured files are not that proof.
2. Build a separate immutable legacy archive under a fresh SYSTEM-only private root, proposed `C:\ProgramData\CoChemPipeline427\private\legacy-history-20261007`. Its reviewed manifest should bind each original source identity, snapshot SHA/size, capture time and related receipts/artifact paths. Refuse existing destinations and missing/changed input bytes. Preserve whole exact databases; do not flatten or deduplicate overlapping job IDs across sources. Use a source-hash plus primary-key namespace for reference IDs. This is a proposed archive path, not a deployed/configured path; do not change the frozen configuration for it now.
3. Implement an explicit read-only archive browser/import preview and durable continuation map before old jobs can become executable. Default every unfinished legacy job to `LEGACY_IMPORT_HOLD`, outside the modern executable queue. Preserve original attempts/limits/status, leases, quota metadata, baseline commits, results and receipts. A mapping must prove an explicitly selected old request's immutable artifact lineage and current Chapter 06 contract before creating any resumable modern workflow. Do not mark old success as current Windows acceptance, silently reset attempts, auto-resubmit old pending work, or manufacture current routing evidence.
4. Keep the existing legacy Oracle DB byte-exact in that archive. The modern Oracle requires durable payload/acknowledgement records and distinct delivery identity; its schema cannot infer these from a `(task_id, rule_hash)` table. The captured table is empty, so there is no captured delivery to translate. After genuine workflow selection, replay pending events through the current Oracle with new protected delivery receipts, preserving their legacy provenance. Do not pre-acknowledge old work or invent a watermark. The 12 legacy rules remain historical inputs until separately reviewed against the current canonical rules; do not silently activate their text.
5. Keep the supervisor unregistered/stopped until repair-budget authority is resolved. The preserved job attempt totals, 72 recovery rows, quota row and legacy repair receipts are not the modern incident/day/component ledgers. An empty replacement DB or missing `PreviousDataRoot` must not mean zero spend. If no genuine modern ledger is found, implement an explicit persistent `UNRESOLVED_LEGACY_AUTHORITY` state before any supervisor/component reservation can occur. Enforce it transactionally in initial reservation, additional calls/review/resume, and independent component recovery; preserve known charges and all uncertain incidents. This policy needs tests against restart/upgrade/rollback and simultaneous reservations, and a versioned deployment. `auto_deploy:false` does not prevent paid generation/review and is not this guard.

The current unknown budget state should be retained without asking the owner to reconstruct exact spend from memory. Any later authority decision must be recorded and reviewed as a narrow release of a specific hold, never a reset. No budget importer or legacy execution importer is currently claimed to exist.

## Morning user involvement

The reviewed administrative continuation can perform only the prepared private knowledge/index and worker-denial acceptance stages. Then the owner completes isolated Codex/Claude browser/device authentication using the reviewed login helper, one identity/provider at a time; no credential contents should enter chat. Those steps do not authorize inference or activate daemons. The legacy migration and budget guard above remain code/review work, and selected legacy requests can stay preserved and held while it proceeds.

Reviewed implementation: `src/cochem_pipeline/upgrade_preview.py`, `store.py`, `oracle.py`, `runtime.py`, `scripts/install_supervisor_windows.ps1`, and the canonical Windows handoff. No frozen source was edited during this review.
