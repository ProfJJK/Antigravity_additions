"""Native, read-only deployment-plan checks; never run generated installer commands."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "windows"
SCRIPT = ROOT / "scripts" / "plan_aetherdesk_427_cutover.ps1"
PYTHON = r"C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe"
UV = r"C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\uv\uv.exe"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Requires actual Windows paths, PowerShell and read-only ACL inspection")


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def inputs(tmp_path):
    # A known wrong hash deliberately produces a stale-evidence hold; this is
    # a plan fixture, never a claim of repository or deployment acceptance.
    manifest = save(tmp_path / "manifest.json", {"files": {"pyproject.toml": "0" * 64}})
    return {
        "StageManifest": CONFIG / "aetherdesk-427.stage.json",
        "PipelineProposal": CONFIG / "aetherdesk-427.proposed.json",
        "SupervisorProposal": CONFIG / "aetherdesk-427.supervisor.proposed.json",
        "SourceManifest": manifest,
        "Python": PYTHON,
        "Uv": UV,
    }


def plan(inputs, **overrides):
    executable = shutil.which("powershell.exe")
    if not executable:
        pytest.skip("Native Windows PowerShell is unavailable")
    argv = [executable, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT)]
    for key, value in (inputs | overrides).items():
        argv.extend([f"-{key}", str(value)])
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def test_supervisor_proposal_loads_without_creating_state_and_preserves_holds(tmp_path):
    from cochem_supervisor.config import load_config

    proposed = read(CONFIG / "aetherdesk-427.supervisor.proposed.json")
    pipeline = read(CONFIG / "aetherdesk-427.proposed.json")
    # Only the read-only policy input is redirected; protected state stays absent.
    proposed["pipeline_config"] = str(CONFIG / "aetherdesk-427.proposed.json")
    path = save(tmp_path / "supervisor.json", proposed)
    before = set(tmp_path.iterdir())
    result = load_config(path)
    assert set(tmp_path.iterdir()) == before
    assert (result["max_per_incident"], result["max_per_day"], result["cooldown_seconds"]) == (2, 4, 1800)
    assert result["auto_deploy"] is True
    assert result["pipeline_routing"]["tiers"] == pipeline["routing"]["tiers"]
    assert result["pipeline_routing"]["policy_version"] == 2
    assert result["pipeline_routing"]["provider_limits"] == pipeline["routing"]["provider_limits"]
    assert all(value is None for value in result["pipeline_routing"]["model_limits"].values())
    expected_hold = pipeline["providers"]["gemini"]["integration_hold"]
    repair_provider = next(spec for spec in result["providers"] if spec["provider"] == "gemini")
    assert repair_provider["integration_hold"] == expected_hold
    observed_hold = result["provider_integration_holds"]["gemini"]
    assert observed_hold["state"] == "verification_pending"
    assert observed_hold["scope"] == "isolated_pipeline_integration"
    assert observed_hold["category"] == "compatibility"
    assert observed_hold["provider"] == "gemini"
    for key in ("reason", "finding_ids", "evidence_path", "evidence_sha256", "executable_sha256", "version"):
        assert observed_hold[key] == expected_hold[key]
    evidence_bytes = Path(observed_hold["evidence_path"]).read_bytes()
    assert hashlib.sha256(evidence_bytes).hexdigest() == observed_hold["evidence_sha256"]
    native = json.loads(evidence_bytes)["installed_executables"]["gemini"]
    assert (native["sha256"], native["version"]) == (observed_hold["executable_sha256"], observed_hold["version"])
    assert "gemini" not in result.get("provider_contract_errors", {})
    assert all(repair_provider[key] is None for key in ("arguments", "subscription_probe", "inference_only"))
    assert result["repair_worker"]["name"] not in {worker["name"] for worker in pipeline["workers"].values()}
    assert result["_staging"]["migration"]["previous_supervisor_data_root"] is None
    assert result["_staging"]["migration"]["create_empty_replacement_ledgers"] is False
    assert Path(result["pointer_file"]).parent == Path(result["private_root"])
    assert result["_staging"]["state_paths"]["repair_job_board"].endswith(r"\private\repair-job-board.db")


def test_plan_is_held_and_commands_remain_inert_explicit_data(inputs, tmp_path):
    before = set(tmp_path.iterdir())
    result = plan(inputs)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert set(tmp_path.iterdir()) == before
    assert data["mode"] == "READ_ONLY_PLAN"
    assert data["activation_ready"] is False
    assert data["initial_shared_slots"] == 4
    assert data["max_shared_slots"] == 4
    assert data["provisioned_worker_identities"] == 6
    assert data["worker_identity_status"] == "PLANNED_NOT_PROVISIONED"
    assert data["commands_executed"] == []
    assert data["source_mismatches"] == ["pyproject.toml"]
    assert {"source-evidence-stale", "previous-budget-root", "agy-native-contract", "migration-evidence", "system-acceptance", "activation-review"} <= {row["id"] for row in data["holds"]}
    commands = {row["id"]: row for row in data["commands_for_review"]}
    first = commands["pipeline-install"]["arguments"]
    assert first[first.index("-Slots") + 1] == "6"
    assert "-RegisterDaemon" not in first
    supervisor = commands["supervisor-install"]["arguments"]
    assert supervisor[supervisor.index("-PreviousDataRoot") + 1] is None
    assert "-RegisterSupervisor" not in supervisor
    for row in commands.values():
        args = row["arguments"]
        assert args[args.index("-Python") + 1] == PYTHON
        assert args[args.index("-Uv") + 1] == UV
        assert args[args.index("-WardenTaskName") + 1] == "CoChem-4.2.7-Warden"
        assert "CoChemHostWarden_V412" not in args
        assert Path(row["script"]).is_absolute()


@pytest.mark.parametrize("which,field,value,message", [
    ("PipelineProposal", "max_execution_slots", 8, "four shared slots"),
    ("SupervisorProposal", "max_per_day", 5, "repair ceilings"),
    ("SupervisorProposal", "warden_task", "CoChemHostWarden_V412", "legacy Hyper-V MCP"),
])
def test_plan_refuses_capacity_budget_or_legacy_task_changes(inputs, tmp_path, which, field, value, message):
    candidate = read(inputs[which])
    candidate[field] = value
    modified = save(tmp_path / "modified.json", candidate)
    result = plan(inputs, **{which: modified})
    assert result.returncode != 0
    assert message in result.stderr


def test_plan_refuses_an_identity_pool_too_small_for_six_chapters(inputs, tmp_path):
    candidate = read(inputs["PipelineProposal"])
    for slot in ("slot5", "slot6"):
        del candidate["workers"][slot]
        del candidate["slot_roots"][slot]
    result = plan(inputs, PipelineProposal=save(tmp_path / "too-few-identities.json", candidate))
    assert result.returncode != 0
    assert "six isolated worker identities" in result.stderr


@pytest.mark.parametrize("field", ["name", "credential_target", "slot_root"])
def test_plan_refuses_reusing_another_chapters_identity_or_root(inputs, tmp_path, field):
    candidate = read(inputs["PipelineProposal"])
    if field == "slot_root":
        candidate["slot_roots"]["slot6"] = candidate["slot_roots"]["slot1"]
    else:
        candidate["workers"]["slot6"][field] = candidate["workers"]["slot1"][field]
    result = plan(inputs, PipelineProposal=save(tmp_path / "reused-identity.json", candidate))
    assert result.returncode != 0
    assert "distinct canonical names, credential targets and roots" in result.stderr


def test_six_planned_identities_finish_six_owned_chapters_under_four_shared_seats(tmp_path):
    # Real configuration/SQLite admission regression only; synthetic storage
    # receipts do not attest native execution, identity provisioning or Docker.
    from cochem_pipeline.admission import JointAdmission
    from cochem_pipeline.config import load_config
    from pipeline_tests.test_store import chapter, finish, seeded

    config = load_config(str(CONFIG / "aetherdesk-427.proposed.json"))
    slots = [f"slot{number}" for number in range(1, 7)]
    assert set(config.workers) == set(config.slot_roots) == set(slots)
    assert config.max_execution_slots == config.docker.max_containers == 4
    assert config.docker.warm_pool_size == 2
    for number, slot in enumerate(slots, 1):
        assert config.workers[slot] == {"name": f"CoChem422Worker{number}", "credential_target": f"CoChem422/{slot}"}
        assert config.slot_roots[slot] == config.private_root.parent / "workers" / slot
    store, workflow_id = seeded(tmp_path, count=6)
    try:
        admission = JointAdmission(store, None, capacity=config.max_execution_slots, max_capacity=config.max_execution_slots)
        def claim(slot):
            return admission.claim("identity-pool-regression", worker_slot=slot, requires_cleanup=False)
        first = [claim(slot)[0] for slot in slots[:4]]
        assert len(store.active_jobs()) == 4
        assert claim(slots[4]) is None
        finish(store, first[0], chapter(first[0]))
        assert claim(slots[0]) is None  # Existing chapter ownership is immutable.
        fifth = claim(slots[4])[0]
        assert len(store.active_jobs()) == 4
        assert claim(slots[5]) is None
        finish(store, first[1], chapter(first[1]))
        sixth = claim(slots[5])[0]
        assert len(store.active_jobs()) == 4
        for job in [*first[2:], fifth, sixth]:
            finish(store, job, chapter(job))
        chapters = [job for job in store.workflow(workflow_id)["jobs"] if job["kind"] == "CHAPTER_DRAFT"]
        assert len(chapters) == 6
        assert {job["worker_slot"] for job in chapters} == set(slots)
        assert {job["status"] for job in chapters} == {"COMPLETED"}
    finally:
        store.close()


def test_evidence_output_is_create_only(inputs, tmp_path):
    output = tmp_path / "evidence.json"
    result = plan(inputs, Output=output)
    assert result.returncode == 0, result.stderr
    assert read(output)["commands_executed"] == []
    original = output.read_bytes()
    refused = plan(inputs, Output=output)
    assert refused.returncode != 0
    assert output.read_bytes() == original


def test_evidence_output_refuses_an_alternate_stream(inputs, tmp_path):
    original = tmp_path / "preserved.txt"
    original.write_bytes(b"preserved")
    result = plan(inputs, Output=str(original) + ":hidden-plan")
    assert result.returncode != 0
    assert "without alternate streams" in result.stderr
    assert original.read_bytes() == b"preserved"


@pytest.mark.parametrize("output", [
    r"R:\cutover-plan-must-not-be-written.json",
    r"C:\Program Files\CoChem\cutover-plan-must-not-be-written.json",
    r"C:\ProgramData\CoChemSupervisor427-windows-20261006\private\cutover-plan-must-not-be-written.json",
    r"D:\__CoChem\__agentic\v4.1.2\cutover-plan-must-not-be-written.json",
    r"C:\Users\ansac\CoChem427\cutover-plan-must-not-be-written.json",
])
def test_evidence_cannot_be_written_into_protected_or_preserved_roots(inputs, output):
    path = Path(output)
    existed = path.exists()
    result = plan(inputs, Output=output)
    assert result.returncode != 0
    assert "outside protected deployment/state" in result.stderr
    assert path.exists() is existed


def test_powershell_ast_contains_no_process_task_install_or_dynamic_execution():
    executable = shutil.which("powershell.exe")
    if not executable:
        pytest.skip("Native Windows PowerShell is unavailable")
    path = str(SCRIPT).replace("'", "''")
    code = f"""
    $ErrorActionPreference='Stop';$tokens=$null;$errors=$null;
    $ast=[Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors);
    if ($errors.Count) {{ throw ($errors | Out-String) }};
    $commands=@($ast.FindAll({{param($n) $n -is [Management.Automation.Language.CommandAst]}},$true));
    $forbidden=@($commands | Where-Object {{ $_.InvocationOperator -ne 'Unknown' -or
        $_.GetCommandName() -match '^(Start-|Stop-|Register-|Unregister-|Set-|Remove-|Copy-|Move-|Invoke-Expression$|Invoke-Command$|Enable-|Disable-|New-|powershell|pwsh|cmd|python|uv)' }} | ForEach-Object {{ $_.Extent.Text }});
    ConvertTo-Json -InputObject $forbidden -Compress
    """
    result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", code],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    # StrictMode changes only this script's parser behavior, not machine state.
    assert json.loads(result.stdout) == ["Set-StrictMode -Version Latest"]
