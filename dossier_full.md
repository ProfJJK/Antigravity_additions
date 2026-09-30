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
  * Task 20.10811: Driver-level file-locking bypass environment configuration (`HDF5_USE_FILE_LOCKING=FALSE`).
  * Task 20.10813: Multi-tensor dynamic chunk geometry derivation and memory sizing (64 kB – 256 kB).
  * Task 20.10814: Bit-exact lossless roundtrip preservation and raw byte corruption failure trapping.
  * Task 20.10815: Full ab-initio potential energy surface test fixture generation and cross-silo deployment.

---

## 2. Existing Code & Patterns to Reuse

* **Core Implementation**: [`src/cochem/storage/hdf5_filters.py`](file:///D:/__CoChem/__agentic/src/cochem/storage/hdf5_filters.py) already contains the complete logic for the tripartite pipeline, `HDF5ZstdConfig`, `build_filter_pipeline`, and `inspect_dataset_filter_pipeline`.
* **Plugin Registration**: Importing `hdf5plugin` dynamically registers filter ID `32015` with the active `libhdf5` C-driver. Calling `hdf5plugin.Zstd(clevel=clevel)` produces a dictionary with keys `"compression": 32015` and `"compression_opts": (clevel,)`.
* **Low-Level DCPL Extraction Pattern**:
  * Extract the native dataset identifier: `dataset_id = getattr(dataset, "id", dataset)`.
  * Acquire the DCPL: `dcpl = dataset_id.get_create_plist()`.
  * Inspect registered filters: iterate `index` from `0` to `dcpl.get_nfilters() - 1` using `dcpl.get_filter(index)`, which yields `(filter_id, flags, cd_values, name)`.
  * Zstandard's effective compression level resides at `cd_values[0]` of filter `32015`.

---

## 3. Authoritative APIs, Signatures & Schemas

### 3.1 Constants & Registry Values
```python
FILTER_SHUFFLE_ID: int = 2          # H5Z_FILTER_SHUFFLE
FILTER_FLETCHER32_ID: int = 3        # H5Z_FILTER_FLETCHER32
FILTER_ZSTD_ID: int = 32015         # H5Z_FILTER_ZSTD (hdf5plugin.ZSTD_ID)
DEFAULT_ZSTD_CLEVEL: int = 3
MIN_ZSTD_CLEVEL: int = 1
MAX_ZSTD_CLEVEL: int = 22
```

### 3.2 `is_zstd_filter_available() -> bool`
Queries `h5py.h5z.filter_avail(FILTER_ZSTD_ID)` to confirm dynamic C-driver registration.

### 3.3 `HDF5ZstdConfig` (Frozen Dataclass)
```python
@dataclass(frozen=True)
class HDF5ZstdConfig:
    clevel: int = DEFAULT_ZSTD_CLEVEL
    shuffle: bool = True
    fletcher32: bool = True

    def __post_init__(self) -> None: ...
    def to_dataset_kwargs(self) -> Dict[str, Any]: ...
```
* **Validation Rules**:
  * `clevel`: Must reject `bool` (even though `isinstance(True, int)`), `float` (including `3.0`), `str`, and `None` with `ValueError`. Must enforce `1 <= int(clevel) <= 22`. Normalized internally via `object.__setattr__(self, "clevel", int(level))`.
  * `shuffle`: Must reject non-bools with `ValueError`.
  * `fletcher32`: Must reject non-bools with `ValueError`.
* **Kwargs Output**:
  ```python
  {
      "compression": 32015,
      "compression_opts": (clevel,),
      # "shuffle": True (only if self.shuffle is True)
      # "fletcher32": True (only if self.fletcher32 is True)
  }
  ```

### 3.4 `build_filter_pipeline(config: Optional[HDF5ZstdConfig] = None) -> Dict[str, Any]`
* If `config is None`, falls back to `HDF5ZstdConfig()`.
* Raises `TypeError` if `config` is not an instance of `HDF5ZstdConfig` or `None`.
* Invokes `config.to_dataset_kwargs()`.

### 3.5 `inspect_dataset_filter_pipeline(dataset: Any) -> Dict[str, Any]`
Accepts `h5py.Dataset` or raw `h5py.h5d.DatasetID`. Raises `TypeError` if DCPL cannot be retrieved.

**Return Schema**:
```python
{
    "nfilters": int,                  # Total number of registered filters on DCPL
    "filter_ids": List[int],          # Registered filter IDs in DCPL index order
    "has_shuffle": bool,              # 2 in filter_ids
    "has_fletcher32": bool,           # 3 in filter_ids
    "has_zstd": bool,                 # 32015 in filter_ids
    "clevel": Optional[int],          # cd_values[0] for ZSTD_ID, or None
}
```

### 3.6 Re-Exports in [`src/cochem/storage/__init__.py`](file:///D:/__CoChem/__agentic/src/cochem/storage/__init__.py)
Package level must re-export:
* `HDF5ZstdConfig` (bound to `hdf5_filters.HDF5ZstdConfig`)
* `build_filter_pipeline`
* `inspect_dataset_filter_pipeline`
* `FILTER_SHUFFLE_ID`, `FILTER_FLETCHER32_ID`, `FILTER_ZSTD_ID`
* `DEFAULT_ZSTD_CLEVEL`, `MIN_ZSTD_CLEVEL`, `MAX_ZSTD_CLEVEL`
* `is_zstd_filter_available`
* Update `__all__` list to declare all exported symbols.

---

## 4. Technical Constraints & Verification Invariants

1. **Zero-Mock & Anti-Spoofing Protocols**:
   * Test [`test_anti_spoofing_ast_and_real_filter_execution`](file:///D:/__CoChem/__agentic/tests/tdd/test_task_20_10812.py#L311-L404) uses Python AST parsing on `hdf5_filters.py`.
   * **STRICTLY BANNED**: `NotImplementedError`, `unittest.mock`, `MagicMock`, `pytest.monkeypatch`, `patch`, and standalone `pass` statements or `...` stubs anywhere in the module AST.
2. **Authentic Disk I/O Verification**:
   * Every dataset test writes real IEEE 754 float64 numpy arrays to temporary HDF5 files on disk (`tmp_path`), closes the handle, reopens for reading, and asserts lossless bit-exact identity (`np.testing.assert_array_equal`).
3. **Subprocess Invariants (Windows)**:
   * Subprocess executions must supply `creationflags=subprocess.CREATE_NO_WINDOW` (or `0x08000000`) to prevent console popup windows and pass `encoding='utf-8'`.
4. **File Encoding**:
   * All file operations must explicitly use `encoding="utf-8"`.

---

## 5. Engineering Pitfalls & Triage Matrix

| Pitfall | Root Cause | Preventive Measure |
| :--- | :--- | :--- |
| **`HDF5ZstdConfig` Namespace Collision in `storage`** | Legacy [`src/cochem/storage/hdf5_zstd.py`](file:///D:/__CoChem/__agentic/src/cochem/storage/hdf5_zstd.py) has an existing dataclass also named `HDF5ZstdConfig` with chunk geometry methods. | [`src/cochem/storage/__init__.py`](file:///D:/__CoChem/__agentic/src/cochem/storage/__init__.py) currently re-exports `hdf5_zstd.HDF5ZstdConfig`. Test `test_storage_package_reexports_filter_api` asserts `storage.HDF5ZstdConfig is hdf5_filters.HDF5ZstdConfig`. Change the import in `storage/__init__.py` to import `HDF5ZstdConfig` from `hdf5_filters`. |
| **Python `bool` Subtype Trap** | In Python, `isinstance(True, int)` evaluates to `True` because `bool` inherits from `int`. | Explicit check `if isinstance(level, bool): raise ValueError(...)` before verifying `numbers.Integral`. |
| **HDF5 Chunking Precondition** | HDF5 filter pipelines only operate on chunked datasets. Calling `create_dataset` without `chunks=...` creates a contiguous dataset where filters are ignored. | Uncompressed contiguous or chunked datasets must be gracefully handled by `inspect_dataset_filter_pipeline`, returning `nfilters=0`, `filter_ids=[]`, `clevel=None`. |
| **Dynamic Filter Index Assumptions** | Filter pipeline registration order may vary or omit filters when `shuffle=False` or `fletcher32=False`. | Never hardcode filter indexes (e.g. assuming index 1 is Zstd). Loop through all `range(nfilters)` and check `filter_id == FILTER_ZSTD_ID`. |
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