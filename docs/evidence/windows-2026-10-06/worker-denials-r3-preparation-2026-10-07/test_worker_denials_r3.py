"""Inert protocol/ACL/guard fixtures; no kernel device, worker, SYSTEM or task run."""
import ast
import copy
import ctypes as C
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location('worker_denial', ROOT / 'worker-denial-acceptance-r3.py')
denial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(denial)
NONCE = 'a' * 32
SID = 'S-1-5-21-1-2-3-1001'


def test_r3_frozen_manifest_validates_all_actual_source_bytes():
    import hashlib
    raw=(ROOT/'stopped-runtime-r3-source-manifest.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==denial.MANIFEST_SHA256
    manifest=json.loads(raw);seen=[]
    repo=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
    def read(path,pin,length):
        relative=path.relative_to(denial.INSTALL/'source')
        data=(repo/relative).read_bytes()
        assert len(data)==length and hashlib.sha256(data).hexdigest()==pin
        seen.append(relative.as_posix())
    denial.verify_r3_source(manifest,read)
    assert len(seen)==166 and 'src/cochem_pipeline/resource_limits.py' in seen


@pytest.mark.parametrize('change',['traversal','duplicate','wrong_root','missing_row','unfrozen'])
def test_r3_manifest_rejects_unsafe_or_incomplete_source_bindings(change):
    value=json.loads((ROOT/'stopped-runtime-r3-source-manifest.json').read_bytes())
    if change=='traversal':value['files'][0]['relative']='src/../../outside.py'
    elif change=='duplicate':value['files'][-1]=value['files'][0].copy()
    elif change=='wrong_root':value['target_root']='C:\\wrong'
    elif change=='missing_row':value['files'].pop()
    else:value['complete_source_freeze']=False
    with pytest.raises(ValueError):denial.verify_r3_source(value,lambda *args:None)


def passing_result():
    rows = {key: {'opened': False, 'winerror': 5, 'classification': 'DENIED'} for key in denial.targets_for('slot1')}
    rows.update({'device_' + key: {'opened': False, 'winerror': 5, 'classification': 'DENIED'} for key in denial.DEVICE_CASES})
    rows['own_workspace'] = {'opened': True, 'winerror': 0, 'classification': 'OPENED_AND_CLOSED'}
    return {'schema': 'cochem-worker-handle-denial/1', 'nonce': NONCE, 'slot': 'slot1', 'pid': 12345,
            'token': {'sid': SID, 'elevated': False, 'administrators_enabled': False},
            'handles': rows, 'ioctls_sent': 0, 'contents_read': 0}


def validate(value):
    return denial.validate_child_result(json.dumps(value).encode(), NONCE, 'slot1', SID, 12345)


def test_complete_denials_require_actual_own_workspace_control():
    result = passing_result()
    assert validate(result)[1]
    result['handles']['own_workspace'] = {'opened': False, 'winerror': 5, 'classification': 'DENIED'}
    assert not validate(result)[1]


@pytest.mark.parametrize('error', [2, 3, 32, 87, 121, 231])
def test_absence_sharing_and_other_errors_never_count_as_denial(error):
    result = passing_result()
    result['handles']['device_lhm_mask3'] = {'opened': False, 'winerror': error, 'classification': 'INCONCLUSIVE'}
    assert not validate(result)[1]


@pytest.mark.parametrize('key', ['device_lhm_mask3', 'device_generic_read_write', 'device_zero_access',
                                  'device_zero_access_overlapped', 'controller', 'private', 'sibling_slot6'])
def test_any_inappropriate_handle_success_holds_acceptance(key):
    result = passing_result()
    result['handles'][key] = {'opened': True, 'winerror': 0, 'classification': 'OPENED_AND_CLOSED'}
    assert not validate(result)[1]


@pytest.mark.parametrize('field,value', [('nonce', 'b'*32), ('slot', 'slot2'), ('pid', 999),
                                        ('ioctls_sent', 1), ('contents_read', 1), ('contents_read', False)])
def test_receipt_binding_and_no_io_claims_are_strict(field, value):
    result = passing_result(); result[field] = value
    with pytest.raises(ValueError):
        validate(result)


@pytest.mark.parametrize('token', [{'sid': 'S-1-5-18', 'elevated': False, 'administrators_enabled': False},
                                  {'sid': SID, 'elevated': True, 'administrators_enabled': False},
                                  {'sid': SID, 'elevated': False, 'administrators_enabled': True},
                                  {'sid': SID, 'elevated': 0, 'administrators_enabled': False}])
def test_privileged_wrong_or_ambiguous_worker_token_rejected(token):
    result = passing_result(); result['token'] = token
    with pytest.raises(ValueError):
        validate(result)


def test_missing_extra_duplicate_oversized_and_contradictory_receipts_rejected():
    for mutation in ('missing', 'extra', 'contradictory'):
        result = passing_result()
        if mutation == 'missing':
            del result['handles']['sibling_slot6']
        elif mutation == 'extra':
            result['handles']['secret_file'] = result['handles']['private']
        else:
            result['handles']['private']['classification'] = 'OPENED_AND_CLOSED'
        with pytest.raises(ValueError):
            validate(result)
    raw = json.dumps(passing_result()).encode().replace(b'"pid": 12345', b'"pid": 12345, "pid": 12345')
    with pytest.raises(ValueError):
        denial.validate_child_result(raw, NONCE, 'slot1', SID, 12345)
    with pytest.raises(ValueError):
        denial.validate_child_result(b' ' * 65537, NONCE, 'slot1', SID, 12345)


def test_handle_attempt_never_reads_writes_or_sends_ioctl_and_always_closes_success():
    calls = []
    def create(*args):
        calls.append(args)
        return 123
    closed = []
    api = SimpleNamespace(k=SimpleNamespace(CreateFileW=create, CloseHandle=lambda h: closed.append(h) or True),
                          checked=lambda value: None if value else pytest.fail('bad close'))
    assert denial.attempt_handle(api, denial.DEVICE, 3, 128) == {
        'opened': True, 'winerror': 0, 'classification': 'OPENED_AND_CLOSED'}
    assert calls == [(denial.DEVICE, 3, 3, None, 3, 128, None)]
    assert closed == [123]
    def refused(*args):
        C.set_last_error(5)
        return C.c_void_p(-1).value
    api.k.CreateFileW = refused
    assert denial.attempt_handle(api, denial.DEVICE, 0, 0x40000000)['classification'] == 'DENIED'
    assert closed == [123]


def acceptable_acl():
    return {'owner_sid': 'S-1-5-18', 'dacl_present': True, 'protected': True, 'aces': [
        {'type': 'allow', 'sid': 'S-1-5-18', 'mask': 0x10000000, 'flags': 0},
        {'type': 'allow', 'sid': 'S-1-5-32-544', 'mask': 0x10000000, 'flags': 0}]}


def test_device_acl_fails_closed_for_null_unprotected_unknown_and_untrusted_grants():
    assert denial.acl_acceptable(acceptable_acl())
    for change in ('null', 'unprotected', 'unknown_ace', 'world', 'empty', 'owner_untrusted', 'owner_missing'):
        value = acceptable_acl()
        if change == 'null': value['dacl_present'] = False
        elif change == 'unprotected': value['protected'] = False
        elif change == 'unknown_ace': value['aces'][0]['type'] = 'callback_allow'
        elif change == 'world': value['aces'][1]['sid'] = 'S-1-1-0'
        elif change == 'owner_untrusted': value['owner_sid'] = SID
        elif change == 'owner_missing': del value['owner_sid']
        else: value['aces'] = []
        assert not denial.acl_acceptable(value)


def test_device_acl_oversize_closes_query_handle_without_allocating_or_using_device():
    closes = []
    def query(handle, info, target, size, output_size):
        assert info == 5 and size == 0 and target is None
        C.cast(output_size, C.POINTER(denial.W.DWORD))[0] = 65537
        return False
    api = SimpleNamespace(k=SimpleNamespace(CreateFileW=lambda *args: 123,
                          CloseHandle=lambda h: closes.append(h) or True),
                          a=SimpleNamespace(GetKernelObjectSecurity=query), checked=lambda value: None)
    with pytest.raises(ValueError, match='size'):
        denial.device_acl(api)
    assert closes == [123]


def test_sanitized_failure_preserves_phase_type_and_numeric_cause_without_messages():
    secret = 'credential-or-content-must-never-appear'
    underlying = OSError(secret)
    underlying.winerror = 50
    wrapped = RuntimeError(secret)
    wrapped.__cause__ = underlying
    value = denial.safe_failure(wrapped, 'device_query')
    assert value == {'phase': 'device_query', 'error_type': 'RuntimeError', 'winerror': 50}
    assert secret not in json.dumps(value)
    assert denial.safe_failure(wrapped, secret)['phase'] == 'trusted_preflight'


def test_layout_failure_is_saved_in_early_exclusive_receipt_without_device_or_worker(monkeypatch, tmp_path):
    import sys
    package = SimpleNamespace()
    root = tmp_path/'WorkerDenial4.2.7-windows-20261007-r3-slot1'
    root.mkdir()
    script = root/'worker-denial-acceptance-r3.py'
    script.write_text('fixture', encoding='utf-8')
    class FakeCleanupError(Exception):
        pass
    closed = []
    fake = SimpleNamespace(require_system=lambda: None, validate_code_path=lambda p: None,
                           __file__=str(tmp_path/'windows.py'), SYSTEM_SID='S-1-5-18',
                           WindowsCleanupError=FakeCleanupError, _close=lambda h: closed.append(h),
                           _check=lambda *a: None,
                           _api=lambda: {'kernel32': SimpleNamespace(CreateMutexW=lambda *a: 123)})
    package.windows = fake
    monkeypatch.setitem(sys.modules, 'cochem_pipeline', package)
    monkeypatch.setitem(sys.modules, 'cochem_pipeline.ramdisk', SimpleNamespace(ordinary_tree=lambda p: None))
    monkeypatch.setitem(sys.modules, 'cochem_pipeline.resource_limits', SimpleNamespace(ResourceLimits=object))
    original_path = Path
    monkeypatch.setattr(denial, 'Path', lambda value: tmp_path if str(value) == r'C:\Program Files\CoChem' else original_path(value))
    monkeypatch.setattr(denial, '__file__', str(script))
    monkeypatch.setattr(denial, 'PYTHON', original_path(sys.executable).resolve())
    def digest(path):
        if path == original_path(fake.__file__): return denial.WINDOWS_SHA256
        if path == denial.BASE_PYTHON: return denial.BASE_SHA256
        if path == denial.DRIVER: return denial.DRIVER_SHA256
        return 'a'*64
    monkeypatch.setattr(denial, 'digest_file', digest)
    monkeypatch.setattr(denial, 'verify_r3_runtime', lambda win, receipt_hash: {'verified': True})
    def fail_layout(*a):
        error = OSError('sensitive-message-not-published'); error.winerror = 2
        raise error
    monkeypatch.setattr(denial, 'protected_json', fail_layout)
    monkeypatch.setattr(denial, 'Kernel', lambda: pytest.fail('Device API must not be initialized'))
    assert denial.run('slot1', NONCE, 'b'*64) == 2
    receipt = root/'worker-denial-acceptance.json'
    record = json.loads(receipt.read_text())
    assert record['failure'] == {'phase': 'layout', 'error_type': 'OSError', 'winerror': 2}
    assert not record['worker_dispatch_attempted'] and not record['worker_released']
    assert 'sensitive-message-not-published' not in receipt.read_text()
    assert closed == [123]
    before = receipt.read_bytes()
    with pytest.raises(FileExistsError): denial.run('slot1', NONCE, 'b'*64)
    assert receipt.read_bytes() == before


def layout_fixture():
    slots = {f'slot{i}': {'identity': f'CoChem422Worker{i}', 'credential_target': f'CoChem422/slot{i}',
                         'root': denial.targets_for(f'slot{i}')['own_workspace'][0],
                         'sid': f'S-1-5-21-1-2-3-{1000+i}'} for i in range(1, 7)}
    layout = {'slots': slots, 'private_root': r'C:\ProgramData\CoChemPipeline427\private', 'operator_name': r'AETHERDESK\ansac'}
    config = {'workers': {key: {'name': row['identity'], 'credential_target': row['credential_target']} for key, row in slots.items()},
              'slot_roots': {key: row['root'] for key, row in slots.items()}, 'private_root': layout['private_root'],
              'operator_name': layout['operator_name'], 'token_file': r'C:\Users\ansac\CoChem427\controller.token',
              'max_execution_slots': 4}
    return layout, config


@pytest.mark.parametrize('change', ['capacity', 'sid', 'root', 'target', 'missing'])
def test_six_identity_four_capacity_layout_refuses_drift(change):
    layout, config = layout_fixture(); denial.validate_layout(layout, config)
    if change == 'capacity': config['max_execution_slots'] = 6
    elif change == 'sid': layout['slots']['slot6']['sid'] = layout['slots']['slot1']['sid']
    elif change == 'root': config['slot_roots']['slot1'] = config['slot_roots']['slot2']
    elif change == 'target': config['workers']['slot1']['credential_target'] = 'CoChem422/slot2'
    else: del layout['slots']['slot6']
    with pytest.raises(ValueError): denial.validate_layout(layout, config)


@pytest.mark.parametrize('name', ['CoChem-4.2.7-Warden', 'CoChem-4.2.7-Supervisor'])
@pytest.mark.parametrize('enabled,state,instances', [(True, 3, 0), (False, 4, 0), (False, 2, 0), (False, 0, 0), (False, 3, 1)])
def test_real_powershell_guard_body_rejects_actual_daemons_with_fake_scheduler(name, enabled, state, instances):
    def guarded(script, names):
        prefix = ("$ErrorActionPreference='Stop';$data=ConvertFrom-Json '"+json.dumps(names)+"';"
                  f"$script:blocked='{name}';$script:enabled=${str(enabled).lower()};$script:state={state};$script:instances={instances};")
        prefix += r'''
            $script:folder=[pscustomobject]@{};
            $script:folder|Add-Member ScriptMethod GetTask {param($name)
                $t=if($name -eq $script:blocked){[pscustomobject]@{Enabled=$script:enabled;State=$script:state;Instances=$script:instances}}else{[pscustomobject]@{Enabled=$false;State=1;Instances=0}};
                $t|Add-Member ScriptMethod GetInstances {param($flags)[pscustomobject]@{Count=$this.Instances}};return $t};
            $script:scheduler=[pscustomobject]@{};
            $script:scheduler|Add-Member ScriptMethod Connect {};
            $script:scheduler|Add-Member ScriptMethod GetFolder {param($path)return $script:folder};
            function New-Object {param($ComObject)if($ComObject -ne 'Schedule.Service'){throw 'Unexpected COM'};return $script:scheduler};
        '''
        exe = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-Command', prefix+script],
                                capture_output=True, text=True, timeout=15)
        assert result.returncode != 0
        assert 'Protected daemons must remain stopped and disabled' in result.stderr
        raise RuntimeError('expected guard refusal')
    with pytest.raises(RuntimeError, match='expected guard'):
        denial.require_stopped(SimpleNamespace(_powershell=guarded))


