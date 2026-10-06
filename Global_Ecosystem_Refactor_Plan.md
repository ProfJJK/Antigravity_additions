# Global Ecosystem Refactor Plan

Currently, the CoChem swarm is burdened by duplicated agents (e.g., `g-coder` vs `cochem-coder`) and excessively long, universally applied global rules. This leads to **Rule Saturation**: the LLMs have so many concurrent priorities and "MUST DOs" spanning both generic software engineering and highly specific chemistry tasks that they drop critical instructions, leading to lazy compliance and hallucinations.

### Phase 1: Trimming Global Rules
The global rules (`cochem-anti-spoofing-v4.md` and `user_global.md`) will be aggressively condensed down to a core set of 5 universal invariants:
1. **Zero-Mock Execution:** Banning stubs and tautological tests.
2. **Asymmetric Verification:** External validation mandates.
3. **WBS Fracture Mandate:** Chunk-scoped editing strictly enforced.
4. **Hard Abort Criteria:** Meta-pivot ceilings before auto-termination.
5. **Source of Truth:** Reliance on SQLite databases over JSON or memory.

All legacy pipeline specifics or domain-specific logic will be moved into the WikiRAG.

### Phase 2: Sunsetting CoChem Agents
We will retire specialized CoChem/CoChem agents that duplicate standard software engineering tasks:
- `cochem-file-audit` -> merged into `g-audit`
- `cochem-draco-audit` -> merged into `g-tester`
- `cochem-improver-suite` -> merged into `g-improve`
- `beta-tester` -> deprecated.

This reduces swarm bloat and ensures the AI models only receive domain-agnostic best practices when working on the pipeline architecture.

### Phase 3: WikiRAG Dynamic Injection
For CoChem-specific chemistry tasks, we will establish a dynamic rule injection protocol (similar to the Method Matrix):
- When a `g-agent` takes on a CoChem task, it will natively query the SQLite WikiRAG database.
- It will inject only the necessary, granular guidelines (e.g., "Two-Pass Blueprint Protocol", specific ORCA/CREST standards) directly into the workflow context.
- This keeps the global prompts clean and allows domain rules to be highly specialized without polluting generic engineering tasks.

**Next Step:** Please review this refactor plan. Once approved, we will begin the surgical removal of CoKIM agents and the truncation of the Global Rules files.
