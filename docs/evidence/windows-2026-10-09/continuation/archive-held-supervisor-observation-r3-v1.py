"""CreateNew archive of an ordinary-Windows preparation; no operational actions."""
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

WORK = Path(r"C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next")
OUT = Path(r"D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\docs\evidence\windows-2026-10-06\held-supervisor-observation-preparation-2026-10-07")
FILES = (
    "held-supervisor-observation-r3-v1.py",
    "install-held-supervisor-observation-r3-v1.ps1",
    "test_held_supervisor_observation_r3_v1.py",
    "test_held_supervisor_observation_wrapper_r3_v1.py",
    "held-supervisor-observation-r3-v1-tests-final.xml",
    "held-supervisor-observation-r3-v1-preview-final.json",
)
EXPECTED = {
    FILES[0]: "d2fd68543963a05ba918c6194729a8bca642322565235ab18c7ba17ccad17640",
    FILES[1]: "88c285591b579a432718dc5ab383b844e597299f5372c6a6f4dd98e9efdd38e5",
    FILES[4]: "7456c82b79a5568b3cad05ec5fc4ffae0affe85009c084c6d0e6133f14125eb4",
    FILES[5]: "b1870082b2bc01ff6c67aefcff931ae735ec55d2b13b1f43fa5ce789268ca181",
}

def main():
    contents = {name: (WORK / name).read_bytes() for name in FILES}
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}
    for name, digest in EXPECTED.items():
        if hashes[name] != digest:
            raise ValueError("reviewed source drift")
    suite = ET.fromstring(contents[FILES[4]]).find("testsuite")
    if suite is None or any(suite.attrib[k] != v for k, v in {"tests": "43", "failures": "0", "errors": "0", "skipped": "0"}.items()):
        raise ValueError("unexpected final test results")
    preview = json.loads(contents[FILES[5]])
    if preview["holds"] != ["STAGING_NOT_INSTALLED", "COMMISSIONING_NOT_COMPLETE"]:
        raise ValueError("unexpected preview holds")
    OUT.mkdir(parents=True, exist_ok=False)
    for name, raw in contents.items():
        with (OUT / name).open("xb") as stream:
            stream.write(raw)
    record = {
        "schema": "cochem-held-supervisor-observation-preparation/1",
        "status": "REVIEWED_PREPARED_NOT_APPLIED",
        "platform": "Windows ordinary ansac token; Python 3.12.13 and Windows PowerShell 5.1",
        "files": [{"name": name, "sha256": hashes[name], "bytes": len(contents[name])} for name in FILES],
        "tests": {"passed": 43, "failed": 0, "errors": 0, "skipped": 0, "junit_seconds": float(suite.attrib["time"]), "system_or_provider_execution": False},
        "review": {"reviewer": "host_inspection", "result": "PASS_NO_REMAINING_CONCRETE_FINDING", "source_sha256": EXPECTED[FILES[0]], "wrapper_sha256": EXPECTED[FILES[1]], "reviewer_executed_apply": False, "reviewer_reran_tests": False},
        "actual_preview_holds": preview["holds"],
        "privileged_task_and_identity_checks": "Deferred to the future reviewed elevated/SYSTEM phase; ordinary preview does not attest them.",
        "future_write_set": [
            "Fresh Program Files observation code, inputs, receipts and empty release root only.",
            "Fresh dedicated ProgramData observation private boundary and repair workspace.",
            "Exact copy of five closed staged unresolved-pair files; originals and legacy history remain unchanged.",
            "Repair account and credential creation only when both exact names are absent; mixed or unverifiable existing state holds; valid existing credentials preserved.",
            "Scoped repair workspace permission/indexing and Defender exclusion provisioning.",
            "Separate observation config and private release pointer; existing Warden task/config/pointer unchanged.",
            "One new provisioning task and one observation-only boot task, zero automatic retries; no Warden start/stop/restart.",
            "Observation status, process history and SQLite coordination sidecars only; paid/component ledger rows remain held.",
        ],
        "safety_contract": {
            "installed_r3_changed": False, "existing_warden_changed": False, "legacy_history_read_or_migrated": False,
            "provider_or_model_calls": 0, "paid_repair_enabled": False, "component_recovery_enabled": False,
            "ram_root_or_startup_task_changed": False, "oracle_acceptance_prerequisite": False,
            "first_useful_pipeline_prerequisite": False, "full_srs_acceptance_claimed": False,
            "partial_outputs_preserved": True, "apply_performed": False,
        },
        "required_future_prerequisites": [
            "Exact successful independent stopped-staging receipt and source/runtime custody.",
            "Exact successful first-Warden commissioning receipt and preserved prior tasks.",
            "Fresh observation code/data/task names; native identity/credential precheck under SYSTEM.",
        ],
        "human_requirement": "One future consolidated elevated installation action for independent held observation; no provider sign-in or budget estimate is required for this observer. It is not an additional first-use gate.",
    }
    raw = (json.dumps(record, indent=2) + "\n").encode()
    with (OUT / "preparation.json").open("xb") as stream:
        stream.write(raw)
    print(json.dumps({"path": str(OUT / "preparation.json"), "sha256": hashlib.sha256(raw).hexdigest(), "files": hashes, "junit_seconds": record["tests"]["junit_seconds"]}))

if __name__ == "__main__":
    main()