def test_helper_has_no_device_io_content_io_or_driver_loading_entrypoints():
    tree = ast.parse((ROOT/'worker-denial-acceptance-r3.py').read_text())
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls & {'DeviceIoControl', 'ReadFile', 'WriteFile', 'LoadLibraryW', 'StartServiceW', 'CreateServiceW'}
    assert denial.DEVICE == r'\\?\GLOBALROOT\Device\PawnIO'
    assert denial.DEVICE_CASES['lhm_mask3'][0] == 3
    assert len(denial.targets_for('slot1')) == 8


def powershell(code):
    exe = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    prefix = "$ErrorActionPreference='Stop';Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Utility\\Microsoft.PowerShell.Utility.psd1');"
    return subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-Command', prefix+code],
                          capture_output=True, text=True, timeout=15)


def wrapper_ast():
    path = str(ROOT/'check-worker-denials-r3.ps1').replace("'", "''")
    return ("$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('"+path+
            "',[ref]$tokens,[ref]$errors);if($errors.Count){throw 'Wrapper parse failed'};")


def test_wrapper_wait_and_exact_absence_errors_using_inert_objects():
    code = wrapper_ast() + r'''
        foreach($name in @('Wait-DenialTaskInstance','Get-ExactTaskOrAbsent')) {
            $function=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true);
            if($null -eq $function){throw 'Actual function missing'};. ([scriptblock]::Create($function.Extent.Text))
        }
        $instance=[pscustomobject]@{State=4};
        $instance|Add-Member ScriptMethod Refresh {throw [Runtime.InteropServices.COMException]::new('completed',-2147216629)};
        Wait-DenialTaskInstance $instance;
        $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($name)throw [Runtime.InteropServices.COMException]::new('missing',-2147024894)};
        if($null -ne (Get-ExactTaskOrAbsent 'fixture')){throw 'Missing result differs'};
        $folder=[pscustomobject]@{};$folder|Add-Member ScriptMethod GetTask {param($name)throw [Runtime.InteropServices.COMException]::new('account information absent',-2147216625)};
        $refused=$false;try{$null=Get-ExactTaskOrAbsent 'fixture'}catch{$refused=$true};
        if(-not$refused){throw 'Account information error treated as absent'};
        $instance=[pscustomobject]@{State=4};$instance|Add-Member ScriptMethod Refresh {throw [Runtime.InteropServices.COMException]::new('denied',-2147024891)};
        $refused=$false;try{Wait-DenialTaskInstance $instance}catch{$refused=$true};if(-not$refused){throw 'Access denied swallowed'};
        $instance=[pscustomobject]@{State=4};$instance|Add-Member ScriptMethod Refresh {};
        $bounded=$false;try{Wait-DenialTaskInstance $instance -TimeoutSeconds 0}catch{$bounded=$_.Exception.Message -like '*timed out*'};
        if(-not$bounded){throw 'Running instance unbounded'};'fixtures-passed'
    '''
    result = powershell(code)
    assert result.returncode == 0, result.stderr
    assert 'fixtures-passed' in result.stdout


