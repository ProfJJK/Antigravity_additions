"""Inert PS5.1 flow/security fixtures. No installation, task or privileged API."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import hashlib

import pytest

ROOT = Path(__file__).parent
SCRIPT = ROOT / 'install-stopped-runtime-r3.ps1'
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')


def run(body):
    source = str(SCRIPT).replace("'", "''")
    prelude = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
      foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){{Import-Module (Join-Path $PSHOME "Modules\\$m\\$m.psd1")}};
      $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{source}',[ref]$tokens,[ref]$errors);
      if($errors.Count){{throw ($errors|Out-String)}};
      foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    return subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command', prelude + body],
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize('case', ['valid','unsafe','duplicate','incomplete','baseline','missing','previous_target'])
def test_inventory_is_complete_confined_and_bound_to_prior_source(case):
    result = run(f"$case='{case}';" + r'''
      $script:repo='D:\repo';$script:targetRoot='C:\Program Files\CoChem\new';$script:sourceRoot=$script:targetRoot+'\source';
      $required=@('README.md','pyproject.toml','uv.lock','src/cochem_pipeline/resource_limits.py','src/cochem_pipeline/ramdisk.py','src/cochem_pipeline/deployment.py','src/cochem_pipeline/config.py','src/cochem_pipeline/deployment_revision.py','src/cochem_supervisor/engine.py');
      $rows=@($required|ForEach-Object {[pscustomobject]@{relative=$_;sha256=('a'*64);length=1}});
      foreach($i in 1..91){$rows+=[pscustomobject]@{relative="src/package/item$i.py";sha256=('a'*64);length=1}};
      $inventory=[pscustomobject]@{schema='cochem-stopped-runtime-source/1';host='AETHERDESK';complete_source_freeze=$true;source_repository=$script:repo;target_root=$script:targetRoot;previous_source_manifest_sha256='df473b21f027a711c41a7c9436fbdcfd424a6e24ae3556de37e23bb40220f4e8';files=$rows};
      switch($case){'unsafe'{$rows[20].relative='src/../../secret'}'duplicate'{$rows[20].relative='SRC/COCHEM_PIPELINE/ramdisk.py'}'incomplete'{$inventory.complete_source_freeze=$false}'baseline'{$inventory.previous_source_manifest_sha256=('b'*64)}'missing'{$rows[0].relative='src/other.py'}'previous_target'{$inventory.target_root='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r1'}};
      $refused=$false;$count=0;try{$files=@(Get-RuntimeFiles $inventory);$count=$files.Count}catch{$refused=$true};
      [ordered]@{refused=$refused;count=$count}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'refused': case != 'valid', 'count': 100 if case == 'valid' else 0}


@pytest.mark.parametrize('case', ['valid','capacity','worker','subtree','whitespace'])
def test_actual_r2_configuration_requires_exact_preservation(case):
    result = run(f"$case='{case}';" + r'''
      $before=Get-Content -LiteralPath 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2\pipeline.json' -Raw;
      $after=$before;
      switch($case){
        'capacity'{$after=$after.Replace('"max_execution_slots": 4','"max_execution_slots": 6')}
        'worker'{$after=$after.Replace('"slot6"','"other"')}
        'subtree'{$after=$after.Replace('CoChem427-windows-20261007','Different')}
        'whitespace'{$after=$after+" `n"}
      };
      if($case -ne 'valid' -and $before -ceq $after){throw 'Fixture did not mutate the configuration'};
      $refused=$false;try{Assert-ExactPreservedConfig $before $after}catch{$refused=$true};
      @{refused=$refused}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] == (case != 'valid')


@pytest.mark.parametrize('case', ['absent','disabled','enabled','running','queued','unknown','instances','denied','wrong_hresult'])
def test_all_managed_daemon_states_fail_closed(case):
    result = run(f"$script:case='{case}';" + r'''
      $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($name)
        if($script:case -in @('absent','denied','wrong_hresult')){$code=if($script:case -eq 'absent'){-2147024894}elseif($script:case -eq 'denied'){-2147024891}else{-2147216625};throw [Runtime.InteropServices.COMException]::new('fixture',$code)};
        $state=switch($script:case){'running'{4}'queued'{2}'unknown'{0}default{3}};
        $task=[pscustomobject]@{Enabled=($script:case -eq 'enabled');State=$state};
        $task|Add-Member ScriptMethod GetInstances {param($unused)@{Count=$(if($script:case -eq 'instances'){1}else{0})}};$task
      };
      $refused=$false;try{Assert-StoppedRuntimeTasks $folder}catch{$refused=$true};@{refused=$refused}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] == (case not in ('absent','disabled'))


def test_frozen_build_restores_environment_on_failure_and_does_not_call_installer(tmp_path):
    escaped = str(tmp_path).replace("'", "''")
    result = run(f"$script:targetRoot='{escaped}';" + r'''
      $script:sourceRoot=$script:targetRoot+'\source';$script:python='C:\protected\python.exe';$script:uv='Invoke-FixtureUv';
      $env:UV_INDEX_URL='fixture-existing-index';$env:PYTHONPATH='fixture-existing-pythonpath';$beforeTemp=$env:TEMP;
      function Invoke-FixtureUv {
        if($null -ne $env:UV_INDEX_URL -or $null -ne $env:PYTHONPATH){throw 'Ambient policy leaked'};
        if($env:UV_PROJECT_ENVIRONMENT -ne ($script:targetRoot+'\.venv') -or $env:TEMP -ne ($script:targetRoot+'\build-temp')){throw 'Build escaped fresh root'};
        $script:arguments=@($args);$global:LASTEXITCODE=17
      };
      $failed=$false;try{Invoke-FrozenRuntimeBuild}catch{$failed=$true};
      [ordered]@{failed=$failed;restored=($env:UV_INDEX_URL -eq 'fixture-existing-index' -and $env:PYTHONPATH -eq 'fixture-existing-pythonpath' -and $env:TEMP -eq $beforeTemp);arguments=$script:arguments}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['failed'] and value['restored']
    arguments = value['arguments']
    assert arguments[:3] == ['--no-config','--no-cache','sync']
    assert '--frozen' in arguments and '--no-editable' in arguments and '--no-python-downloads' in arguments
    assert arguments[arguments.index('--link-mode') + 1] == 'copy'
    assert arguments[arguments.index('--extra') + 1] == 'mcp'
    assert not any('install_pipeline_windows' in value or 'provision' in value for value in arguments)


def test_runtime_tree_hardlink_is_refused_before_file_acl_mutation(tmp_path):
    outside = tmp_path / 'outside.txt'; outside.write_bytes(b'preserved')
    root = tmp_path / 'new'; root.mkdir(); os.link(outside, root / 'alias.txt')
    helper = str(REPO / 'scripts/stage_aetherdesk_427_payloads.ps1').replace("'", "''")
    result = run(f"$script:targetRoot='{str(root)}';$helper='{helper}';" + r'''
      $tokens=$null;$errors=$null;$a=[Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors);
      $f=$a.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Initialize-FileIdentity'},$true);
      . ([scriptblock]::Create($f.Extent.Text));Initialize-FileIdentity;
      function Assert-ProtectedPath {param($Path)};function New-CodeAcl {param($Directory)$null};
      # Retain the real tree walk/handle verifier; only ACL objects are fixtures.
    ''' + f"$treeSource='{str(ROOT / 'check-worker-denials.ps1')}';" + r'''
      $treeAst=[Management.Automation.Language.Parser]::ParseFile($treeSource,[ref]$tokens,[ref]$errors);
      $treeFunction=$treeAst.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-CodeTreeOnce'},$true);
      . ([scriptblock]::Create($treeFunction.Extent.Text));
      function Get-Acl {param($LiteralPath)
        $acl=[Security.AccessControl.DirectorySecurity]::new();$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'));
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new('S-1-5-18'),'FullControl','Allow'));$acl};
      $script:aclsChanged=0;function Set-Acl {param($LiteralPath,$AclObject)$script:aclsChanged++};
      $refused=$false;try{Protect-NewRuntimeTree $script:targetRoot}catch{$refused=$true};
      [ordered]@{refused=$refused;acls_changed=$script:aclsChanged}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'refused':True,'acls_changed':0}
    assert outside.read_bytes() == b'preserved'


def test_nonadmin_apply_refuses_before_source_manifest_or_targets():
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Apply'],
                            capture_output=True,text=True,timeout=20)
    assert result.returncode != 0
    assert '-Apply requires the owner in Administrator Windows PowerShell' in result.stderr


def test_actual_ps51_python_c_transport_and_scoped_config_parser(tmp_path):
    """Execute the wrapper's exact validation body as ordinary ansac, no setup."""
    installed = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2')
    fixture = tmp_path / 'revision'; (fixture / 'source/src').mkdir(parents=True)
    for package in ('cochem_pipeline', 'cochem_mcp', 'cochem_supervisor'):
        shutil.copytree(installed / 'source/src' / package, fixture / 'source/src' / package,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('pyproject.toml','uv.lock'):
        shutil.copyfile(installed / 'source' / name, fixture / 'source' / name)
    shutil.copyfile(installed / 'pipeline.json', fixture / 'pipeline.json')
    body = SCRIPT.read_text().split("$validation=@'\n", 1)[1].split("\n'@", 1)[0]
    # Test imports the newly repaired parser from source, but revision comparison
    # checks the real unchanged installed packages against their copied snapshot.
    # A code-only revision is allowed before recovery of a failed knowledge index.
    # The exact real parser/validation body must not instantiate KnowledgeService
    # or read/write its state while validating paths and installed code.
    code = "import sys;sys.path.insert(0," + repr(str(REPO / 'src')) + ")\n"
    code += "import cochem_pipeline.knowledge as knowledge\n"
    code += "def deny_knowledge_state(*args, **kwargs): raise AssertionError('Code-only validation touched knowledge service')\n"
    code += "knowledge.KnowledgeService.__init__=deny_knowledge_state\n" + body
    ps = "$ErrorActionPreference='Stop';$validation=@'\n" + code + "\n'@\n"
    ps += "& '" + str(installed / '.venv/Scripts/python.exe') + "' -I -B -c $validation '" + str(fixture) + "'\nexit $LASTEXITCODE"
    command = tmp_path / 'argv-fixture.ps1'; command.write_text(ps)
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(command)],
                            capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['revision']['verified'] and value['configuration_parsed']
    assert value['ram_workspace_root'] == 'R:\\CoChem427-windows-20261007'


