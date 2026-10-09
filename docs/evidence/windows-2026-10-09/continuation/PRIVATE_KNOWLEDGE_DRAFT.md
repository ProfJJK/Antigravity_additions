# Private corpus continuation draft — 2026-10-07

Prepared outside the frozen repository and installed package. **No Apply, protected corpus copy, SYSTEM task, or production index acceptance has run.** The current read-only preview has zero holds. It cannot attest the SYSTEM-only index directory's absence; the SYSTEM helper explicitly checks that before corpus provisioning or index creation. Errors, existing roots/tasks/state, and partial failures require review; no automatic retry, deletion, reset, or permission broadening is provided.

The new candidate preserves all 11 public catalog entries byte for byte and appends 126 unique historical raw Markdown captures, registered with `historical_source` authority. It contains 137 documents, 138 files, four directories, and 2,156,744 payload bytes. Private provenance remains beside the candidate, outside Git. It is not silently included in the retrieval corpus.

The disposable staging index has 1,708 sections and occupies 3,747,840 bytes; integrity and size checks passed, and the current captured canonical SRS and owner amendment resolve to their dated source files. This validation used an explicitly bounded staging permission fixture, **not SYSTEM or production ACL acceptance**. The installer independently invokes the actual installed permission checks and creates a fresh production index; it does not copy the disposable database.

The preservation boundary remains explicit: 50 of the original 86 indexed records have exact captured bytes in the preserved wiki captures. Thirty-six old indexed hashes remain absent. Across current live paths, 47 match their old indexed hashes, 36 have changed, and three are missing. External Antigravity files were located but their bytes differ from the indexed originals. No FTS fragments were reconstructed as raw source files, no original database was rewritten, and complete legacy continuity is not claimed.

## Morning operation after independent review

Run the consolidated reviewed morning entrypoint when available. Its knowledge stage is equivalent to:

```powershell
& 'C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\install-private-knowledge-candidate.ps1' -Apply
```

This requires the owner in 64-bit Administrator Windows PowerShell 5.1 on AETHERDESK. Without `-Apply`, the script only returns its read-only plan and source checks. The default preview must have an empty `holds` array; exit code zero alone does not prove readiness.

The one-shot operation creates `C:\Program Files\CoChem\Knowledge4.2.7-windows-20261006` using protected SYSTEM/Admin-only ACLs **before the first private byte**. It creates a separate protected `KnowledgeAcceptance4.2.7-windows-20261006` helper root and a no-trigger SYSTEM task named `CoChem-4.2.7-PrivateKnowledge-Acceptance`. The SYSTEM helper verifies the exact installed configuration/runtime and every copied file, applies the production corpus ACLs, and builds only the new `C:\ProgramData\CoChemPipeline427\private\knowledge-windows-20261006` index. Production corpus provisioning restricts those corpus files to SYSTEM; the raw documents need not remain readable to the ordinary owner account.

It checks actual canonical authority, source pins and bytes, SQLite integrity and document count, index size, protected config stability, and disabled/terminal daemons. It never enables a daemon, runs a provider/model job, logs in, imports an old source database, or changes any repair budget/configuration. The acceptance task has a five-minute limit. The wrapper accepts its receipt only after the task is terminal with no instances, a zero last result, and matching nonce/SYSTEM/helper/inventory binding.

Success has status `PRIVATE_CORPUS_AND_NEW_INDEX_VERIFIED` in schema `cochem-private-knowledge-install-result/1`, pointing to `C:\Program Files\CoChem\KnowledgeAcceptance4.2.7-windows-20261006\knowledge-acceptance.json`. The sanitized receipt records counts, hashes, canonical source names, and safe failure phase/type/winerror, never private document titles, paths, content, or credentials. It is only knowledge readiness; authentication, remaining migration, and pipeline activation are separate gates.

## Exact review pins

| Artifact | SHA-256 |
|---|---|
| `install-private-knowledge-candidate.ps1` | `5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a` |
| `accept-private-knowledge.py` | `832eb45e9629f2bcde75eff58a455135934118bc5ab9f45c7d2870c9db0950ca` |
| `test_private_knowledge_candidate.py` | `241c85ccf2d6e7b5b3acba1250aef6bcecb48f6a68b4976f5c0f4644923249a7` |
| `private-knowledge-runtime-binding-tests.xml` | `0e99b1e37c4696be7bd83344e6ef617fa08a2667b4521f1251758adc94bf4906` |
| `private-knowledge-readonly-preview-final.json` | `73a426c9f624ebdfdb87f7fb43de1f3dfe7a367e21b3c6ee2707fc58dc4edffa` |

The final Windows check was **62 passed in 13.41 seconds**. The earlier draft receipt remains preserved with 42 passed and six fixture failures (a fixture variable was overwritten by the exact guard under test; one assertion matched a comment). Both were fixed in the fixture only. Tests exercised actual PowerShell parsing/guards, live candidate byte checks, synthetic mutation/path cases, an actual temporary hardlink rejection, permission-denied state handling, safe diagnostics, and task states/errors. No tests executed Apply or any native provider.

Candidate root: `C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\knowledge-continuation-candidate-20261007T053341Z`.

| Private candidate metadata (relative to root) | SHA-256 |
|---|---|
| `custody\private-install-inventory.json` | `edb97ec08cfdc6e451c9a875b4e9f300dc67b8e9af03b32feada9d6251240892` |
| `corpus\v4.1.2_manifest.json` | `7c16c12d319aa89b78fcd83628be7e23250259ad9b05b0c246dbd48746d44ec5` |
| `validation-evidence.json` | `4d4193a42fb21b3cb3d3c5bfb43d0e799c40e640331218e2fe69505d096e80ea` |
| `validation\knowledge-index\sources.json` | `df99c5cab0eebead545980d68e205aeed0b8649221aec03e0ec6b4ec7d9691ba` |
| `custody\historical-source-provenance.json` | `6c4ba72ddde3ba739d8654a8f60a539a02021782639c1192b5e91a5bee5b11c1` |
| `custody\input-lineage.json` | `ec15b09ba59179023a0bd19d5f9feee07e1ba6565d1fba33a767c71a1ef02938` |
| `custody\original-public-manifest.json` | `cbde9b4d9d5be3ffdb21603c4cfeabe963a9563ab59dec734b8a125a0fcb7ca6` |

The original source databases, original preservation trees, old/current manifests, gap metadata, and current canonical captures remain preserved. Staged knowledge target paths, R: lifecycle, four-slot capacity, chapter routing, and Agy integration hold remain unchanged.

Final runtime custody also checks the complete protected base Python312 tree, exact launcher/base executable hashes and pyvenv.cfg bytes/home/executable/version/site policy before SYSTEM launch. The helper checks its actual base_prefix/_base_executable, Python 3.12.13, isolated mode and disabled bytecode; these are observed runtime checks, not requested-path assertions.
