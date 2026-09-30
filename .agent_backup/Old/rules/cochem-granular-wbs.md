# Deep Granularity & Work Breakdown Structure (WBS) Mandate

You must strictly enforce the following execution invariants across the CoChem swarm to limit context scope and prevent execution drift:

1. **Mandatory Deep Breakdown**: For ANY significant task or complex goal, you are STRICTLY FORBIDDEN from executing the task in one massive attempt. You MUST first generate a deeply nested Work Breakdown Structure (WBS).
2. **3-Tier Minimum Depth**: Your plan must be decomposed into at least three levels of granularity:
   - **Task**: The high-level objective (e.g., "Audit the CoChem-GEOM module").
   - **Sub-task**: The specific component or phase (e.g., "Write pytest fixtures for convergence logic").
   - **Sub-sub-task**: The singular, atomic unit of work to be executed (e.g., "Write test for TolMaxG 1e-5 on water dimer").
3. **Atomic Execution**: Agents (especially execution agents like `cochem-coder` or `cochem-tester`) must only be assigned to, and only execute, a single **sub-sub-task** at a time.
4. **Context Limiting**: By executing only at the sub-sub-task level, agents keep their context windows extremely limited, focused entirely on the immediate physical files and isolated logic.
5. **No Monolithic Sweeps**: Never allow an agent to "audit the whole directory" or "fix all errors." 