@pytest.mark.parametrize('exit_code', [0, 7])
def test_actual_native_stderr_is_judged_by_exit_code_in_ps51(tmp_path, exit_code):
    python = Path(r'C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe')
    result = run(f"$script:targetRoot='{tmp_path}';$script:uv='{python}';$script:fixtureExit={exit_code};" + r'''
      function Get-FrozenSyncArguments { @('-I','-B','-c',("import sys; print('benign native stderr',file=sys.stderr); sys.exit("+$script:fixtureExit+")")) };
      $failed=$false;try{Invoke-FrozenRuntimeBuild}catch{$failed=$true};
      [ordered]@{failed=$failed;preference=[string]$ErrorActionPreference;log_contains_stderr=((Get-Content -LiteralPath (Join-Path $script:targetRoot 'frozen-build.log') -Raw) -match 'benign native stderr')}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'failed': exit_code != 0, 'preference':'Stop', 'log_contains_stderr':True}


@pytest.mark.parametrize('mutate', [False, True])
def test_post_build_source_rebinding_rejects_identical_source_and_installed_drift(tmp_path, mutate):
    source = tmp_path / 'source.py'; installed = tmp_path / 'installed.py'
    original = b'approved source\n'; changed = b'mutated source!\n'
    source.write_bytes(changed if mutate else original); installed.write_bytes(source.read_bytes())
    assert source.read_bytes() == installed.read_bytes()
    helper = REPO / 'scripts/stage_aetherdesk_427_payloads.ps1'
    result = run(f"$helper='{helper}';$file='{source}';$expected='{hashlib.sha256(original).hexdigest()}';$length={len(original)};" + r'''
      $tokens=$null;$errors=$null;$a=[Management.Automation.Language.Parser]::ParseFile($helper,[ref]$tokens,[ref]$errors);
      foreach($f in $a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in @('Initialize-FileIdentity','Assert-NoReparseAncestors','Open-VerifiedFile')},$true)){. ([scriptblock]::Create($f.Extent.Text))};Initialize-FileIdentity;
      $refused=$false;try{Assert-PostBuildFrozenFiles @([pscustomobject]@{destination=$file;sha256=$expected;length=$length})}catch{$refused=$true};
      @{refused=$refused}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] == mutate