@pytest.mark.parametrize('name', ['CoChem-4.2.7-Warden', 'CoChem-4.2.7-Supervisor'])
def test_actual_wrapper_daemon_loop_holds_each_new_name(name):
    code = wrapper_ast()+f"$script:blocked='{name}';"+r'''
        $loop=$ast.Find({param($n)$n -is [Management.Automation.Language.ForEachStatementAst] -and $n.Extent.Text.Contains('CoChem-4.2.7-Warden')},$true);
        function Get-ExactTaskOrAbsent {param($Name)$t=[pscustomobject]@{Enabled=($Name -eq $script:blocked);State=1};$t|Add-Member ScriptMethod GetInstances {param($flags)[pscustomobject]@{Count=0}};return $t};
        $holds=@();. ([scriptblock]::Create($loop.Extent.Text));
        if($holds.Count -ne 1 -or -not$holds[0].Contains($script:blocked)){throw 'Actual daemon was not held'};'guard-passed'
    '''
    result = powershell(code)
    assert result.returncode == 0, result.stderr


def test_nonadmin_apply_refuses_before_any_runtime_or_protected_action():
    code = ("$i=[Security.Principal.WindowsIdentity]::GetCurrent();"
            "if([Security.Principal.WindowsPrincipal]::new($i).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Test requires nonadmin'};"
            "& '"+str(ROOT/'check-worker-denials-r3.ps1').replace("'", "''")+"' -Slot slot1 -Apply")
    result = powershell(code)
    assert result.returncode != 0
    assert '-Apply requires the owner in Administrator Windows PowerShell; nothing was executed' in result.stderr


