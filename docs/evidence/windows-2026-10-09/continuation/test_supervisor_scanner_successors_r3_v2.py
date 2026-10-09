"""Scanner-only successor bindings and inert real WinPS5.1 wrapper regressions.

All writes stay in disposable pytest workspace paths. No Apply entrypoint,
Scheduler COM connection, protected-host change, provider or Python task runs.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

WORK = Path(__file__).resolve().parent
POWERSHELL = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
STAGING = WORK / 'install-independent-supervisor-staging-r3-v2.ps1'
HELD = WORK / 'install-held-supervisor-observation-r3-v2.ps1'
INSPECTION_HASH = '5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d'
STAGING_V1_HASH = '4f8d3afa512ce81a73c114a384e208c39006ab3abe4771512a121ebf7ae7b098'
HELD_V1_HASH = '88c285591b579a432718dc5ab383b844e597299f5372c6a6f4dd98e9efdd38e5'
STAGING_V2_HASH = '00412dfa8d7d6668713f9fb850c6fc4a2dd4df4dcc1527c65106ce7497be893d'
HELD_V2_HASH = '9fb90f828ff3b0a7776af9bc48acd99eb2a520508a80271a1e87b785a2ce7313'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(body):
    completed = subprocess.run(
        [POWERSHELL, '-NoProfile', '-NonInteractive', '-Command',
         "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;" + body],
        capture_output=True, text=True, timeout=40,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, WORK / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Re-run the existing ordinary-token real-function fixtures against successors.
# Their privileged effects are substituted; function definitions are actual AST.
staging = load('scanner_v2_staging_baseline', 'test_independent_supervisor_staging_r3_v1.py')
staging.PS = STAGING
for name in (
    'test_actual_inventory_strict_ps51',
    'test_bound_receipt_projection_preserves_safe_failure_details',
    'test_reviewed_native_copy_function_createnew_identity_and_hardlinks',
    'test_task_absence_is_not_permission_denial',
    'test_full_ps51_staging_apply_flow_uses_new_copy_then_private_task',
    'test_real_task_definition_argv_registration_and_readback',
):
    globals()[name] = getattr(staging, name)

observation = load('scanner_v2_observation_baseline', 'test_held_supervisor_observation_wrapper_r3_v1.py')
observation.SCRIPT = HELD
for name in dir(observation):
    if name.startswith('test_'):
        globals()[name] = getattr(observation, name)


def test_frozen_predecessors_helper_and_successor_hashes():
    for name, pin in (
        ('install-independent-supervisor-staging-r3-v1.ps1', STAGING_V1_HASH),
        ('install-held-supervisor-observation-r3-v1.ps1', HELD_V1_HASH),
        ('install-private-knowledge-candidate.ps1', '5f663f3eab141c5feaba465615ea1a6e4d76c155087e852ae464f8aed426d05a'),
        ('protected-code-inspection-v4.ps1', INSPECTION_HASH),
        (STAGING.name, STAGING_V2_HASH),
        (HELD.name, HELD_V2_HASH),
        ('stage-independent-supervisor-holds-r3-v1.py', '215af8db414c7bbc4352c825dbf250b7bbc244a8a2dbc2d4addadc72cf1acb74'),
        ('held-supervisor-observation-r3-v1.py', 'd2fd68543963a05ba918c6194729a8bca642322565235ab18c7ba17ccad17640'),
        ('independent-supervisor-staging-r3-v1.manifest.json', '6aec5eaf82205be34e5849e73700f2e817e157be5c5ddd4366f04e549b75f0c4'),
    ):
        assert digest(WORK / name) == pin, name


def test_only_reviewed_scanner_dependency_and_support_binding_bytes_changed():
    expected = (WORK / 'install-independent-supervisor-staging-r3-v1.ps1').read_bytes()
    expected = expected.replace(
        b"'New-PrivateDirectory','Assert-CodeTreeOnce','Wait-KnowledgeTask'",
        b"'New-PrivateDirectory','Wait-KnowledgeTask'",
    ).replace(
        b'\n)\n$held=[Collections.Generic.List[IO.Stream]]::new()',
        ("\n    [pscustomobject]@{path=(Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1');hash='"
         + INSPECTION_HASH + "';names=@('Assert-CodeTreeOnce')}\n)\n$held=[Collections.Generic.List[IO.Stream]]::new()").encode(),
    )
    assert STAGING.read_bytes() == expected
    expected = (WORK / 'install-held-supervisor-observation-r3-v1.ps1').read_bytes()
    expected = expected.replace(
        b"$support=Join-Path $PSScriptRoot 'install-independent-supervisor-staging-r3-v1.ps1'",
        b"$support=Join-Path $PSScriptRoot 'install-independent-supervisor-staging-r3-v2.ps1'",
    ).replace(STAGING_V1_HASH.encode(), STAGING_V2_HASH.encode()).replace(
        b"names=@('Assert-CodeTreeOnce','Wait-KnowledgeTask','Assert-CompletedKnowledgeTask')",
        b"names=@('Wait-KnowledgeTask','Assert-CompletedKnowledgeTask')",
    ).replace(
        b"  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'register-stopped-warden-r3.ps1')",
        ("  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'protected-code-inspection-v4.ps1');hash='"
         + INSPECTION_HASH + "';names=@('Assert-CodeTreeOnce')},\n"
         + "  [pscustomobject]@{path=(Join-Path $PSScriptRoot 'register-stopped-warden-r3.ps1')").encode(),
    )
    assert HELD.read_bytes() == expected


@pytest.mark.parametrize('wrapper', [STAGING, HELD], ids=['staging', 'observation'])
def test_ast_functions_unchanged_scanner_binding_unique_and_import_and_custody_loops_present(wrapper):
    old = wrapper.with_name(wrapper.name.replace('-v2.ps1', '-v1.ps1'))
    value = run(r'''
      function Read-Ast {param($Path)$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e);if($e.Count){throw 'parse refused'};return $a}
    ''' + f'$old=Read-Ast {quote(old)};$new=Read-Ast {quote(wrapper)};$workspaceFixtureRoot={quote(WORK)};' + r'''
      $repo='D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
      $functions=@{};foreach($f in $old.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)){$functions[$f.Name]=$f.Extent.Text}
      $newFunctions=@($new.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true));$same=$newFunctions.Count -eq $functions.Count
      foreach($f in $newFunctions){$same=$same -and $functions.ContainsKey($f.Name) -and $functions[$f.Name] -ceq $f.Extent.Text}
      $assignment=@($new.FindAll({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left -is [Management.Automation.Language.VariableExpressionAst] -and $n.Left.VariablePath.UserPath -ceq 'dependencies'},$true))
      if($assignment.Count -ne 1){throw 'dependency assignment ambiguous'}
      # An extracted dynamic scriptblock has an empty automatic PSScriptRoot.
      . ([scriptblock]::Create($assignment[0].Extent.Text.Replace('$PSScriptRoot','$workspaceFixtureRoot')))
      $inspection=@($dependencies|Where-Object {($_.names -ccontains 'Assert-CodeTreeOnce')})
      $oldScan=@($dependencies|Where-Object {$_.path.EndsWith('install-private-knowledge-candidate.ps1') -and $_.names -ccontains 'Assert-CodeTreeOnce'})
      $loops=@($new.FindAll({param($n)$n -is [Management.Automation.Language.ForEachStatementAst] -and $n.Condition.Extent.Text -ceq '$dependencies'},$true))
      $importLoops=@($loops|Where-Object {$_.Extent.Text -match 'Import-StagingFunctions'});$holdLoops=@($loops|Where-Object {$_.Extent.Text -match 'Open-VerifiedFile'})
      @{same_functions=$same;scanner_count=$inspection.Count;old_scanner_count=$oldScan.Count;scanner_path=$inspection[0].path;scanner_hash=$inspection[0].hash;scanner_names=@($inspection[0].names);import_loops=$importLoops.Count;hold_loops=$holdLoops.Count}|ConvertTo-Json -Compress
    ''')
    assert value == {
        'same_functions': True, 'scanner_count': 1, 'old_scanner_count': 0,
        'scanner_path': str(WORK / 'protected-code-inspection-v4.ps1'),
        'scanner_hash': INSPECTION_HASH, 'scanner_names': ['Assert-CodeTreeOnce'],
        'import_loops': 1, 'hold_loops': 1,
    }


@pytest.mark.parametrize('wrapper', [STAGING, HELD], ids=['staging', 'observation'])
@pytest.mark.parametrize('case', ['valid', 'changed_before_import', 'changed_before_hold'])
def test_actual_successor_import_and_held_native_identity_reject_changes(tmp_path, wrapper, case):
    fixture = tmp_path / 'inspection.ps1'
    fixture.write_bytes((WORK / 'protected-code-inspection-v4.ps1').read_bytes())
    body = f'$wrapper={quote(wrapper)};$fixture={quote(fixture)};$case={quote(case)};$inspectionHash={quote(INSPECTION_HASH)};' + r'''
      function Read-Ast {param($Path)$t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile($Path,[ref]$t,[ref]$e);if($e.Count){throw 'parse refused'};return $a}
    ''' + f'$support=Read-Ast {quote(STAGING)};' + r'''
      $f=@($support.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Import-StagingFunctions'},$true))[0];. ([scriptblock]::Create($f.Extent.Text))
      foreach($definition in @(Import-StagingFunctions 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1' '0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b' @('Assert-NoReparseAncestors','Initialize-FileIdentity','Open-VerifiedFile'))){. ([scriptblock]::Create($definition))};Initialize-FileIdentity
      function Assert-ProtectedPath {param($Path)if($Path -cne $fixture){throw 'outside disposable fixture'}}
      $new=Read-Ast $wrapper;$dependencies=@([pscustomobject]@{path=$fixture;hash=$inspectionHash;names=@('Assert-CodeTreeOnce')});$held=[Collections.Generic.List[IO.Stream]]::new()
      $loops=@($new.FindAll({param($n)$n -is [Management.Automation.Language.ForEachStatementAst] -and $n.Condition.Extent.Text -ceq '$dependencies'},$true))
      $imports=@($loops|Where-Object {$_.Extent.Text -match 'Import-StagingFunctions'});$holds=@($loops|Where-Object {$_.Extent.Text -match 'Open-VerifiedFile'})
      $importRefused=$false;$holdRefused=$false;$bound=$false;$writeBlocked=$false;$heldCount=0
      if($case -eq 'changed_before_import'){[IO.File]::AppendAllText($fixture,"`n# changed fixture")}
      try{. ([scriptblock]::Create($imports[0].Extent.Text));$bound=(Get-Item Function:Assert-CodeTreeOnce).Definition.Contains('[CoChemProtectedCodeInspectionV4]::Scan')}catch{$importRefused=$true}
      if(-not $importRefused){
       if($case -eq 'changed_before_hold'){[IO.File]::AppendAllText($fixture,"`n# changed fixture")}
       try{. ([scriptblock]::Create($holds[0].Extent.Text));$heldCount=$held.Count}catch{$holdRefused=$true}
       if(-not $holdRefused){try{$writer=[IO.File]::Open($fixture,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None);$writer.Dispose()}catch{$writeBlocked=$true}}
      }
      foreach($stream in $held){$stream.Dispose()};$writer=[IO.File]::Open($fixture,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None);$writer.Dispose()
      @{import_refused=$importRefused;hold_refused=$holdRefused;bound_to_v4=$bound;held_count=$heldCount;write_blocked=$writeBlocked}|ConvertTo-Json -Compress
    '''
    assert run(body) == {
        'import_refused': case == 'changed_before_import',
        'hold_refused': case == 'changed_before_hold',
        'bound_to_v4': case != 'changed_before_import',
        'held_count': 1 if case == 'valid' else 0,
        'write_blocked': case == 'valid',
    }


@pytest.mark.parametrize('kind', ['staging', 'observation'])
def test_existing_partial_destination_refused_before_scan_copy_or_task(tmp_path, kind):
    root = tmp_path / 'existing'
    root.mkdir()
    (root / 'preserve.bin').write_bytes(b'opaque partial state')
    value = staging.ps(f'$fixture={quote(tmp_path)};$kind={quote(kind)};' + f'''
      $t=$null;$e=$null;$a=[Management.Automation.Language.Parser]::ParseFile({quote(HELD)},[ref]$t,[ref]$e)
      foreach($f in $a.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}}
    ''' + r'''
      $script:targetRoot=Join-Path $fixture 'existing';$script:dataRoot=Join-Path $fixture 'data';$script:events=[Collections.Generic.List[string]]::new()
      function Assert-CodeTreeOnce {param($Path)$script:events.Add('scan');throw 'unexpected scan'}
      function New-ProtectedDirectory {param($Path)$script:events.Add('mkdir');throw 'unexpected mkdir'}
      function Get-StagingTaskOrAbsent {param($Folder,$Name)$script:events.Add('task');throw 'unexpected task access'}
      $refused=$false;try{if($kind -eq 'staging'){$null=Invoke-IndependentStaging @() @() $null @{} ('a'*64)}else{$null=Invoke-HeldObservationInstall $null $null $null}}catch{$refused=$true}
      @{refused=$refused;events=@($script:events);preserved=([IO.File]::ReadAllText((Join-Path $script:targetRoot 'preserve.bin')) -ceq 'opaque partial state');data_absent=(-not [IO.Directory]::Exists($script:dataRoot))}|ConvertTo-Json -Compress
    ''')
    assert value == {'refused': True, 'events': [], 'preserved': True, 'data_absent': True}
