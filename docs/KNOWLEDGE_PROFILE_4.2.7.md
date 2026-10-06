# Knowledge MCP profile — 4.2.7

The owner accepts the historical **7.731 ms authenticated MCP mean** and **4.100 ms backend mean**. Under [the canonical SRS](../4.2.7_SRS.md), 5 ms remains an optimization target, not a release-denial condition. That acceptance is an observed operating range, not a guarantee for every request or an exemption from authentication, corpus integrity, index size or memory limits.

The measured gap includes SDK schema processing, asynchronous dispatch and HTTP work. Bearer comparison is a small part of it. The optimization keeps authentication and validation: the supported MCP SDK `Annotated[CallToolResult, dict[str, Any]]` result path preserves the published output schemas and Pydantic structured-output validation, while avoiding a second server-side JSON-schema check and redundant pretty-print conversion. Client JSON-schema validation still executes. Both structured and text representations contain the same result; scientific Unicode remains literal. Every request still rereads the token, authenticates, verifies the protected generation and runs its physical FTS5 query. There is no query-result cache.

## Physical comparison

Three fresh generated corpora each contain **101 documents and 10,001 sections**. Each run interleaves 300 previous/current named MCP searches against the same immutable index and persistent authenticated HTTP client. Pair order alternates for every query. This yields **900 requests per implementation** with identical structured results. The previous implementation is the exact `server.py` from tag `v4.2.6`; its SHA-256 is recorded in [the machine-readable evidence](KNOWLEDGE_PROFILE_4.2.7.json), together with current source hashes, package versions, all end-to-end samples and report hashes.

| End-to-end MCP metric | Previous implementation | 4.2.7 implementation |
| --- | ---: | ---: |
| Mean | 6.997 ms | 6.550 ms |
| Median | 4.637 ms | 3.893 ms |
| p95 | 14.612 ms | 13.947 ms |
| Maximum | 41.103 ms | 143.367 ms |

The observed mean reduction is **0.447 ms (6.39%)**. The maximum did not improve: the 143.367 ms sample spent 123.879 ms in client schema validation, with a 1.688 ms backend query. This profile does not establish a worst-case guarantee or a Windows result. An earlier sequential comparison under changing host load measured 8.480→9.425 ms and did not establish an end-to-end improvement; the recorded interleaved design reduces that ordering confound without claiming to eliminate host variability.

These are the additive mean components from the same paired MCP requests:

| Component | Previous | 4.2.7 |
| --- | ---: | ---: |
| Protected generation + backend query/result shaping | 3.624 ms | 3.706 ms |
| Token reread | 0.090 ms | 0.077 ms |
| Constant-time bearer comparison | 0.002 ms | 0.002 ms |
| HTTP reply work within client interval | 0.147 ms | 0.174 ms |
| Other HTTP parsing, socket and scheduling work | 0.585 ms | 0.586 ms |
| SDK JSON-schema validation | 1.336 ms | 0.857 ms |
| Other MCP protocol, conversion and asynchronous dispatch | 1.213 ms | 1.147 ms |

Backend measurements further separate protected-generation verification (**0.167→0.187 ms**), actual SQLite execution (**3.313→3.379 ms**), row fetching (**0.061→0.060 ms**) and remaining pool/query-validation/result-shaping work (**0.082→0.080 ms**). Those are nested inside the backend row and must not be added to it again. The direct HTTP and backend distributions are retained separately in the evidence.

The profiling wrappers call the actual production functions and retain their values, I/O and validation. They add measurement overhead. Because a server socket write can return after the client has received its bytes, the harness finishes attribution before starting the next query and separates reply overlap within the client interval from post-client completion. The latter is outside end-to-end latency; it is not incorrectly subtracted as an exclusive HTTP cost.

Each generated corpus has **4,142,117 source bytes** and an **8,183,808-byte index (1.976×)**. The highest sampled combined-process RSS across the runs was **71,897,088 bytes**; that process contained the indexer, controller and both MCP sessions. These observations meet the benchmark's index-size and memory limits. They do not certify long-running native daemon memory.

## Reproduce and validate

Run from the repository with the locked environment. The comparison option executes the supplied Python source: use a reviewed version of this repository's server, as below.

```bash
git show v4.2.6:src/cochem_pipeline/server.py > /tmp/cochem-server-4.2.6.py
python scripts/benchmark_knowledge.py --iterations 300 \
  --comparison-server-file /tmp/cochem-server-4.2.6.py \
  --output /tmp/knowledge-profile.json
python -m pytest -q pipeline_tests/test_knowledge.py
```

Repeat the physical benchmark in fresh directories/processes to assess variability. Without a comparison file it measures the installed backend, authenticated HTTP and named MCP path. The normal exit status enforces index-size and memory requirements; `--require-latency-targets` additionally makes the optional 5 ms targets affect exit status. Raw paired component samples are written to the output JSON; terminal output omits those large arrays.

Focused validation: **28 tests passed**. The actual HTTP/MCP path checks unchanged output schemas, equality of text/structured values, scientific UTF-8, invalid-token rejection and recovery after token restoration, query bounds, corpus integrity and index rebuild behavior. The benchmark also rejects any difference between old/new structured query results.

## Remaining acceptance and remediation

The 5 ms end-to-end mean target remains unmet in this run, with owner-accepted performance recorded separately from target attainment. The Linux benchmark uses real HTTP and the in-process MCP session transport; it excludes stdio framing, the Antigravity GUI and the deployed Windows filesystem/security environment. First-query measurements do not evict operating-system caches populated during index construction. The generated corpus establishes scale and byte handling, not representative production retrieval relevance.

On Windows, repeat with the actual protected corpus, GUI/stdio connection, installed accounts and representative concurrent jobs. Retain full latency distributions, corpus/index hashes and memory evidence. If further optimization is needed, investigate reuse of already-compiled schema validators in the actual MCP client/SDK and its scheduling outliers before changing database or authentication boundaries. Such a change must continue validating every result and must be benchmarked independently; it is not implemented or promised by this release.
