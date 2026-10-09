"""Windows inert fixtures only; no native provider, task, account or login runs."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
from types import SimpleNamespace

import pytest

REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
PYTHON = Path(__file__).parent / 'worker_claude_login_bridge_r3.py'
WRAPPER = Path(__file__).parent / 'login_pipeline_worker_interactive_r3.ps1'
spec = importlib.util.spec_from_file_location('bridge', PYTHON)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
NONCE = 'a' * 32


def packet(code=b'fixture-code', nonce=NONCE):
    return b'cochem-login-input/1\n' + nonce.encode() + b'\n' + code + b'\n'


@pytest.mark.parametrize('code', [b'fixture-code', b'x' * 2048, 'fixture-測試'.encode()])
def test_packet_exact_bound_and_utf8(code):
    assert bridge.parse_packet(packet(code), NONCE) == code


@pytest.mark.parametrize('raw', [b'', packet(b''), packet(b'x'*2049), packet(b'a\nb'), packet(b'a\rb'),
    packet(b'a\0b'), packet(b'\xff'), packet(nonce='b'*32), packet()[:-1], packet()+b'\n'])
def test_packet_refuses_unbound_or_multiline_values_without_echo(raw):
    with pytest.raises((ValueError, UnicodeError)) as error:
        bridge.parse_packet(raw, NONCE)
    assert 'fixture-code' not in str(error.value)


def test_cancel_exact_value_only():
    assert bridge.parse_packet(packet(b'CANCEL'), NONCE, cancel=True) == b'CANCEL'
    with pytest.raises(ValueError):
        bridge.parse_packet(packet(), NONCE, cancel=True)


def test_native_handle_reads_single_link_and_deletes_only_after_request(tmp_path):
    # Synthetic non-secret file. ACL is deliberately a separate fixture boundary.
    path = tmp_path / 'input.once'; path.write_bytes(packet())
    checked = []
    item = bridge.ChannelFile(path, SimpleNamespace(validate_private_path=lambda p: checked.append(p)))
    assert checked == [path] and item.raw == packet() and path.exists()
    # The held handle denies both write and pathname replacement/deletion.
    with pytest.raises(PermissionError):
        path.write_bytes(b'replaced')
    with pytest.raises(PermissionError):
        path.unlink()
    item.delete_after_verified_cleanup()
    assert not path.exists()


def test_native_handle_failed_cleanup_preserves_file(tmp_path):
    path = tmp_path / 'input.once'; path.write_bytes(packet())
    item = bridge.ChannelFile(path, SimpleNamespace(validate_private_path=lambda p: None))
    item.close()
    assert path.read_bytes() == packet()


def test_native_handle_rejects_hardlinks(tmp_path):
    path = tmp_path / 'input.once'; path.write_bytes(packet())
    os.link(path, tmp_path / 'second')
    with pytest.raises(ValueError, match='single-link'):
        bridge.ChannelFile(path, SimpleNamespace(validate_private_path=lambda p: None))


class Writer(io.BytesIO):
    def close(self):
        self.captured = self.getvalue()
        super().close()


def test_feed_is_one_line_flushed_then_closed():
    stream = Writer(); bridge.feed_once(stream, b'synthetic-code')
    assert stream.closed and stream.captured == b'synthetic-code\n'


def test_partial_stdin_write_fails_without_retry_and_closes():
    class ShortWriter(Writer):
        def write(self,value):
            return super().write(value[:3])
    stream=ShortWriter()
    with pytest.raises(OSError,match='incomplete line'):
        bridge.feed_once(stream,b'synthetic-code')
    assert stream.closed and stream.captured==b'syn'


def test_actual_windows_anonymous_pipe_accepts_maximum_line_without_reader():
    r, w = os.pipe()
    try:
        bridge.feed_once(os.fdopen(w, 'wb', buffering=0), b'x'*2048)
        assert os.read(r, 4096) == b'x'*2048+b'\n'
    finally:
        os.close(r)


def test_native_echo_after_submission_never_reaches_log(tmp_path, monkeypatch):
    (tmp_path/'input.once').write_bytes(packet(b'echo-me'))
    monkeypatch.setattr(bridge, 'ChannelFile', lambda path, win: SimpleNamespace(raw=path.read_bytes()))
    events = queue.Queue(); events.put(b'echo-me\n')
    log = io.BytesIO(); writer = Writer(); report = {}; channels = []
    result = bridge.run_loop(SimpleNamespace(poll=lambda: 0), writer, events, log, tmp_path, NONCE, None, report, channels)
    assert result == 0 and writer.captured == b'echo-me\n'
    assert log.getvalue() == b'' and report == {'one_line_submitted': True}


def test_preinput_browser_instructions_can_be_published(tmp_path):
    events = queue.Queue(); events.put(b'Visit the provider browser URL\n')
    log = io.BytesIO(); writer = Writer(); report = {}
    result = bridge.run_loop(SimpleNamespace(poll=lambda: 0), writer, events, log, tmp_path, NONCE, None, report, [])
    assert result == 0 and log.getvalue() == b'Visit the provider browser URL\n'
    assert report == {} and not writer.closed
    writer.close()


def test_cancel_never_feeds_provider(tmp_path, monkeypatch):
    (tmp_path/'cancel.request').write_bytes(packet(b'CANCEL'))
    monkeypatch.setattr(bridge, 'ChannelFile', lambda path, win: SimpleNamespace(raw=path.read_bytes()))
    writer = Writer(); report = {}
    with pytest.raises(InterruptedError):
        bridge.run_loop(None, writer, queue.Queue(), io.BytesIO(), tmp_path, NONCE, None, report, [])
    assert writer.getvalue() == b'' and report == {'operator_cancelled': True}


def test_login_deadline_expires_without_waiting_for_native(tmp_path, monkeypatch):
    clock=iter([0,601]);monkeypatch.setattr(bridge.time,'monotonic',lambda:next(clock))
    with pytest.raises(TimeoutError):
        bridge.run_loop(None,Writer(),queue.Queue(),io.BytesIO(),tmp_path,NONCE,None,{},[])


def test_output_limit_fails_before_log_publication(tmp_path):
    events=queue.Queue();events.put(b'x'*(bridge.MAX_OUTPUT+1));log=io.BytesIO()
    with pytest.raises(ValueError,match='output exceeded'):
        bridge.run_loop(None,Writer(),events,log,tmp_path,NONCE,None,{},[])
    assert log.getvalue()==b''


@pytest.mark.parametrize('fault', ['job', 'reader', 'daemon', 'none'])
def test_channel_deletion_requires_terminal_job_and_reader_proof(fault):
    events = []
    def close():
        events.append('job-proof')
        if fault == 'job':
            raise RuntimeError('synthetic unverified cleanup')
    def postcheck():
        events.append('daemon-proof')
        if fault == 'daemon':
            raise RuntimeError('synthetic daemon uncertainty')
    channel = SimpleNamespace(delete_after_verified_cleanup=lambda: events.append('delete'))
    thread = SimpleNamespace(join=lambda timeout: events.append('reader-join'), is_alive=lambda: fault == 'reader')
    report = {'cleanup_verified':False, 'input_file_deleted':False}
    if fault != 'none':
        with pytest.raises(RuntimeError):
            bridge.cleanup_session(SimpleNamespace(close=close), Writer(), threading.Event(), [thread], [channel], report, postcheck)
        assert 'delete' not in events
    else:
        bridge.cleanup_session(SimpleNamespace(close=close), Writer(), threading.Event(), [thread], [channel], report, postcheck)
        assert events == ['job-proof','reader-join','daemon-proof','delete']
        assert report == {'cleanup_verified':True,'input_file_deleted':True}


def ps(body):
    quoted = str(WRAPPER).replace("'", "''")
    code = f"""$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;
    Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1');
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile('{quoted}',[ref]$tokens,[ref]$errors);
    if($errors.Count){{throw ($errors|Out-String)}};
    foreach($f in $ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]}},$true)){{. ([scriptblock]::Create($f.Extent.Text))}};
    """
    return subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Command',code+body],capture_output=True,text=True,timeout=30)


def test_private_acl_has_only_system_admin_full_control():
    result=ps(r'''$acl=New-PrivateAcl;[ordered]@{owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value;protected=$acl.AreAccessRulesProtected;rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])|ForEach-Object{[ordered]@{sid=$_.IdentityReference.Value;rights=[int64]$_.FileSystemRights}})}|ConvertTo-Json -Depth 4 -Compress''')
    assert result.returncode==0,result.stderr
    result=json.loads(result.stdout)
    assert result=={'owner':'S-1-5-32-544','protected':True,'rules':[{'sid':'S-1-5-18','rights':2032127},{'sid':'S-1-5-32-544','rights':2032127}]}


@pytest.mark.parametrize('code',['synthetic-fixture','fixture-\u8a66\U0001f600'])
def test_actual_ps51_private_packet_create_new_and_repeat_refusal(tmp_path,code):
    path=str(tmp_path/'input.once').replace("'","''")
    # Replace only the ACL provider with a current-user-owned test fixture;
    # unprivileged tests cannot assign Administrators ownership. Production
    # ACL object is independently asserted by the preceding test.
    result=ps(f"$path='{path}';$fixture='{code}';"+r'''
    function New-PrivateAcl {$acl=[Security.AccessControl.FileSecurity]::new();$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User;$acl.SetAccessRuleProtection($true,$false);$acl.SetOwner($sid);$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,'FullControl','Allow'));return $acl};
    $secret=[Security.SecureString]::new();foreach($c in $fixture.ToCharArray()){$secret.AppendChar($c)};
    try{Write-PrivatePacket $path ('a'*32) $secret;$refused=$false;try{Write-PrivatePacket $path ('a'*32) $secret}catch{$refused=$true};[ordered]@{refused=$refused;private=(Get-Acl -LiteralPath $path).AreAccessRulesProtected}|ConvertTo-Json -Compress}finally{$secret.Dispose()}
    ''')
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'refused':True,'private':True}
    assert (tmp_path/'input.once').read_bytes()==packet(code.encode())


@pytest.mark.parametrize('state,instances,ok',[(3,0,True),(1,0,True),(0,0,False),(2,0,False),(4,0,False),(3,1,False)])
def test_task_terminal_proof(state,instances,ok):
    result=ps(f"$script:instances={instances};$task=[pscustomobject]@{{State={state}}};"+r'''$task|Add-Member ScriptMethod GetInstances {param($flags)[pscustomobject]@{Count=$script:instances}};$ok=$true;try{Assert-TaskTerminal $task}catch{$ok=$false};$ok|ConvertTo-Json -Compress''')
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==ok


@pytest.mark.parametrize('hresult,ok',[(-2147216629,True),(-2147216625,False),(-2147024894,False),(-2147024891,False)])
def test_only_task_not_running_ends_instance_poll(hresult,ok):
    result=ps(f"$script:result={hresult};"+r'''$instance=[pscustomobject]@{};$instance|Add-Member ScriptMethod Refresh {throw [Runtime.InteropServices.COMException]::new('fixture',$script:result)};$ok=$false;try{$ok=Test-InstanceComplete $instance}catch{};$ok|ConvertTo-Json -Compress''')
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)==ok


def test_wrapper_pins_source_and_support():
    text=WRAPPER.read_text()
    assert hashlib.sha256(PYTHON.read_bytes()).hexdigest() in text
    assert bridge.SUPPORT_SHA256 in text


def test_noninteractive_apply_refuses_before_any_task_or_copy():
    result=subprocess.run(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-File',str(WRAPPER),'-Slot','slot1','-Apply','-Interactive'],capture_output=True,text=True,timeout=15)
    assert result.returncode!=0
    assert 'Apply requires the owner at an elevated interactive ConsoleHost' in result.stderr
