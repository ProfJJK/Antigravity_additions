# CoChem Anti-Spoofing Protocol v2
You must strictly enforce the following execution invariants across the CoChem swarm:
1. **Asymmetric Verification**: Agents cannot verify their own work. `cochem-audit` must perform all final validations in the `/tmp/cochem_exec_<uuid>/` quarantine via `zero_trust_runner.py`.
2. **Immutable Infrastructure**: Code infrastructure integrity must be verified via `verify_core_integrity.py` before execution.
3. **No Mocks or Stub Logic**: Eradication of mocked data (no dummy loops, fake data, stub logic). Testing must run against real physical constraints. The `anti_spoof_linter.py` must be used to enforce this.
4. **Hard Abort Criteria**: If the swarm exhausts 3 methodological pivots (`MAX_PIVOT_CYCLES=3`), you must trigger a Hard Abort (`[HARD_ABORT: PHYSICS WALL]`).
5. **Autopsy Triggering**: If a Hard Abort is hit, invoke `cochem-debug` to generate a `Physics_Autopsy_Report.md`.
