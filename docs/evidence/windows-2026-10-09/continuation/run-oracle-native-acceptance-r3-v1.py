"""One isolated Oracle/Job acceptance under maintained Warden exclusion.

Only the reviewed administrator wrapper can stage this exact protected entry.
Never stops/enables/restarts a daemon; an active or uncertain host is held.
"""
from __future__ import annotations
from contextlib import contextmanager, ExitStack
import argparse
import ctypes as C
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(r'C:\Program Files\CoChem\OracleNativeAcceptance4.2.7-windows-20261008-r3-v1')
INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
PRIVATE_ROOT = Path(r'C:\ProgramData\CoChemPipeline427\private')
PRIVATE = PRIVATE_ROOT / 'oracle-native-acceptance-20261008-r3-v1'
SCRATCH = Path(r'R:\CoChem427-windows-20261007\slot1\.cochem-scratch\oracle-native-acceptance-20261008-r3-v1')
TASK = 'CoChem-4.2.7-OracleNativeAcceptance-20261008-r3-v1'
COMMISSION = ROOT.parent/'WardenCommissioning4.2.7-windows-20261007-r3-v1'
COMMISSION_SHA = 'bd6ed9cc62bc96777d5748e819c5f780ada2e58ce0d43bf7e5f2013ca72f451c'
PINS = {
    'oracle-native-components-r3-v1.py': 'e32634f56bd67199cde3a7b6501030da892f540fa40e184ac0206639b847d1a0',
    'oracle-rogue-fixture-r3-v1.py': '137ffae58caee103a8025b7325f64580026ad6aefb10e95c3feba8f4494f0205',
    'worker-native-status-r3.py': 'c3c3069f097040442777ea30a6296abc506783968e381611fce26e20c7c4aed5',
    'inspect-execution-prerequisites-r3.py': '17a9a795fd755dc2e4955b1039784b4e19fd854aee399227e9d9427428eec41a',
}
DAEMON_SOURCE_SHA = '6d9886bfa131aa4bcf72c7debbfaae5e36117d41f5c85b17b29572a0554c8705'
PRIORS = {
    'foundation': (ROOT.parent/'ExecutionFoundation4.2.7-windows-20261007-r3-v2/execution-foundation.json', '9aea1e8f382ea8600cbd75d679f76acadc05da32774d9d8742cad08967ce1b41'),
    'physical': (ROOT.parent/'DockerExecutionAcceptance4.2.7-windows-20261007-r3-v2/docker-physical-acceptance.json', '2ca594fdca714ff48ba8f9a8201f5f27c0e71ba833a5aa5a23cbbe3266ad2e74'),
}
DAEMON_TASKS = ('CoChem-4.2.7-Warden', 'CoChem-4.2.7-Supervisor', 'CoChem-4.2.2-Warden',
                'CoChem-4.2.3-Supervisor', 'CoChem-4.2.7-WardenCommissioning-r3-v1')