@pytest.mark.parametrize('case', ['valid', 'second_change', 'wrong_fix', 'removed', 'added'])
def test_r3_manifest_accepts_only_reviewed_repair_from_pinned_r2(case):
    result = run(f"$case='{case}';" + r'''
      $prior=Get-Content -LiteralPath 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2\source-manifest.json' -Raw|ConvertFrom-Json;
      $current=Get-Content -LiteralPath 'REPLACE_MANIFEST' -Raw|ConvertFrom-Json;
      switch($case){
        'second_change'{$current.files[0].sha256='a'*64}
        'wrong_fix'{($current.files|Where-Object {$_.relative -eq 'src/cochem_pipeline/resource_limits.py'}).sha256='b'*64}
        'removed'{$current.files=@($current.files|Select-Object -Skip 1)}
        'added'{$current.files[0].relative='src/extra.py'}
      };
      $refused=$false;try{Assert-ReviewedR3Delta $current $prior}catch{$refused=$true};
      @{refused=$refused}|ConvertTo-Json -Compress
    '''.replace('REPLACE_MANIFEST', str(ROOT / 'stopped-runtime-r3-source-manifest.json')))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] == (case != 'valid')


@pytest.mark.parametrize('case', ['valid','missing','denied','running','instances','exit','nonce','principal','trigger'])
def test_original_failed_task_must_be_exact_and_terminal(case):
    result = run(f"$script:case='{case}';" + r'''
      $root='C:\Program Files\CoChem\WorkerDenial4.2.7-windows-20261006-slot1';
      $script:action=[pscustomobject]@{Type=0;Path='C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006\.venv\Scripts\python.exe';Arguments=('-I -B "'+$root+'\worker-denial-acceptance.py" --slot slot1 --nonce 06478e2f265546cd8257aa09111373f4');WorkingDirectory=$root};
      if($script:case -eq 'nonce'){$script:action.Arguments+='0'};
      $actions=[pscustomobject]@{Count=1};$actions|Add-Member ScriptMethod Item {param($i)$script:action};
      $script:task=[pscustomobject]@{State=$(if($script:case -eq 'running'){4}else{3});LastTaskResult=$(if($script:case -eq 'exit'){0}else{2});Definition=[pscustomobject]@{
        Principal=[pscustomobject]@{UserId=$(if($script:case -eq 'principal'){'ansac'}else{'SYSTEM'});LogonType=5;RunLevel=1};Triggers=[pscustomobject]@{Count=$(if($script:case -eq 'trigger'){1}else{0})};Actions=$actions}};
      $script:task|Add-Member ScriptMethod GetInstances {param($n)@{Count=$(if($script:case -eq 'instances'){1}else{0})}};
      $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($name)
        if($script:case -eq 'missing'){throw [Runtime.InteropServices.COMException]::new('missing',-2147024894)};
        if($script:case -eq 'denied'){throw [Runtime.InteropServices.COMException]::new('denied',-2147024891)};
        $script:task};
      $refused=$false;try{Assert-PreservedDenialTask $folder}catch{$refused=$true};
      @{refused=$refused}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] == (case != 'valid')


def test_held_text_reader_disposal_retains_file_handle(tmp_path):
    control = tmp_path / 'control.json'; control.write_bytes(b'{"safe":true}')
    result = run(f"$path='{control}';" + r'''
      $stream=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);
      try{$first=Read-HeldText $stream;$second=Read-HeldText $stream;
        $refused=$false;try{$w=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::Read);$w.Dispose()}catch{$refused=$true};
        @{repeat=($first -ceq $second);write_denied=$refused;readable=$stream.CanRead}|ConvertTo-Json -Compress
      }finally{$stream.Dispose()}
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'repeat': True, 'write_denied': True, 'readable': True}


