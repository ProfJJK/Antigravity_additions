"""Isolated read-only Windows counter experiment; no production runtime state.

The candidate is an in-memory AST transform of the pinned installed telemetry
source. Only the Structure lifetime and GetPerformanceInfo binding are changed.
Each variant runs in its own ordinary fresh Python process.
"""
from __future__ import annotations

import ast
import copy
import ctypes
import functools
import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tracemalloc
import types

import psutil

SOURCE = Path(r"C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3\.venv\Lib\site-packages\cochem_pipeline\resource_telemetry.py")
SOURCE_SHA = "ad5ada81f1cc3ff683d4e43c706383594a4ab256dd1f3d62675f6a8a2f4d0e8a"
OUTPUT = Path(__file__).with_name("commit-type-cache-comparison-r3-20261009.json")


def load_variant(variant):
    raw = SOURCE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA
    parsed = ast.parse(raw.decode("utf-8-sig"), filename=str(SOURCE))
    selected = {"_finite", "_error", "_commit"}
    nodes = [copy.deepcopy(node) for node in parsed.body if isinstance(node, ast.FunctionDef) and node.name in selected]
    assert {node.name for node in nodes} == selected
    commit = next(node for node in nodes if node.name == "_commit")
    if variant == "hoisted_type_cached_binding":
        structures = [node for node in commit.body if isinstance(node, ast.ClassDef)]
        assert len(structures) == 1 and structures[0].name == "PerformanceInformation"
        structure = structures[0]
        commit.body.remove(structure)
        native_try = next(node for node in commit.body if isinstance(node, ast.Try))
        assert len(native_try.body) == 11
        binding_statements = native_try.body[:4]
        expected = ast.parse("library = ctypes.WinDLL('psapi', use_last_error=True)\nprobe = library.GetPerformanceInfo\nprobe.argtypes = [ctypes.POINTER(PerformanceInformation), ctypes.c_uint32]\nprobe.restype = ctypes.c_int").body
        assert [ast.dump(node) for node in binding_statements] == [ast.dump(node) for node in expected]
        loader = ast.FunctionDef(name="_get_commit_probe", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=binding_statements + [ast.Return(value=ast.Name(id="probe", ctx=ast.Load()))], decorator_list=[ast.Call(func=ast.Attribute(value=ast.Name(id="functools", ctx=ast.Load()), attr="lru_cache", ctx=ast.Load()), args=[], keywords=[ast.keyword(arg="maxsize", value=ast.Constant(value=1))])])
        native_try.body[:4] = ast.parse("probe = _get_commit_probe()").body
        nodes = [structure, loader, *nodes]
    else:
        assert variant == "installed_dynamic_type"
    module = types.ModuleType("isolated_commit_" + variant)
    module.__dict__.update(ctypes=ctypes, functools=functools, math=__import__("math"), os=os, psutil=psutil, Any=object, _MIB=1048576, _PROBE_ERRORS=(OSError, psutil.Error, ValueError, NotImplementedError))
    tree = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(tree, "<inert-pinned-commit-variant>", "exec"), module.__dict__)
    return module


def snapshot(calls):
    gc.collect()
    memory = psutil.Process().memory_info()
    current, peak = tracemalloc.get_traced_memory()
    return {"calls": calls, "pointer_cache_entries": len(ctypes._pointer_type_cache), "performance_type_entries": sum(getattr(key, "__name__", "") == "PerformanceInformation" for key in ctypes._pointer_type_cache), "python_traced_current_bytes": current, "python_traced_peak_bytes": peak, "rss_bytes": memory.rss, "private_bytes": memory.private}


def child(variant):
    assert os.name == "nt"
    module = load_variant(variant)
    tracemalloc.start()
    samples = [snapshot(0)]
    schema = None
    for batch in range(3):
        for _ in range(1000):
            result = module._commit()
            assert result["available"] is True and result["process_memory_available"] is True
            assert result["error"] is None and result["process_memory_error"] is None
            assert 0 <= result["total_mb"] <= result["limit_mb"]
            assert abs(result["free_mb"] + result["total_mb"] - result["limit_mb"]) < 1e-8
            keys = sorted(result)
            schema = keys if schema is None else schema
            assert keys == schema
        del result
        samples.append(snapshot((batch + 1) * 1000))
    print(json.dumps({"variant": variant, "schema_keys": schema, "samples": samples, "native_counter_reads": 3000, "production_runtime_started": False, "production_state_opened": False}))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        child(sys.argv[2])
        return
    assert not OUTPUT.exists()
    results = []
    for variant in ("installed_dynamic_type", "hoisted_type_cached_binding"):
        completed = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "--child", variant], capture_output=True, text=True, timeout=45, check=True)
        assert completed.stderr == ""
        results.append(json.loads(completed.stdout))
    assert results[0]["schema_keys"] == results[1]["schema_keys"]
    report = {"schema": "cochem-isolated-commit-type-cache-comparison/1", "source_sha256": SOURCE_SHA, "python_version": sys.version, "scope": "Two fresh ordinary isolated processes, read-only native OS counters; no runtime/config/database/credentials/provider/task operations.", "results": results, "policy_limits_changed": False}
    with OUTPUT.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"status": "CAUSAL_COMPARISON_RECORDED", "report_path": str(OUTPUT), "report_sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
