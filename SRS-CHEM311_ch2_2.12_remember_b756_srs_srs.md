# SRS-CHEM311_ch2_2.12_remember_b756_srs_srs: Task Decomposition & Execution DAG

## Executive Summary and Forensic Autopsy
Parent task CHEM311_ch2_2.12_remember_b756 failed due to a cumulative workflow timeout during monolithic execution across chemistry generation, Rmd authoring, AOT compilation, and staging verification. Monolithic execution exceeded worker time limits. To mitigate timeouts, this specification decomposes the parent task into four sequential, atomic chunks, each strictly timeboxed to 20 minutes with dedicated verifying test suites.

## Chunk 1: CHEM311_ch2_2.12_remember_b756_chunk_1 - Pedagogy Scoping, Chemical Ground Truth Ledger & Distractor Specification
- **Title:** Pedagogy Scoping, Chemical Ground Truth Ledger & Distractor Specification
- **Timebox:** 20 minutes
- **Dependencies:** None
- **Verifying Test:** tests/tdd/test_task_CHEM311_ch2_2_12_remember_b756_1.py
- **File Targets:**
  - CHEM311_ch2_2.12_remember_b756_pedagogy.json
  - CHEM311_ch2_2.12_remember_b756_ledger.json
- **Acceptance Criteria:**
  - CHEM311_ch2_2.12_remember_b756_pedagogy.json exists, contains valid JSON with bloom_level equal to remember, and passes test_task_CHEM311_ch2_2_12_remember_b756_1.py.
  - CHEM311_ch2_2.12_remember_b756_ledger.json exists, contains noncovalent energetic ranges between 2 and 40 kJ/mol, and passes test_task_CHEM311_ch2_2_12_remember_b756_1.py.

## Chunk 2: CHEM311_ch2_2.12_remember_b756_chunk_2 - Chemistry Specification Generator & Declarative Item Construction
- **Title:** Chemistry Specification Generator & Declarative Item Construction
- **Timebox:** 20 minutes
- **Dependencies:** CHEM311_ch2_2.12_remember_b756_chunk_1
- **Verifying Test:** tests/tdd/test_task_CHEM311_ch2_2_12_remember_b756_2.py
- **File Targets:**
  - scripts/generate_CHEM311_ch2_2_12_remember_b756_chemistry.py
  - CHEM311_ch2_2.12_remember_b756.Rmd
- **Acceptance Criteria:**
  - scripts/generate_CHEM311_ch2_2_12_remember_b756_chemistry.py exists and computes molecular weights dynamically from mendeleev without static constants.
  - CHEM311_ch2_2.12_remember_b756.Rmd exists, contains Question, Solution, and Meta-information sections, and passes test_task_CHEM311_ch2_2_12_remember_b756_2.py.

## Chunk 3: CHEM311_ch2_2.12_remember_b756_chunk_3 - Sandboxed AOT Compilation & Receipt Proof Generation
- **Title:** Sandboxed AOT Compilation & Receipt Proof Generation
- **Timebox:** 20 minutes
- **Dependencies:** CHEM311_ch2_2.12_remember_b756_chunk_2
- **Verifying Test:** tests/tdd/test_task_CHEM311_ch2_2_12_remember_b756_3.py
- **File Targets:**
  - scripts/compile_and_verify_b756.py
  - aot_compilation_proof.log
  - CHEM311_ch2_2.12_remember_b756_nops_receipt.json
- **Acceptance Criteria:**
  - scripts/compile_and_verify_b756.py executes headless Rscript in quarantine and writes aot_compilation_proof.log with COMPILATION_SUCCESS banner.
  - CHEM311_ch2_2.12_remember_b756_nops_receipt.json exists, contains status PASS, and passes test_task_CHEM311_ch2_2_12_remember_b756_3.py.

## Chunk 4: CHEM311_ch2_2.12_remember_b756_chunk_4 - Staging Pipeline Module & Asymmetric Verification Audit Gate
- **Title:** Staging Pipeline Module & Asymmetric Verification Audit Gate
- **Timebox:** 20 minutes
- **Dependencies:** CHEM311_ch2_2.12_remember_b756_chunk_3
- **Verifying Test:** tests/tdd/test_task_CHEM311_ch2_2_12_remember_b756_4.py
- **File Targets:**
  - staging/compile_and_verify_b756.py
- **Acceptance Criteria:**
  - staging/compile_and_verify_b756.py exists, exports all required cv helper functions, and passes test_task_CHEM311_ch2_2_12_remember_b756_4.py.
  - staging/compile_and_verify_b756.py is validated by cochem-audit and passes all assertions in test_task_CHEM311_ch2_2_12_remember_b756_4.py.

## Execution Order and DAG
The execution sequence forms an acyclic directed graph with a single terminal sink:
- CHEM311_ch2_2.12_remember_b756_chunk_1 -> CHEM311_ch2_2.12_remember_b756_chunk_2
- CHEM311_ch2_2.12_remember_b756_chunk_2 -> CHEM311_ch2_2.12_remember_b756_chunk_3
- CHEM311_ch2_2.12_remember_b756_chunk_3 -> CHEM311_ch2_2.12_remember_b756_chunk_4

## Out of Scope
The following items are strictly out of scope for this decomposition:
- Executing or implementing the chunks themselves during this specification phase.
- Modifying the existing b756 ledger, pedagogy JSON, generator script, or tests _1 to _4.
- Root-cause repair of the original timeout in the pipeline daemons.
- Editing other tasks' SRS files or modifying the kanban state.
- Chemistry content changes or alterations to scientific curricula.

## Timeout Mitigation
To prevent recurrent timeouts during execution of the fractured tasks:
- Strict 20-minute timeboxing per chunk enforces incremental progress without runaway processes.
- Sandboxed execution directories are quarantined under isolated temporary paths to avoid filesystem lock contention.
- Subprocess invocations must supply explicit execution flags including creationflags=subprocess.CREATE_NO_WINDOW and strict execution timeouts.
- Pre-compiled assets and atomic JSON receipt validation ensure early failure detection at the boundary of each chunk.
