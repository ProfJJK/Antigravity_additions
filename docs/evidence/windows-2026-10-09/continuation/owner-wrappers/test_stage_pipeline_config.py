"""Staging guard tests: no protected writes, task mutation or model execution."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

WORK = Path(__file__).parent
SCRIPT = WORK / 'stage_pipeline_config.ps1'
PROPOSAL = Path(r'C:\Program Files\CoChem\InstallGuard4.2.7-windows-20261006\repository\config\windows\aetherdesk-427.proposed.json')
CANDIDATE = WORK / 'pipeline.staged.json'


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(body):
    code = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile({quote(SCRIPT)},[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    return subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', code + body],
                          text=True, capture_output=True, timeout=30)


def test_candidate_exactly_one_evidence_path_change_and_preserves_policy():
    original = PROPOSAL.read_bytes()
    candidate = CANDIDATE.read_bytes()
    assert hashlib.sha256(original).hexdigest() == 'f3161c436ffeb6a519dcaece7bf2ffbef61445cee6b4cb9513147bd3b49371a3'
    assert hashlib.sha256(candidate).hexdigest() == '2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6'
    original_doc, candidate_doc = json.loads(original), json.loads(candidate)
    old_hold = original_doc['providers']['gemini']['integration_hold']
    new_hold = candidate_doc['providers']['gemini']['integration_hold']
    assert old_hold['evidence_path'] != new_hold['evidence_path']
    old_hold['evidence_path'] = new_hold['evidence_path']
    assert original_doc == candidate_doc
    assert candidate_doc['max_execution_slots'] == 4
    assert len(candidate_doc['workers']) == 6
    assert candidate_doc['knowledge']['enabled'] is True


def test_actual_windows_parser_argv_preserves_python_keys():
    # Root's complete protected custody/revision inspection has passed. This
    # command only imports load_config; no Runtime, doctor or database is opened.
    python = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006\.venv\Scripts\python.exe')
    result = run(f'Invoke-ParserOnly {quote(python)} {quote(CANDIDATE)} | ConvertTo-Json -Compress')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'parser_valid': True, 'worker_identities': 6,
        'max_shared_slots': 4, 'knowledge_enabled': True, 'activation_ready': False}


@pytest.mark.parametrize('change,success', [('', True),
    ("$candidate=$candidate.Replace('\"max_execution_slots\": 4','\"max_execution_slots\": 6')", False),
    ("$candidate=$candidate.Replace('verification_pending','ready')", False),
    ("$original=$original.Replace('native-contract-agy-1.3.1-followup.json','other.json')", False)])
def test_rebinding_refuses_other_changes(change, success):
    result = run(f"$original=[IO.File]::ReadAllText({quote(PROPOSAL)});$candidate=[IO.File]::ReadAllText({quote(CANDIDATE)});" +
                 change + ';Assert-OnlyEvidenceRebind $original $candidate')
    assert (result.returncode == 0) is success, result.stderr


@pytest.mark.parametrize('change,success', [('', True),
    ('$c.max_execution_slots=6', False), ("$l.slots.slot6.sid='different'", False),
    ("$c.workers.slot2.credential_target='CoChem422/slot1'", False),
    ("$c.slot_roots.slot5='C:\\wrong'", False), ("$c.workers.PSObject.Properties.Remove('slot6')", False)])
def test_layout_requires_six_actual_mappings_and_four_shared_slots(change, success):
    body = f"$c=Get-Content -LiteralPath {quote(CANDIDATE)} -Raw|ConvertFrom-Json;"
    body += """$l=[pscustomobject]@{private_root=$c.private_root;operator_name=$c.operator_name;slots=[pscustomobject]@{}};$accounts=@();
    foreach($n in 1..6){$key="slot$n";$w=$c.workers.PSObject.Properties[$key].Value;
    $l.slots|Add-Member NoteProperty $key ([pscustomobject]@{identity=$w.name;credential_target=$w.credential_target;root=$c.slot_roots.PSObject.Properties[$key].Value;sid="SID$n"});
    $accounts+=@([pscustomobject]@{Name=$w.name;SID="SID$n"})};
    """
    result = run(body + change + ';Assert-LayoutMapping $c $l $accounts')
    assert (result.returncode == 0) is success, result.stderr


@pytest.mark.parametrize('hresult,held', [(-2147024894, False), (-2147024891, True), (-2147216625, True), (-2147216629, True)])
def test_task_errors_never_become_false_absence(hresult, held):
    result = run(f"""$folder=[pscustomobject]@{{}};$folder|Add-Member ScriptMethod GetTask {{param($name) throw [Runtime.InteropServices.COMException]::new('observed scheduler error',{hresult})}};
    @((Get-DaemonHolds $folder)).Count
    """)
    assert result.returncode == 0, result.stderr
    assert int(result.stdout) == (4 if held else 0)


@pytest.mark.parametrize('enabled,state,instances,held', [(False,3,0,False),(False,1,0,False),(False,0,0,True),(True,3,0,True),(False,4,0,True),(False,2,0,True),(False,3,1,True)])
def test_any_enabled_or_running_daemon_holds_config_apply(enabled, state, instances, held):
    result = run(f"""$script:task=[pscustomobject]@{{Enabled=${str(enabled).lower()};State={state}}};
    $script:task|Add-Member ScriptMethod GetInstances {{param($flags) [pscustomobject]@{{Count={instances}}}}};
    $folder=[pscustomobject]@{{}};$folder|Add-Member ScriptMethod GetTask {{param($name) $script:task}};
    @((Get-DaemonHolds $folder)).Count
    """)
    assert result.returncode == 0, result.stderr
    assert int(result.stdout) == (4 if held else 0)
