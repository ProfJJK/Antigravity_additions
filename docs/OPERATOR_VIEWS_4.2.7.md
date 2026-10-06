# 4.2.7 authenticated operator views

Use `pipeline_operator_view` in Antigravity to inspect the controller's retained evidence. Omit identifiers for a deployment overview, pass `workflow_id` for one workflow, or pass `job_id` for one job. These reads do not submit inference, change leases, repair the database, or acknowledge Oracle deliveries.

The view provides:

- Governing specification, amendment, source, execution-contract and routing-policy hashes captured at submission. Current and captured hashes are compared explicitly. A historical missing capture remains unknown.
- An ordered transition timeline and the recorded prerequisite, routing hold, cooldown or hardware pause blocking each job. Aggregate workflow records are distinguished from executing jobs.
- Chapter 06 scores and rationale, captured candidate order, a read-only next-eligible-candidate prediction, actual selected reservations, cooldowns and observed provider holds. The prediction uses the scheduler's shared eligibility checks, including current model/provider/pool limits, observed reservations, asymmetric-review exclusions, backlog, retry cursors and budgets. It identifies skipped candidates and reasons, reports separate host-admission state and never claims to reserve a model. Subscription balances remain unknown unless measured; a quota failure is not a remaining-balance estimate.
- SVG plots of admission capacity, CPU utilization and available RAM from retained transition telemetry and the latest completed hardware sample. Each quantity has its own labelled scale; limiting reasons accompany the samples.
- A Mermaid deployment diagram plus configured identities/paths/endpoints and separately identified native-receipt or engine-preflight observations. Missing observations are not healthy attestations. Observed worker-account drift is reported.
- Protected Oracle delivery/acknowledgement generations and rule-budget selection, prepared-pool demand/targets, RAM occupancy/scratch age, bounded retention forecasts, and separate interactive/background latency and error-budget observations when those runtime components are available.
- One acceptance row and release-checklist entry for every canonical requirement, including owner decisions, required platforms, revision, physical artifact hash and remaining remediation. Missing catalog rows, stale revisions, missing files, wrong hashes, missing platform evidence or a later failed artifact prevent an all-verified result.

The view returns at most 200 jobs and 200 events per request and reports truncation. Select any older job directly by `job_id`; use `after_event_id=0` to read the oldest event page and pass `window.next_event_cursor` for the next page. Omitting the cursor shows the latest event window. Oracle entries have their own delivery-generation cursor in the protected Oracle API.

The equivalent loopback routes are authenticated with the existing controller bearer token:

```text
GET /operator
GET /operator/workflow/<workflow_id>
GET /operator/job/<job_id>?after_event_id=0
```

`docs/requirements_4.2.7.json` contains the canonical requirement catalog; `docs/acceptance_4.2.7.json` is its ordered evidence ledger. Append newer results after older results so a later failure cannot be hidden by an earlier pass. The catalog's `specification_sha256` must match the physical canonical bytes. An accepted artifact requires `id`, `artifact`, `sha256`, `platform`, `revision`, `specification_sha256` (on the record or its evidence-ledger header) and `status: "passed"`; every declared `required_platforms` value must have current passing evidence. A wording change cannot inherit earlier acceptance merely because requirement IDs or revision labels stayed the same. Source-package assets are included in wheels. Evidence artifacts absent from a deployment remain unverified, even when their ledger entries were packaged.

Authenticated HTTP latency is recorded asynchronously as bounded operation names, durations, success flags and random evidence IDs. Prompts, query text, workflow identifiers, returned artifacts and tokens are excluded. Dropped, pending or failed metric writes are reported, so an incomplete distribution cannot count as a measured pass.

Use `pipeline_provider_preflight_submit` only when intentionally requesting one bounded real subscription task. It submits a job to the same Chapter 06 board, accepts no forced provider/model override and never periodically schedules itself. Normal quota/auth fallback remains active within the captured maximum of three dispatches, a five-minute routing deadline and a one-minute inference deadline; stricter configured limits still apply. Poll its returned workflow with `pipeline_status`; only the actual native receipt establishes which provider and model ran. A single successful preflight does not attest every installed provider or every model tier.

Windows identity/ACL/ImDisk evidence, actual subscription receipts and launch-specific acceptance must be collected on the deployed Windows host. Linux controller and HTTP tests do not establish those claims.

The production controller compares the active RAG canonical SRS and owner addendum against the exact installed captures, including physical hashes, authority kinds and amendment revisions. Checks run at startup, whenever the index generation changes and at least every 30 seconds. `knowledge_status` and pipeline health expose `authority_matches_capture`; missing or mismatched authority pauses admission with a concrete prerequisite. Remediation uses a fresh versioned corpus; this check never rewrites protected historical source pins.