def test_reviewed_helper_digest_pin_matches_wrapper_bytes():
    import hashlib
    digest = hashlib.sha256((ROOT/'worker-denial-acceptance-r3.py').read_bytes()).hexdigest()
    assert "$sourceHash='"+digest+"'" in (ROOT/'check-worker-denials-r3.ps1').read_text()


@pytest.mark.parametrize('state,instances,pass_expected', [(1,0,True),(3,0,True),(0,0,False),(2,0,False),(4,0,False),(3,1,False)])
def test_registered_task_requires_known_terminal_state_and_zero_instances(state, instances, pass_expected):
    code=wrapper_ast()+f"$state={state};$instances={instances};"+r'''
        $fn=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-CompletedDenialTask'},$true);
        . ([scriptblock]::Create($fn.Extent.Text));
        $task=[pscustomobject]@{State=$state;Instances=$instances};$task|Add-Member ScriptMethod GetInstances {param($flags)[pscustomobject]@{Count=$this.Instances}};
        $passed=$false;try{Assert-CompletedDenialTask $task;$passed=$true}catch{};
        [ordered]@{passed=$passed}|ConvertTo-Json -Compress
    '''
    result=powershell(code)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['passed'] is pass_expected


@pytest.mark.parametrize('case', ['ordinary_and_db', 'hardlink', 'untrusted_acl'])
def test_tree_once_checks_every_child_and_metadata_handle_without_rewalking_ancestors(tmp_path, case):
    tree = tmp_path/'tree'; tree.mkdir()
    (tree/'check.py').write_text('fixture', encoding='utf-8')
    (tree/'bundled.db').write_bytes(b'fixture-database-bytes-not-read')
    if case == 'hardlink': os.link(tree/'check.py', tree/'alias.py')
    helper = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1')
    code = wrapper_ast()+f"$script:case='{case}';$fixture='{str(tree).replace(chr(39), chr(39)*2)}';"
    code += r'''
        $fn=$ast.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Assert-CodeTreeOnce'},$true);
        . ([scriptblock]::Create($fn.Extent.Text));
        $script:ancestry=0;$script:ownAcls=0;
        function Assert-ProtectedPath {param($Path)$script:ancestry++};
        function Get-Acl {param($LiteralPath)
            $script:ownAcls++;$bad=($script:case -eq 'untrusted_acl' -and $LiteralPath.EndsWith('bundled.db'));
            $a=[pscustomobject]@{Bad=$bad};
            $a|Add-Member ScriptMethod GetOwner {param($type)[Security.Principal.SecurityIdentifier]::new('S-1-5-18')};
            $a|Add-Member ScriptMethod GetAccessRules {param($explicit,$inherited,$type)
                if($this.Bad){[pscustomobject]@{AccessControlType='Allow';IdentityReference=[Security.Principal.SecurityIdentifier]::new('S-1-1-0');PropagationFlags=0;FileSystemRights=2032127}}};
            return $a
        };
    '''
    code += "$helperAst=[Management.Automation.Language.Parser]::ParseFile('"+str(helper)+"',[ref]$tokens,[ref]$errors);"
    code += r'''
        $init=$helperAst.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Initialize-FileIdentity'},$true);
        . ([scriptblock]::Create($init.Extent.Text));Initialize-FileIdentity;
        $refused=$false;$count=0;try{$count=Assert-CodeTreeOnce $fixture}catch{$refused=$true};
        [ordered]@{refused=$refused;count=$count;ancestry=$script:ancestry;own_acls=$script:ownAcls}|ConvertTo-Json -Compress
    '''
    result = powershell(code)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record['ancestry'] == 1
    if case == 'ordinary_and_db':
        assert record == {'refused': False, 'count': 3, 'ancestry': 1, 'own_acls': 3}
    else:
        assert record['refused'] is True
