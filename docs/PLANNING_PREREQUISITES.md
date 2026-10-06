# Planning evidence and unresolved canonical specification

Production coding submissions require registered planning specifications. They
capture the actual Git baseline and return a durable `PLANNING_HOLD`, with no
runnable planning or implementation jobs, when those prerequisites are missing.
HTTP `/coding/submit` and MCP `pipeline_code` return that state; the workflow's
`coding.planning_hold` records the concrete reason. Registration does not assert
that any stage executed.

Runtime `/status` exposes the `planning` readiness diagnostic. Native
`execution-readiness` includes the same check and cannot report a ready coding
deployment while the execution binding is missing. Readiness distinguishes
configured source registration from physical source verification; submission
performs the latter against the captured Git bytes.
The production acceptance verifier also requires an implemented controller
binding. A completed component workflow, registered source, or supplied
`stage_execution_verified` boolean cannot bypass that prerequisite.

The missing sources are:

- The original `v2/task_planning_orchestra.py`, whose seven-stage flow must be
  preserved under `V4_CORE_MODIFICATION_AUTHORIZATION.md:49–57`. Its contents and
  stage definitions are absent from the checkout and available Git history.
- The actual Method Matrix M-1 through M-8 definitions. `v4.1.2/TEST_INFRA.md:107`
  requires their audit but does not supply their text. A filename reference or a
  historical claim that the file existed does not supply those definitions.

The source registry is implemented; the exact seven-stage transition binding is
not implemented because its defining source is missing. A registered source
therefore still produces a production hold until its execution transitions have
been implemented and verified. `stage_execution_verified: false` is explicit in
registration evidence. The existing ten-phase TDD sequence is a separate workflow
and is not counted as seven-stage planning compliance.

`CodingProject.planning` accepts this operator-owned structure:

| Field | Required content |
| --- | --- |
| `protocol` | `path`, `sha256`, and seven ordered `clauses`, each with its actual `id` and verbatim `quote`. |
| `method_matrix` | `path`, `sha256`, and eight `clauses`, identified exactly `M-1` through `M-8`, with verbatim `quote`. |
| `research_sources` | Two to eight distinct records containing `id` and an exact public HTTPS `url`. |
| `max_revisions` | Integer 1–10; defaults to five. |

Specification paths are contained relative paths in the captured Git snapshot,
including tracked hidden directories. Source bytes must match the registered
SHA256, every quotation must occur in those bytes, and stage quotations must
retain their source order. No substitute stage names or Method Matrix meanings
are supplied by the controller.

For registered plans, every Method Matrix clause must link to declared
requirements and actual generated artifacts. `MethodMatrix.json` retains the
source hash, clause quotes, links, and linked artifact hashes. Its own hash is
included in the independent planning audit's complete artifact set.

`collect_external_sources` fetches the registered URLs with system TLS trust and
the inherited policy proxy. It rejects redirects and private-address direct
destinations, bounds each response to 64 KiB and the collection to a 30-second
budget, and retains actual text, SHA256, byte count, URL, and retrieval timestamp.
The research validator checks exact quotations against those bytes, at least two
distinct source URLs, and full declared-requirement coverage. Its confidence
record measures evidence coverage; it is not a scientific probability and ignores
any confidence score supplied by a model. Network denial remains a prerequisite
failure and does not authorize bypassing the proxy or TLS verification.

Smart planning revisions operate independently of TDD. An authenticated native
planning audit may return `FAIL` or `REVISE` with artifact-bound findings. The
controller persists that outcome and creates a revised `CODE_PLAN` job carrying
the previous plan and findings. Revisions must change each cited artifact group;
unchanged resubmissions and exhausted revision budgets produce `PLANNING_HOLD`.
Only a final independent `PASS` records TDD phase P1 or permits the subsequent
research stage. All prior native outputs and receipt hashes remain in SQLite's
immutable job records and `coding.planning_history`.

The direct `JobStore` component interface remains usable without production
registration for isolated storage tests. Such tests establish transactional
behavior only; they cannot establish production readiness or native inference.
