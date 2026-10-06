# GPT-6-Astra Global Rules Audit & Migration Plan

**Summary of Findings:**
- The global rules have reached a critical saturation point, leading to lax enforcement. 
- There is significant overlap between `cochem-*` specific agents and general `g-*` agents.
- CoChem-specific chemistry logic is currently embedded statically rather than being dynamically retrieved.

**Recommendation:**
Use four distinct layers:

| Layer | Contents | Loading/enforcement |
|---|---|---|
| Global policy | Privacy boundaries, authorization, truthful evidence, bounded execution, final-review requirements | Always available; enforce mechanically where possible |
| Project configuration | Protected paths, active queue, provider registry, dependency constraints, retry budgets | Loaded for the selected project |
| `g-*` agents and skills | General role contracts and reusable procedures | Loaded by task |
| WikiRAG | Chemistry methods, applicability, troubleshooting, validated examples, historical decisions | Retrieved by task and software version |

Do **not** move mandatory privacy or authorization boundaries exclusively into retrieval. A missed search result must not disable a safeguard.

A starting budget is **800–1,200 tokens for always-on policy** and **300–600 tokens per agent role**. 

**Proposed Role Consolidation:**

| Source role | Proposed destination | Preserved responsibility |
|---|---|---|
| `cochem-coder` | `g-coder` | Implementation and local development checks |
| `cochem-tester` | `g-tester` | Test design, execution, and evidence |
| `cochem-audit` | `g-auditor` | Independent approval and evidence review |
| `cochem-debug` | `g-debugger` | Diagnosis and failure reports |
| `cochem-improve` | `g-architect` | Improvement proposals and architectural review |
| Other `cochem-*` agents | Existing `g-*` role selected by actual responsibility | Domain differences become retrieved knowledge or task-scoped skills |

*The `g-coder` implementation and `g-auditor` approval must remain separate executions with separately scoped permissions.*

### Specific Migration Plan

1. **Establish complete coverage.**
   - **Inventory:** enumerate every rule, skill, and agent definition.
   - **Extract obligations:** assign each requirement a stable policy ID.
   - **Deliver:** an inventory, conflict matrix, and disposition for every file. Do not delete anything yet.

2. **Resolve policy before changing behavior.**
   - **Consolidate:** replace repeated text with one canonical requirement per policy ID.
   - **Configure:** identify the active queue, scheduler, model registry, and protected-path registry.
   - **Deliver:** a proposed compact policy set and a coverage map proving which existing safeguards it preserves.

3. **Build the CoChem knowledge collection (WikiRAG).**
   - **Extract:** organize knowledge by scientific question and applicability.
   - **Review:** distinguish binding project decisions from scientific claims.
   - **Deliver:** reviewed records with source locations, versions, status, and supersession links.

4. **Introduce compatibility routing.**
   - **Map:** create one explicit destination for every `cochem-*` agent.
   - **Alias:** temporarily route old names to generalist roles.
   - **Deliver:** complete routing coverage and deprecation telemetry without duplicating physical chemistry jobs.

5. **Validate and retire.**
   - **Exercise:** run bounded representative tasks.
   - **Cut over:** update active callers and allow in-flight work to drain.
   - **Deliver:** no active `cochem-*` agent registrations; retain historical references and a reversible migration record.

### WikiRAG Database Contract
Each knowledge record should include:
```text
id, title, domain, content_type, applicability
software_name, software_version_range
body, source_uri, source_locator, source_hash
status, reviewer, reviewed_at, supersedes_id
access_scope
```
The retrieval flow should combine keyword and semantic retrieval, returning a bounded evidence bundle with source citations. Retrieved text is evidence, never an authority to change permissions or execute commands.