# Exact disabled r3 daemon is supported; other known launch authorities must be
# absent. Any enabled/unknown/queued state is a maintenance dependency, not a
# reason for this helper to modify an existing task or stop owner work.
TASK_SCRIPT = r"""
$s=New-Object -ComObject 'Schedule.Service';$s.Connect();$f=$s.GetFolder('\');$rows=@()
foreach($name in $data.names){
 $t=$null
 try{$t=$f.GetTask($name)}catch{$missing=$false;$e=$_.Exception;while($null -ne $e){if($e.HResult -eq -2147024894){$missing=$true;break};$e=$e.InnerException};if(-not $missing){throw 'TASK_VISIBILITY'}}
 if($null -eq $t){$rows+=@(@{name=$name;absent=$true;xml_sha256=$null;security_sha256=$null});continue}
 if($name -cnotin @('CoChem-4.2.7-Warden','CoChem-4.2.7-WardenCommissioning-r3-v1')){throw 'OTHER_LAUNCH_AUTHORITY_PRESENT'}
 $commission=$name -ceq 'CoChem-4.2.7-WardenCommissioning-r3-v1'
 $limit=if($commission){'PT8M'}else{'PT0S'};$arguments=if($commission){$data.commission_arguments}else{$data.arguments}
 if($commission -and ($t.LastTaskResult -ne 0 -or -not $arguments)){throw 'COMMISSIONING_NOT_VERIFIED'}
 $d=$t.Definition
 if($t.Enabled -or $t.State -ne 1 -or $t.GetInstances(0).Count -ne 0 -or $d.Settings.Enabled -or
    -not $d.Settings.AllowDemandStart -or $d.Settings.RestartCount -ne 0 -or $d.Settings.MultipleInstances -ne 2 -or
    $d.Settings.ExecutionTimeLimit -cne $limit -or $d.Triggers.Count -ne 0 -or $d.Actions.Count -ne 1 -or
    $d.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $d.Principal.LogonType -ne 5 -or $d.Principal.RunLevel -ne 1){throw 'MAINTENANCE_REQUIRED'}
 $a=$d.Actions.Item(1)
 if($a.Type -ne 0 -or $a.Path -cne $data.python -or $a.Arguments -cne $arguments -or $a.WorkingDirectory -cne $data.install){throw 'DAEMON_DEFINITION_DRIFT'}
 $sddl=[string]$t.GetSecurityDescriptor(7);$sd=[Security.AccessControl.RawSecurityDescriptor]::new($sddl)
 if($sd.Owner.Value -notin @('S-1-5-18','S-1-5-32-544') -or -not ($sd.ControlFlags -band [Security.AccessControl.ControlFlags]::DiscretionaryAclProtected) -or $null -eq $sd.DiscretionaryAcl -or $sd.DiscretionaryAcl.Count -ne 2){throw 'TASK_SECURITY'}
 $seen=[Collections.Generic.HashSet[string]]::new()
 foreach($ace in $sd.DiscretionaryAcl){if($ace.AceType -ne 0 -or $ace.AceFlags -ne 0 -or $ace.AccessMask -ne 2032127 -or $ace.SecurityIdentifier.Value -notin @('S-1-5-18','S-1-5-32-544') -or -not $seen.Add($ace.SecurityIdentifier.Value)){throw 'TASK_SECURITY'}}
 $bytes=[Text.Encoding]::UTF8.GetBytes([string]$t.Xml);$sha=[Security.Cryptography.SHA256]::Create()
 try{$hash=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant();$security=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($sddl))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 $rows+=@(@{name=$name;absent=$false;xml_sha256=$hash;security_sha256=$security})
}
ConvertTo-Json -InputObject @($rows) -Compress
"""


def require(value, code):
    if not value:
        raise ValueError(code)


