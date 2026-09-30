# Implementation Context Dossier: HDF5 Filter Pipeline Assembly & Introspection (Task 20.10812)

**Role**: P2 Researcher  
**Target Modules**: [`src/cochem/storage/hdf5_filters.py`](file:///D:/__CoChem/__agentic/src/cochem/storage/hdf5_filters.py), [`src/cochem/storage/__init__.py`](file:///D:/__CoChem/__agentic/src/cochem/storage/__init__.py)  
**Authoritative Test Suite**: [`tests/tdd/test_task_20_10812.py`](file:///D:/__CoChem/__agentic/tests/tdd/test_task_20_10812.py)

---

## 1. Architectural Mission & Operational Scope

The goal of Task 20.10812 is to assemble and introspect an immutable tripartite HDF5 dataset filter pipeline for quantum chemistry trajectory arrays (forces, coordinates, energies, Hessians):
1. **Byte-Shuffle Filter (Filter ID 2)**: Reorganizes byte layouts of IEEE 754 float64 elements by contiguous byte-offset groupings (grouping sign/exponent and mantissa bytes), drastically lowering Shannon entropy prior to compression.
2. **Lossless Zstandard Compression (Filter ID 32015)**: Leverages `hdf5plugin.Zstd` with a tunable compression level $clevel \in [1, 22]$ (default: 3).
3. **Fletcher32 Bit-Rot Checksum (Filter ID 3)**: Computes a 32-bit checksum per chunk, appended to chunk footers for physical data integrity validation.

The module provides both dataset creation argument synthesis (`to_dataset_kwargs`, `build_filter_pipeline`) and native C-driver Dataset Creation Property List (DCPL) query introspection (`inspect_dataset_filter_pipeline`).

### Scope Boundaries
* **In Scope**:
  * Immutable frozen dataclass `HDF5ZstdConfig` with strict type and range validations.
  * Kwargs generator `build_filter_pipeline` compatible with `h5py.Group.create_dataset`.
  * Introspection helper `inspect_dataset_filter_pipeline` querying native HDF5 DCPL handles.
  * Package-level re-exports in [`src/cochem/storage/__init__.py`](file:///D:/__CoChem/__agentic/src/cochem/storage/__init__.py).
* **Explicitly Out of Scope**:
  * Task 20.10811: Driver-level file-locking bypass environment configuration (`HDF5_U
<truncated 6769 bytes>
ters)` and check `filter_id == FILTER_ZSTD_ID`. |
| **AST Linter Disallowing `pass`** | The AST verification test flags any `ast.Pass` node anywhere in `hdf5_filters.py`. | Never use `pass` statements, even in exception handlers or no-op blocks. |
| **Dual Package Root Conflict (`./cochem` vs `./src/cochem`)** | Root directory contains a shallow `./cochem` alongside `./src/cochem`. Running pytest without `src` in `sys.path` causes `ModuleNotFoundError: No module named 'cochem.storage'`. | Ensure execution includes `src` on `sys.path` (configured in [`tests/tdd/test_task_20_10812.py`](file:///D:/__CoChem/__agentic/tests/tdd/test_task_20_10812.py#L19-L22)). |

---

## 6. Acceptance & Verification Playbook

### Current Test Suite Status
Running `pytest tests/tdd/test_task_20_10812.py -v`:
* **11 Passed**: T1 (immutability/validation), T2 (kwargs synthesis), T3 (default pipeline creation/introspection), T4 (disabled filters), T5 (dynamic clevel propagation across 1, 7, 19, 22), T6 (uncompressed dataset handling), T7 (AST anti-spoofing and physical roundtrip).
* **1 Failed**: `test_storage_package_reexports_filter_api` (T8 / AC8) due to missing re-exports in [`src/cochem/storage/__init__.py`](file:///D:/__CoChem/__agentic/src/cochem/storage/__init__.py).

### Implementation Checklist for Engineer
1. **Update [`src/cochem/storage/__init__.py`](file:///D:/__CoChem/__agentic/src/cochem/storage/__init__.py)**:
   * Import `HDF5ZstdConfig`, `build_filter_pipeline`, `inspect_dataset_filter_pipeline`, `is_zstd_filter_available`, `FILTER_SHUFFLE_ID`, `FILTER_FLETCHER32_ID`, `FILTER_ZSTD_ID`, `DEFAULT_ZSTD_CLEVEL`, `MIN_ZSTD_CLEVEL`, `MAX_ZSTD_CLEVEL` from `cochem.storage.hdf5_filters`.
   * Ensure `HDF5ZstdConfig` exported by `cochem.storage` references `hdf5_filters.HDF5ZstdConfig`.
   * Add new symbols to `__all__`.
2. **Execute Verification**:
   ```powershell
   pytest tests/tdd/test_task_20_10812.py -v
   pytest tests/storage/test_swmr_pes_store_production.py -v
   ```
   All 12 TDD test cases must pass with exit code 0.