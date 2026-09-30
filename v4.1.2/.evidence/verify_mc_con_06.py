import sys
import subprocess
import importlib.util

spec = importlib.util.spec_from_file_location("cochem.concurrency.runtime", "src/cochem/concurrency/runtime.py")
rt = importlib.util.module_from_spec(spec)
sys.modules["cochem.concurrency.runtime"] = rt
spec.loader.exec_module(rt)

print("--- AUDIT CHECK 1: Constants ---")
c_no_win = getattr(rt, "CREATE_NO_WINDOW", None)
print(f"CREATE_NO_WINDOW = {hex(c_no_win) if c_no_win is not None else None}")
assert c_no_win == 0x08000000, f"Expected 0x08000000, got {c_no_win}"

c_new_pg = getattr(rt, "CREATE_NEW_PROCESS_GROUP", None)
print(f"CREATE_NEW_PROCESS_GROUP = {hex(c_new_pg) if c_new_pg is not None else None}")
assert c_new_pg == 0x00000200, f"Expected 0x00000200, got {c_new_pg}"

c_flags = getattr(rt, "CREATIONFLAGS", None)
print(f"CREATIONFLAGS = {hex(c_flags) if c_flags is not None else None}")
assert c_flags == 0x08000200, f"Expected 0x08000200, got {c_flags}"

print("\n--- AUDIT CHECK 2: get_subprocess_creationflags ---")
has_fn = hasattr(rt, "get_subprocess_creationflags")
print(f"hasattr(rt, 'get_subprocess_creationflags'): {has_fn}")
if not has_fn:
    print("MISSING_SYMBOL: get_subprocess_creationflags is undefined in runtime.py")
else:
    val = rt.get_subprocess_creationflags()
    print(f"get_subprocess_creationflags() = {hex(val)}")
    assert val == 0x08000200

print("\n--- AUDIT CHECK 3: spawn_hidden_subprocess ---")
has_spawn = hasattr(rt, "spawn_hidden_subprocess")
print(f"hasattr(rt, 'spawn_hidden_subprocess'): {has_spawn}")
if not has_spawn:
    print("MISSING_SYMBOL: spawn_hidden_subprocess is undefined in runtime.py")
else:
    proc = rt.spawn_hidden_subprocess([sys.executable, "-c", "import sys; print('TEST_SUCCESS'); sys.exit(0)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = proc.communicate(timeout=5)
    print(f"proc type: {type(proc)}")
    print(f"proc returncode: {proc.returncode}")
    print(f"proc stdout: {out.strip()}")
    assert proc.returncode == 0
    assert "TEST_SUCCESS" in out