@pytest.mark.parametrize('case', ['success','source_drift','existing_root','failed_task','build_failure'])
def test_exact_apply_branch_real_control_copies_and_stops_on_failure(tmp_path, case):
    """Execute the actual Apply function: real temp source/control copies.

    Explicit boundaries replace administrator ACL creation, task COM access,
    package build and installed-runtime execution. No privileged operation runs.
    """
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    for name in ('pipeline.json', 'windows-layout.json', 'manifest.json', 'source.py'):
        (inputs / name).write_bytes(('fixture ' + name + '\n').encode())
    target = tmp_path / 'new-runtime'
    if case == 'existing_root':
        target.mkdir(); (target / 'preserve.txt').write_bytes(b'untouched')
    result = run(f"$case='{case}';$fixture='{tmp_path}';$inputs='{inputs}';$script:targetRoot='{target}';" + r'''
      $script:setupMutexName='Local\CoChem-R3-Inert-'+[Guid]::NewGuid().ToString('N');
      $script:sourceRoot=Join-Path $script:targetRoot 'source';$script:oldRoot=$inputs;
      $script:oldConfig=Join-Path $inputs 'pipeline.json';$script:layout=Join-Path $inputs 'windows-layout.json';
      $script:configHash=(Get-FileHash $script:oldConfig).Hash.ToLowerInvariant();$script:layoutHash=(Get-FileHash $script:layout).Hash.ToLowerInvariant();
      $manifest=Join-Path $inputs 'manifest.json';$manifestHash=(Get-FileHash $manifest).Hash.ToLowerInvariant();
      $controls=@(New-ControlCopyRecords $manifest $manifestHash);
      if($controls.Count -ne 3 -or @($controls|Where-Object {$_ -is [string]}).Count){throw 'Copy records flattened'};
      $source=Join-Path $inputs 'source.py';$files=@([pscustomobject]@{source=$source;destination=(Join-Path $script:sourceRoot 'src\package\source.py');sha256=(Get-FileHash $source).Hash.ToLowerInvariant();length=(Get-Item $source).Length});
      $script:python='C:\fixture-base\python.exe';$script:uv='C:\fixture-uv\uv.exe';$script:pins=@();
      $script:copied=[Collections.Generic.List[string]]::new();$script:builds=0;$script:validations=0;$script:taskChecks=0;
      function Assert-StoppedRuntimeTasks {param($Folder)};
      function Assert-PreservedDenialTask {param($Folder)$script:taskChecks++;if($case -eq 'failed_task'){throw 'fixture nonterminal'}};
      function Assert-CodeTreeOnce {param($Path)7};function Assert-ProtectedPath {param($Path)};
      function New-ProtectedDirectory {param($Path)$null=[IO.Directory]::CreateDirectory($Path)};
      function Protect-NewRuntimeTree {param($Path)};
      function Open-VerifiedFile {param($Path,$Hash,$Length)
        $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read);
        $sha=[Security.Cryptography.SHA256]::Create();
        try{$actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()};
        if($actual -cne $Hash -or $stream.Length -ne $Length){$stream.Dispose();throw 'fixture binding differs'};
        $stream.Position=0;return $stream
      };
      function Copy-VerifiedPayload {param($File)
        # Real exclusive temp copies exercise every exact record consumed by
        # the Apply branch; production ACL implementation is separately reviewed.
        $sourceStream=Open-VerifiedFile $File.source $File.sha256 $File.length;
        try{$dest=[IO.File]::Open($File.destination,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);
          try{$sourceStream.CopyTo($dest);$dest.Flush($true)}finally{$dest.Dispose()}
        }finally{$sourceStream.Dispose()};$script:copied.Add($File.destination)
      };
      function Write-NewRuntimeReport {param($Path,$Value)[IO.File]::WriteAllText($Path,($Value|ConvertTo-Json -Depth 12))};
      function Invoke-FrozenRuntimeBuild {$script:builds++;if($case -eq 'build_failure'){throw 'fixture build failed'};
        if($case -eq 'source_drift'){[IO.File]::AppendAllText($files[0].destination,'changed by fixture build')}};
      function Invoke-NewRuntimeValidation {param($Root)$script:validations++;[pscustomobject]@{revision=[pscustomobject]@{verified=$true};configuration_parsed=$true}};
      function Assert-NewRuntimeInterpreter {param($Root)[pscustomobject]@{fixture=$true}};
      $plan=[ordered]@{mode='READ_ONLY_PLAN';holds=@();preserved_denial_task_terminal_check_deferred=$true};
      $failed=$false;try{$null=Invoke-NewRuntimeInstallation $files $controls $null $plan}catch{$failed=$true};
      $hashes=@();foreach($record in $controls){if(Test-Path -LiteralPath $record.destination){$hashes+=((Get-FileHash $record.destination).Hash.ToLowerInvariant() -ceq $record.sha256)}};
      @{failed=$failed;copied=$script:copied.Count;controls_correct=@($hashes|Where-Object {$_ -eq $true}).Count;builds=$script:builds;validations=$script:validations;task_checks=$script:taskChecks;after=(Test-Path -LiteralPath (Join-Path $script:targetRoot 'install-after.json'));mode=$plan.mode}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value['failed'] == (case != 'success')
    before_copy = case in ('existing_root', 'failed_task')
    assert value['copied'] == (0 if before_copy else 4)
    assert value['controls_correct'] == (0 if before_copy else 3)
    assert value['builds'] == (0 if before_copy else 1)
    assert value['validations'] == (1 if case == 'success' else 0)
    assert value['after'] == (case == 'success')
    if case == 'existing_root':
        assert (target / 'preserve.txt').read_bytes() == b'untouched'
    assert all((inputs / name).read_bytes() == ('fixture ' + name + '\n').encode()
               for name in ('pipeline.json','windows-layout.json','manifest.json','source.py'))


@pytest.mark.parametrize('case', ['valid','base','version','system_site','extra','duplicate','legacy'])
def test_exact_actual_uv_venv_format_and_policy(case):
    result = run(f"$case='{case}';" + r'''
      $script:python='C:\Program Files\CoChem\Toolchain4.2.7-windows-20261006\Python312\python.exe';
      $text=Get-Content -LiteralPath 'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r2\.venv\pyvenv.cfg' -Raw;
      switch($case){
        'base'{$text=$text.Replace('Python312','WrongBase')}
        'version'{$text=$text.Replace('3.12.13','3.12.12')}
        'system_site'{$text=$text.Replace('false','true')}
        'extra'{$text+="`nexecutable = C:\unexpected\python.exe"}
        'duplicate'{$text+="`nimplementation = CPython"}
        'legacy'{$text=$text.Replace('version_info','version')}
      };
      $refused=$false;try{Assert-UvVenvText $text}catch{$refused=$true};
      @{refused=$refused}|ConvertTo-Json -Compress
    ''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['refused'] == (case != 'valid')