def load_bound(path, pin):
    raw = path.read_bytes()
    require(len(raw) <= 1048576 and hashlib.sha256(raw).hexdigest() == pin, 'SUPPORT_PIN')
    spec = importlib.util.spec_from_file_location('_oracle_' + path.stem.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def commission_arguments(read):
    """Bind a preserved consumed first-start helper; it is never run here."""
    raw = read(COMMISSION/'inputs.json', None)
    packet = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    receipt = json.loads(read(COMMISSION/'commissioning.json', None))
    require(packet.get('schema') == 'cochem-warden-commissioning-inputs/1'
            and re.fullmatch('[a-f0-9]{32}', str(packet.get('nonce'))), 'COMMISSION_PACKET')
    expected = {'schema': 'cochem-warden-commissioning/1', 'status': 'WARDEN_RUNNING_CONTROL_PLANE_VERIFIED',
                'nonce': packet['nonce'], 'input_sha256': digest, 'helper_sha256': COMMISSION_SHA,
                'system_sid': 'S-1-5-18', 'runtime_root': str(INSTALL), 'config_sha256': CONFIG_SHA,
                'exactly_one_start_requested': True, 'automatic_retry_allowed': False}
    require(all(type(receipt.get(k)) is type(v) and receipt[k] == v for k, v in expected.items()), 'COMMISSION_RECEIPT')
    require(packet.get('config_sha256') == CONFIG_SHA, 'COMMISSION_CONFIG')
    read(COMMISSION/'commission-first-warden-r3-v1.py', COMMISSION_SHA)
    read(COMMISSION/'pipeline.json', CONFIG_SHA)
    return f'-I -B "{COMMISSION / "commission-first-warden-r3-v1.py"}" --nonce {packet["nonce"]} --input-sha256 {digest}'


CONFIG_SHA = '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c'


def task_snapshot(win, commission_args=None):
    data = {'names': list(DAEMON_TASKS), 'python': str(INSTALL/'.venv/Scripts/python.exe'), 'install': str(INSTALL),
            'arguments': '-I -B -m cochem_pipeline daemon --config "C:\\Program Files\\CoChem\\WardenCommissioning4.2.7-windows-20261007-r3-v1\\pipeline.json" --queue-launch-output "C:\\ProgramData\\CoChemPipeline427\\private\\queue-commissioning-20261007-r3-v1"',
            'commission_arguments': commission_args}
    result = json.loads(win._powershell(TASK_SCRIPT, data))
    require(isinstance(result, list) and len(result) == len(DAEMON_TASKS), 'TASK_SNAPSHOT')
    for name, row in zip(DAEMON_TASKS, result):
        require(isinstance(row, dict) and set(row) == {'name', 'absent', 'xml_sha256', 'security_sha256'} and row['name'] == name
                and type(row['absent']) is bool, 'TASK_SNAPSHOT')
        for key in ('xml_sha256', 'security_sha256'):
            require(row[key] is None if row['absent'] else re.fullmatch('[a-f0-9]{64}', str(row[key])), 'TASK_COMMITMENT')
    return result


def daemon_census():
    """Metadata only; never returns command lines or alters a process."""
    import psutil
    started = time.monotonic()
    count = 0
    for process in psutil.process_iter(['pid', 'name']):
        count += 1
        require(count <= 8192 and time.monotonic()-started <= 15, 'PROCESS_CENSUS_BOUND')
        name = process.info['name']
        require(isinstance(name, str), 'PROCESS_NAME_UNAVAILABLE')
        if name.casefold() not in {'python.exe', 'pythonw.exe', 'cochem-pipeline.exe', 'cochem-supervisor.exe'}:
            continue
        try:
            argv = process.cmdline()
            require(isinstance(argv, list) and len(argv) <= 256 and sum(map(len, argv)) <= 32768, 'PROCESS_ARGUMENT_BOUND')
            # The maintained native lock is the authoritative construction
            # barrier; this census additionally rejects direct installed runs.
            require(not ('daemon' in argv and (any(item in ('cochem_pipeline', 'cochem_pipeline.__main__') for item in argv) or
                    any('cochem_pipeline' in item.casefold() and item.casefold().endswith('__main__.py') for item in argv))),
                    'DIRECT_DAEMON_PRESENT')
            require(not any(item in ('cochem_supervisor', 'cochem_supervisor.__main__') or
                    ('cochem_supervisor' in item.casefold() and item.casefold().endswith('__main__.py')) for item in argv),
                    'DIRECT_SUPERVISOR_PRESENT')
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            raise ValueError('PROCESS_CENSUS_DENIED') from None
    return {'processes_checked': count, 'direct_daemons': 0, 'command_lines_retained': False}


def file_identity(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_nlink)


@contextmanager
def maintained_lock(path, win, *, expected_parent=PRIVATE_ROOT):
    """Same byte-0 exclusion as production; never truncate or replace bytes."""
    import msvcrt
    from cochem_pipeline.ramdisk import ordinary_tree
    require(path == expected_parent/'warden.lock', 'SERVICE_LOCK_PATH')
    win.validate_private_directory(expected_parent)
    ordinary_tree(expected_parent)
    kernel = win._api()['kernel32']
    created = False
    try:
        before_path = path.lstat()
    except FileNotFoundError:
        before_path = None
    if before_path is not None:
        ordinary_tree(path); win.validate_private_path(path)
        require(path.is_file() and before_path.st_nlink == 1 and 1 <= before_path.st_size <= 4096, 'SERVICE_LOCK_SHAPE')
    handle = kernel.CreateFileW(str(path), 0xC0000000, 3, None, 3 if before_path else 1, 0x00200000, None)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    created = before_path is None
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        win._close(handle)
        raise
    with os.fdopen(descriptor, 'r+b', buffering=0) as stream:
        if before_path is not None:
            require(file_identity(os.fstat(stream.fileno())) == file_identity(before_path), 'SERVICE_LOCK_IDENTITY')
        # Nonblocking. If a daemon wins, this raises and no fixture is launched.
        stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        if created:
            require(os.fstat(stream.fileno()).st_size == 0, 'NEW_SERVICE_LOCK_CHANGED')
            stream.write(b'0'); stream.flush(); os.fsync(stream.fileno())
            win.validate_private_path(path)
        stream.seek(0); original = stream.read(4097)
        require(1 <= len(original) <= 4096, 'SERVICE_LOCK_BYTES')
        before = os.fstat(stream.fileno())
        security = win._acl(path)
        value = {'created_new': created, 'bytes': len(original), 'sha256': hashlib.sha256(original).hexdigest(),
                 'file_id': before.st_ino, 'device': before.st_dev, 'exclusive_byte_zero_lock': True,
                 'bytes_and_identity_preserved': False}
        try:
            yield value
        finally:
            stream.seek(0); final = stream.read(4097)
            require(final == original and file_identity(os.fstat(stream.fileno())) == file_identity(before)
                    and file_identity(path.lstat()) == file_identity(before) and win._acl(path) == security,
                    'SERVICE_LOCK_PRESERVATION')
            value['bytes_and_identity_preserved'] = True
            # Closing is the only release. Do not delete even a newly created lock.


def write_new(path, value):
    raw = (json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, indent=2)+'\n').encode()
    require(len(raw) <= 131072, 'RECEIPT_BOUND')
    with path.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def run(nonce, packet_sha):
    from cochem_pipeline import windows as win
    from cochem_pipeline.config import load_config
    from cochem_pipeline.ramdisk import ordinary_tree
    win.require_system()
    require(Path(__file__).resolve() == ROOT/'run-oracle-native-acceptance-r3-v1.py'
            and Path(sys.executable).resolve() == INSTALL/'.venv/Scripts/python.exe'
            and sys.flags.isolated and sys.dont_write_bytecode, 'PROTECTED_ENTRY')
    require(re.fullmatch('[a-f0-9]{32}', nonce) and re.fullmatch('[a-f0-9]{64}', packet_sha), 'INPUT_BINDINGS')
    win.validate_code_path(ROOT)
    receipt = ROOT/'oracle-native-acceptance.json'
    require(not receipt.exists(), 'RECEIPT_EXISTS')
    report = {'schema': 'cochem-oracle-native-acceptance/1', 'nonce': nonce, 'packet_sha256': packet_sha,
              'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'system_sid': 'S-1-5-18',
              'status': 'ORACLE_NATIVE_ACCEPTANCE_HELD', 'runtime_root': str(INSTALL),
              'full_service_acceptance': False, 'automatic_retry_allowed': False, 'daemon_tasks_modified': False,
              'daemon_stop_requested': False, 'production_databases_modified': False, 'model_jobs_executed': 0,
              'fixture_creation_started': False, 'physical_component_entered': False, 'partial_outputs_preserved': True}
    phase = 'custody'
    try:
        S = load_bound(ROOT/'worker-native-status-r3.py', PINS['worker-native-status-r3.py'])
        P = load_bound(ROOT/'inspect-execution-prerequisites-r3.py', PINS['inspect-execution-prerequisites-r3.py'])
        with ExitStack() as held:
            def get(path, pin, maximum=1048576):
                return held.enter_context(P.held_read(path, maximum, win, pin))
            for name, pin in PINS.items(): get(ROOT/name, pin)
            get(S.BASE_PYTHON, 'd8e3f0adf246db00358c0c4ed349cf714898178f9558fb0e944f79f5c07f8eaa', 1048576)
            get(INSTALL/'.venv/Lib/site-packages/cochem_pipeline/__main__.py', DAEMON_SOURCE_SHA)
            revision = S.verify_r3_runtime(win, S.INSTALL_RECEIPT_SHA256)
            packet = S.strict_json(get(ROOT/'inputs.json', packet_sha))
            require(packet.get('schema') == 'cochem-oracle-native-inputs/1' and packet.get('nonce') == nonce
                    and packet.get('config_sha256') == S.CONFIG_SHA256, 'PACKET_BINDING')
            get(INSTALL/'pipeline.json', S.CONFIG_SHA256)
            config = load_config(INSTALL/'pipeline.json')
            require(config.private_root == PRIVATE_ROOT, 'PRIVATE_ROOT_BINDING')
            core = load_bound(ROOT/'oracle-native-components-r3-v1.py', PINS['oracle-native-components-r3-v1.py'])
            report.update(config_sha256=S.CONFIG_SHA256, source_manifest_sha256=S.MANIFEST_SHA256,
                          install_receipt_sha256=S.INSTALL_RECEIPT_SHA256, revision_sha256=revision['source_sha256'])
            phase = 'prior_evidence'
            for name, (path, pin) in PRIORS.items(): get(path, pin)
            P.knowledge_receipt(S.strict_json(get(P.KNOWLEDGE, P.KNOWLEDGE_SHA)))
            for slot, spec in config.workers.items():
                path = ROOT.parent/f'WorkerDenial4.2.7-windows-20261007-r3-{slot}'/'worker-denial-acceptance.json'
                value, digest = S.protected_json(win, path)
                get(path, digest)
                P.worker_receipt(value, slot, S.INSTALL_RECEIPT_SHA256, revision, win._sid_text(win._account_sid(spec['name'])))
            phase = 'maintenance'
            try: (COMMISSION/'inputs.json').lstat()
            except FileNotFoundError: commission_args = None
            else: commission_args = commission_arguments(get)
            baseline_tasks = task_snapshot(win, commission_args)
            require(baseline_tasks == packet.get('task_baseline'), 'TASK_BASELINE_CHANGED')
            report['process_census_before'] = daemon_census()
            with maintained_lock(PRIVATE_ROOT/'warden.lock', win) as lock:
                report['maintenance_lock'] = lock
                def stopped():
                    require(task_snapshot(win, commission_args) == baseline_tasks, 'MAINTENANCE_TASK_DRIFT')
                    daemon_census()
                stopped()
                ram_before = P.ram_observation(win, config)
                def preserved():
                    require(P.ram_observation(win, config) == ram_before, 'RAM_BASELINE_CHANGED')
                phase = 'fresh_fixture'
                for directory in (PRIVATE, SCRATCH):
                    try: directory.lstat()
                    except FileNotFoundError: pass
                    else: raise ValueError('FRESH_FIXTURE_COLLISION')
                    ordinary_tree(directory.parent)
                report['fixture_creation_started'] = True
                PRIVATE.mkdir(mode=0o777); win.validate_private_directory(PRIVATE)
                SCRATCH.mkdir(mode=0o777)
                stopped(); preserved()
                phase = 'physical_component'
                report['physical_component_entered'] = True
                report['component'] = core._component_core(config, nonce, require_stopped=stopped, assert_preserved_baseline=preserved)
                phase = 'preservation'
                stopped(); preserved()
                report['process_census_after'] = daemon_census()
                report['ram_baseline_preserved'] = True
            require(report['maintenance_lock']['bytes_and_identity_preserved'], 'LOCK_POSTCONDITION')
            report['status'] = 'ISOLATED_ORACLE_NATIVE_TRIP_AND_FENCING_VERIFIED'
    except BaseException as error:
        code = str(error) if isinstance(error, ValueError) and re.fullmatch('[A-Z][A-Z0-9_]{0,95}', str(error)) else None
        number = getattr(error, 'winerror', None)
        report['failure'] = {'phase': phase, 'error_type': type(error).__name__, 'code': code,
                             'winerror': number if type(number) is int and 0 <= number <= 4294967295 else None}
    write_new(receipt, report)
    return report


def main():
    p = argparse.ArgumentParser(); p.add_argument('--nonce', required=True); p.add_argument('--packet-sha256', required=True)
    a = p.parse_args()
    try:
        result = run(a.nonce, a.packet_sha256)
        return 0 if result['status'] == 'ISOLATED_ORACLE_NATIVE_TRIP_AND_FENCING_VERIFIED' else 2
    except BaseException as error:
        print(json.dumps({'schema': 'cochem-oracle-native-bootstrap-failure/1', 'error_type': type(error).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
