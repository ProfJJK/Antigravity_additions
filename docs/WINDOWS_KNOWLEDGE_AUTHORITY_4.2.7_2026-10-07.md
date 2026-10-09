# Windows knowledge authority correction — 2026-10-07

The repository catalog keeps `.sources/4.2.7_SRS.md` and
`.sources/SRS_ADDENDUM_4.2.7.md` as immutable historical sources. Its current
canonical captures are the corresponding `*.model-routing-2026-10-07.md`
files. The former runtime authority check read only the historical filenames,
so this valid current corpus could not satisfy production admission.

`KnowledgeService.read_authority_source` now selects exactly one `.sources/`
catalog entry with the captured SHA-256, required authority, and exact revision.
The bounded query returns at most two rows so ambiguity is rejected. It reads
the selected path through the ordinary protected source reader, verifies its
physical bytes and metadata, and rejects a changed index generation or manifest
binding. `KnowledgeAuthority` also checks the generation across the complete
SRS/amendment verification and reports the actual resolved paths. Current
captures at the old filenames remain compatible. Wiki summaries, missing or
ambiguous matches, stale source bytes, wrong authority, and wrong revisions
remain admission holds. Historical sources, source pins, and the catalog are
not rewritten. The existing generation-triggered and 30-second recheck policy
remains in force.

## Actual Windows test evidence

- [Focused follow-up result](evidence/windows-2026-10-06/knowledge-authority-source-resolution-followup-tests.xml):
  **20 passed in 2.38 seconds**, Windows Python 3.12.13.
- The suite copies the actual repository catalog and archive bytes into a
  disposable fixture, exercises real SQLite/FTS5 generations, checks immutable
  history, tests source tampering and publication races, and verifies a real
  queued workflow remains unclaimed when authority is unresolved.
- Filesystem permission and Oracle tracking protection are explicit portable
  fixture boundaries. These results do **not** attest SYSTEM execution,
  installed corpus ACLs, or native model inference. Those Windows deployment
  checks remain separate from these tests and earlier Linux results.
- [Initial diagnostic run](evidence/windows-2026-10-06/knowledge-authority-source-resolution-tests.xml)
  is retained: 18 passed and two fixture failures. One copied a linked canonical
  document into a wiki fixture without its link targets; the other reached the
  real SYSTEM-only Oracle guard from a non-SYSTEM process. Both fixture
  boundaries were corrected explicitly; no production protection was removed.
