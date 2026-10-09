"""One CPU-only sample, run manually through the reviewed protected SYSTEM task.

This never configures a monitor, installs a driver, starts a pipeline, or grants
worker access. Device containment remains a separate acceptance requirement.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(r"C:\Program Files\CoChem\CpuAcceptance4.2.7-windows-20261006")
PYTHON = Path(r"C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006\.venv\Scripts\python.exe")
PROBE = Path(r"C:\Program Files\CoChem\CpuSensors4.2.7-windows-20261006\cochem-cpu-temperature.exe")


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch("[0-9a-f]{32}", sys.argv[1]):
        raise ValueError("A single reviewed 32-hex receipt nonce is required")
    from cochem_pipeline import cpu_temperature, resource_telemetry, windows
    windows.require_system()  # An elevated operator is deliberately insufficient.
    if Path(__file__).resolve() != ROOT / "cpu-system-acceptance.py" or Path(sys.executable).resolve() != PYTHON:
        raise ValueError("Run only the exact protected script and installed interpreter")
    for path in (ROOT, Path(__file__), PYTHON, Path(cpu_temperature.__file__),
                 Path(resource_telemetry.__file__), Path(windows.__file__)):
        windows.validate_code_path(path)
    report_path = ROOT / "cpu-system-acceptance.json"
    if report_path.exists():
        raise FileExistsError("Preserve the existing one-shot receipt")
    nonce = sys.argv[1]
    report = {
        "schema": "cochem-system-cpu-acceptance/1", "nonce": nonce,
        "system_sid": "S-1-5-18", "started_at_unix_ms": int(time.time() * 1000),
        "status": "FAILED", "hardware_probe_invoked": False,
        "device_containment_tested": False, "worker_denials_tested": False,
        "pipeline_started": False, "sensor_scope": "CPU direct Intel core/package only",
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "module_sha256": {module.__name__: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                          for module in (cpu_temperature, resource_telemetry, windows)},
    }
    # CreateNew before touching hardware: an earlier receipt can never be reset.
    with report_path.open("x", encoding="utf-8") as stream:
        try:
            executable = cpu_temperature.verify_probe_bundle(PROBE)
            report["bundle_manifest_sha256"] = hashlib.sha256(
                (PROBE.parent / "cochem-cpu-temperature.manifest.json").read_bytes()).hexdigest()
            report["hardware_probe_invoked"] = True
            raw = resource_telemetry._bounded_command(
                [str(executable), "--nonce", nonce], timeout=3, max_output_bytes=32768,
                cwd=executable.parent)
            maximum = cpu_temperature.parse_cpu_temperature(raw, nonce, time.time())
            parsed = json.loads(raw.decode("utf-8-sig"))
            report.update(status="CPU_SAMPLE_VALID", maximum_celsius=maximum,
                          sampled_at_unix_ms=parsed["sampled_at_unix_ms"],
                          sensor_count=len(parsed["sensors"]),
                          response_sha256=hashlib.sha256(raw).hexdigest())
        except Exception as error:
            report["error_type"] = type(error).__name__
            report["error"] = str(error)[:1024]
        report["completed_at_unix_ms"] = int(time.time() * 1000)
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return 0 if report["status"] == "CPU_SAMPLE_VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
