"""DRAFT: fresh-only SYSTEM execution initialization, never inference/activation.

Invoked only by the separately reviewed owner wrapper. This intentionally writes
new RAM bookkeeping and a new Docker registry, then production readiness evidence.
It does not create/format/resize R: or change its existing startup task.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

ROOT = Path(r'C:\Program Files\CoChem\ExecutionReadiness4.2.7-windows-20261006')
# Deliberately inert draft: source review found that the installed whole-root
# adoption would change owner R: permissions. A revised protected runtime and
# workspace_subdirectory config must be installed before this can be finalized.
APPROVED_FOR_EXECUTION = False
INSTALL = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261006')
PACKAGE = INSTALL / '.venv/Lib/site-packages/cochem_pipeline'
PYTHON = INSTALL / '.venv/Scripts/python.exe'
PRIVATE = Path(r'C:\ProgramData\CoChemPipeline427\private')
CONFIG_SHA = '2c7c1d781a74b5e110c36b9fae79eb90dfaae5a0249aa60a23d1db40749b62f6'
PINS_SHA = 'c838c4ed98c31382229bddc1cf2b00297e1bb2d771404ea1b979727c4472dd48'
RAM_TASK_SHA = '1da7687a165ddfb425cb01147f4f5cbd3f7ba035947063ef396e268d7babc928'
KNOWLEDGE = Path(r'C:\Program Files\CoChem\KnowledgeAcceptance4.2.7-windows-20261006')
KNOWLEDGE_STATE = PRIVATE / 'knowledge-windows-20261006'
CORPUS = Path(r'C:\Program Files\CoChem\Knowledge4.2.7-windows-20261006')
MANIFEST_SHA = '7c16c12d319aa89b78fcd83628be7e23250259ad9b05b0c246dbd48746d44ec5'
DENIAL_SHA = '78c0006a225fa98211f038c380552d9651a86c9198b518603e989747f315bb45'
DAEMONS = ('CoChem-4.2.7-Warden', 'CoChem-4.2.7-Supervisor',
           'CoChem-4.2.2-Warden', 'CoChem-4.2.3-Supervisor')


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate evidence key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Nonfinite evidence value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def read_bytes(path, limit=8 * 1048576):
    """Bounded ordinary single-link file read, never a credential/payload target."""
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or getattr(before, 'st_file_attributes', 0) & 0x400 or before.st_size > limit):
        raise ValueError('Evidence path is not a bounded ordinary single-link file')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    after = path.lstat()
    if len(raw) > limit or (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns):
        raise ValueError('Evidence changed during bounded read')
    return raw


def digest(path, limit=8 * 1048576):
    return hashlib.sha256(read_bytes(path, limit)).hexdigest()


def require_absent(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return
    # PermissionError and every other failure propagate: inaccessible is not absent.
    raise FileExistsError('Preserve existing execution state or scratch directory')


def verify_runtime():
    raw = read_bytes(ROOT / 'execution-runtime-pins.json')
    if hashlib.sha256(raw).hexdigest() != PINS_SHA:
        raise ValueError('Runtime inventory changed')
    inventory = strict_json(raw)
    if inventory.get('schema') != 'cochem-execution-runtime-pins/1' or inventory.get('root') != str(PACKAGE):
        raise ValueError('Unexpected runtime inventory')
    rows = inventory.get('files')
    if not isinstance(rows, list) or len(rows) != 53:
        raise ValueError('Unexpected runtime source count')
    expected = set()
    for row in rows:
        key = row['relative']
        if not re.fullmatch(r'[A-Za-z0-9_]+\.py', key) or key in expected:
            raise ValueError('Unsafe or duplicate runtime source path')
        expected.add(key)
        path = PACKAGE / key
        if path.stat().st_size != row['length'] or digest(path) != row['sha256']:
            raise ValueError('Installed runtime bytes changed')
    if {p.relative_to(PACKAGE).as_posix() for p in PACKAGE.rglob('*.py')} != expected:
        raise ValueError('Installed runtime source set changed')


def require_stopped(win):
    win._powershell(r"""
      $s=New-Object -ComObject 'Schedule.Service';$s.Connect();$folder=$s.GetFolder('\');
      foreach($name in $data){$task=$null;try{$task=$folder.GetTask($name)}catch{
        $e=$_.Exception;$missing=$false;while($null -ne $e){if($e.HResult -eq -2147024894){$missing=$true;break};$e=$e.InnerException}
        if(-not $missing){throw 'Task state unknown.'}}
        if($null -ne $task -and ($task.Enabled -or $task.State -notin @(1,3) -or $task.GetInstances(0).Count -ne 0)){throw 'Pipeline daemons must be stopped and disabled.'}}
    """, list(DAEMONS))


def ram_baseline(win, config):
    raw = win._powershell(r"""
      $task=Get-ScheduledTask -TaskName 'Mount_CoChem_RAMDisk' -TaskPath '\' -ErrorAction Stop
      $actions=@($task.Actions);$triggers=@($task.Triggers)
      if($task.Principal.UserId -notin @('SYSTEM','S-1-5-18','NT AUTHORITY\SYSTEM') -or -not $task.Settings.Enabled -or $actions.Count -ne 1 -or $triggers.Count -ne 1 -or $triggers[0].CimClass.CimClassName -ne 'MSFT_TaskBootTrigger' -or -not $triggers[0].Enabled){throw 'RAM task contract changed.'}
      if($actions[0].Execute -cne 'imdisk.exe' -or $actions[0].Arguments -cne '-a -s 8G -m R: -p "/fs:ntfs /q /y"' -or $actions[0].WorkingDirectory){throw 'RAM action changed.'}
      $xml=Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop
      [byte[]]$bytes=[Text.Encoding]::Unicode.GetPreamble()+[Text.Encoding]::Unicode.GetBytes($xml)
      $sha=[Security.Cryptography.SHA256]::Create();try{$hash=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
      $volume=@(Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='R:'" -OperationTimeoutSec 10 -ErrorAction Stop)
      if($volume.Count -ne 1 -or [long]$volume[0].Size -ne 8589930496 -or $volume[0].FileSystem -ne 'NTFS' -or $volume[0].VolumeName){throw 'Existing RAM filesystem contract changed.'}
      [ordered]@{task_xml_sha256=$hash;filesystem_bytes=[long]$volume[0].Size;label=$volume[0].VolumeName;root_sddl=(Get-Acl -LiteralPath 'R:\').Sddl;root_attributes=[int](Get-Item -LiteralPath 'R:\' -Force).Attributes}|ConvertTo-Json -Compress
    """)
    metadata = strict_json(raw)
    if metadata['task_xml_sha256'] != RAM_TASK_SHA:
        raise ValueError('Existing R startup task differs from preserved owner baseline')
    from cochem_pipeline.ramdisk import _native_volume
    metadata['volume'] = _native_volume(config.ramdisk)
    if metadata['volume']['size_bytes'] != 8589934592:
        raise ValueError('Actual ImDisk device is not the existing 8 GiB drive')
    return metadata


def require_receipt(win, path, schema, status):
    win.validate_code_path(path)
    value = strict_json(read_bytes(path, 65536))
    if (value.get('schema') != schema or value.get('status') != status
            or value.get('system_sid') != 'S-1-5-18'
            or not re.fullmatch('[0-9a-f]{32}', str(value.get('nonce')))):
        raise ValueError('Required SYSTEM acceptance receipt is missing or failed')
    return value


def verify_prior_acceptance(win, config):
    evidence = {}
    for slot in config.workers:
        path = Path(r'C:\Program Files\CoChem') / ('WorkerDenial4.2.7-windows-20261006-' + slot) / 'worker-denial-acceptance.json'
        value = require_receipt(win, path, 'cochem-worker-denial-acceptance/1', 'HANDLE_DENIALS_VERIFIED')
        if (value.get('slot') != slot or value.get('helper_sha256') != DENIAL_SHA
                or value.get('cleanup_verified') is not True):
            raise ValueError('Worker denial evidence does not match reviewed helper/slot/cleanup')
        evidence[slot] = digest(path)
    path = KNOWLEDGE / 'knowledge-acceptance.json'
    receipt = require_receipt(win, path, 'cochem-private-knowledge-system-acceptance/1', 'PRIVATE_CORPUS_AND_NEW_INDEX_VERIFIED')
    if (receipt.get('pipeline_config_sha256') != CONFIG_SHA or receipt.get('corpus_manifest_sha256') != MANIFEST_SHA
            or receipt.get('index_created_new') is not True or receipt.get('source_bytes_preserved') is not True):
        raise ValueError('Knowledge receipt differs from reviewed private corpus')
    from cochem_pipeline.knowledge import KnowledgeService
    win.validate_private_directory(KNOWLEDGE_STATE)
    service = KnowledgeService(config.knowledge)  # Existing state required above.
    try:
        validation = service.validate()
        current, index = service._current()
        with service._reader() as (_, database):
            integrity = database.execute('PRAGMA quick_check').fetchone()[0]
        if (current['generation'] != receipt['generation'] or validation['manifest_sha256'] != MANIFEST_SHA
                or current['manifest_sha256'] != MANIFEST_SHA or integrity != 'ok'
                or digest(index, 134217728) != receipt['index_sha256']):
            raise ValueError('Knowledge changed after its successful acceptance')
    finally:
        service.close()
    evidence['knowledge'] = digest(path)
    evidence['knowledge_generation'] = receipt['generation']
    evidence['knowledge_index_sha256'] = receipt['index_sha256']
    return evidence


def docker_census(config, identities):
    """Read-only, no DockerRunner construction: that would initialize its DB."""
    from cochem_pipeline.deployment import attest_docker_pipe_server
    from cochem_pipeline.containers import _bounded_process
    endpoint = config.docker.endpoint
    env = dict(os.environ)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)
    counts = {}
    queries = {
        'owner_labelled_containers': ['ps', '-a', '-q', '--no-trunc', '--filter', 'label=org.cochem.owner'],
        'cochem_named_containers': ['ps', '-a', '-q', '--no-trunc', '--filter', 'name=cochem-'],
        'owner_labelled_volumes': ['volume', 'ls', '-q', '--filter', 'label=org.cochem.owner'],
        'owner_labelled_networks': ['network', 'ls', '-q', '--no-trunc', '--filter', 'label=org.cochem.owner'],
    }
    for name, arguments in queries.items():
        attest_docker_pipe_server(endpoint, identities, trusted_operator=config.operator_name,
                                 trusted_server_executables=config.docker.pipe_server_executables)
        result = _bounded_process([config.docker.executable, '--host', endpoint, *arguments],
                                  env=env, timeout=15, output_limit=65536)
        if result.returncode or result.timed_out or result.cancelled or result.output_exceeded or result.stderr.strip():
            raise ValueError('Docker ownership census is failed or uncertain')
        rows = result.stdout.decode('ascii', errors='strict').splitlines()
        counts[name] = len(rows)
        if rows:
            raise FileExistsError('Existing scoped Docker resources require ownership reconciliation')
    return {'scope': 'selected_local_daemon_cochem_labels_and_container_name_prefix', 'counts': counts,
            'unrelated_resources_modified': False, 'empty_scope_verified': True}


def require_fresh_state(config):
    for path in (PRIVATE / 'containers', PRIVATE / 'ramdisk-state.json', PRIVATE / 'execution-readiness.json'):
        require_absent(path)
    for slot in config.workers:
        require_absent(Path(config.ramdisk.mount_root) / slot)


def same_volume(before, after):
    return (before['task_xml_sha256'] == after['task_xml_sha256'] == RAM_TASK_SHA
            and before['volume'] == after['volume']
            and before['filesystem_bytes'] == after['filesystem_bytes']
            and before['label'] == after['label'])


def require_existing_root_boundary(win, config, identities):
    """Never silently take away the owner's current root rights/inheritance.

    Current AETHERDESK R: does NOT pass this gate. Production ensure replaces the
    entire root DACL, so fresh setup needs a reviewed owner-use design first.
    Keeping this gate is deliberate; no approval is inferred from elapsed time.
    """
    worker_sids = [win._sid_text(win._account_sid(item.name)) for item in identities.values()]
    win._validate_boundary(Path(config.ramdisk.mount_root), worker_sids)
    from cochem_pipeline.ramdisk import _no_content_index
    _no_content_index(Path(config.ramdisk.mount_root), apply=False)


def safe_failure(phase, error):
    code, current = None, error
    for _ in range(6):
        candidate = getattr(current, 'winerror', None)
        if type(candidate) is int and 0 <= candidate <= 0xFFFFFFFF:
            code = candidate; break
        current = current.__cause__ or current.__context__
        if current is None:
            break
    return {'phase': phase, 'error_type': re.sub('[^A-Za-z0-9_]', '', type(error).__name__)[:80], 'winerror': code}


def run(nonce):
    if Path(__file__).resolve() != ROOT / 'initialize-execution-readiness.py' or Path(sys.executable).resolve() != PYTHON:
        raise ValueError('Use exact protected initialization helper/interpreter')
    verify_runtime()  # Before importing executable pipeline source.
    from cochem_pipeline import windows as win
    from cochem_pipeline.config import load_config
    from cochem_pipeline.ramdisk import RamdiskManager, ordinary_tree
    from cochem_pipeline.deployment import execution_readiness, verify_docker_access_boundary
    win.require_system()
    for path in (ROOT, Path(__file__), PYTHON):
        ordinary_tree(path); win.validate_code_path(path)
    report = {'schema': 'cochem-initial-execution-readiness/1', 'nonce': nonce,
        'system_sid': win.SYSTEM_SID, 'status': 'UNVERIFIED', 'started_at_unix_ms': int(time.time() * 1000),
        'helper_sha256': digest(Path(__file__)), 'runtime_pins_sha256': PINS_SHA,
        'native_model_jobs_executed': 0, 'pipeline_started': False, 'legacy_state_read_or_modified': False,
        'repair_budgets_modified': False, 'ram_initialization_attempted': False,
        'production_readiness_attempted': False, 'new_state_preserved_on_failure': True}
    phase = 'trusted_preflight'
    with (ROOT / 'initial-execution-readiness.json').open('x', encoding='utf-8') as receipt:
        try:
            config_path = INSTALL / 'pipeline.json'
            ordinary_tree(config_path); win.validate_code_path(config_path)
            if digest(config_path) != CONFIG_SHA:
                raise ValueError('Exact protected host configuration changed')
            config = load_config(str(config_path))
            if (config.private_root != PRIVATE or config.max_execution_slots != 4
                    or set(config.workers) != {'slot' + str(i) for i in range(1, 7)}
                    or config.ramdisk.lifecycle != 'adopt_existing' or config.ramdisk.mount_root != 'R:\\'
                    or config.ramdisk.size_mb != 8192 or not config.docker.enabled):
                raise ValueError('Fresh host execution policy differs')
            identities = {slot: win.WorkerIdentity(**value) for slot, value in config.workers.items()}
            win.validate_layout(PRIVATE, config.slot_roots, identities, require_defender=True)
            win.validate_controller_token(config.token_file, config.operator_name, identities)
            require_stopped(win)
            phase = 'prior_acceptance'
            report['prior_acceptance'] = verify_prior_acceptance(win, config)
            phase = 'fresh_state_and_ram_baseline'
            require_fresh_state(config)
            before = ram_baseline(win, config)
            report['ram_before'] = before
            phase = 'docker_boundary_and_collision_census'
            win.validate_code_path(config.docker.executable)
            report['docker_boundary_before_writes'] = verify_docker_access_boundary(config.docker.endpoint, identities,
                trusted_operator=config.operator_name, trusted_server_executables=config.docker.pipe_server_executables)
            report['docker_census'] = docker_census(config, identities)
            # Repeat all freshness/task gates immediately before first execution-state write.
            require_stopped(win); require_fresh_state(config)
            if digest(config_path) != CONFIG_SHA or not same_volume(before, ram_baseline(win, config)):
                raise ValueError('Configuration or RAM baseline changed during preflight')
            phase = 'ram_root_owner_preservation_gate'
            # Capture original ACL/indexing metadata durably even when this gate holds.
            baseline_path = ROOT / 'preflight-baseline.json'
            with baseline_path.open('x', encoding='utf-8') as baseline:
                json.dump({'schema': 'cochem-execution-preflight-baseline/1', 'nonce': nonce,
                    'system_sid': win.SYSTEM_SID, 'pipeline_config_sha256': CONFIG_SHA,
                    'ram_before': before, 'docker_census': report['docker_census'],
                    'prior_acceptance': report['prior_acceptance']}, baseline, sort_keys=True, indent=2)
                baseline.flush(); os.fsync(baseline.fileno())
            require_existing_root_boundary(win, config, identities)
            phase = 'initial_ram_adoption'
            report['ram_initialization_attempted'] = True
            adoption = RamdiskManager(config.ramdisk, PRIVATE, identities).ensure()
            if adoption.get('lifecycle_action') != 'ADOPT' or adoption.get('adopted_existing_drive') is not True:
                raise ValueError('Unexpected production RAM lifecycle action')
            report['ram_ledger_sha256'] = digest(PRIVATE / 'ramdisk-state.json')
            report['ram_initialization_completed'] = True
            phase = 'production_readiness'
            # Knowledge is already initialized; provision=False avoids repeating corpus ACL setup.
            report['production_readiness_attempted'] = True
            readiness = execution_readiness(config, provision=False)
            report['execution_readiness_sha256'] = digest(PRIVATE / 'execution-readiness.json')
            report['checks_ready'] = {name: value['ready'] for name, value in readiness['checks'].items()}
            report['ready'] = readiness['ready']
            phase = 'post_preservation_checks'
            after = ram_baseline(win, config)
            report['ram_after'] = after
            if not same_volume(before, after) or digest(config_path) != CONFIG_SHA:
                raise ValueError('Preserved RAM identity/startup task or configuration changed')
            report['ram_device_and_task_preserved'] = True
            report['ram_root_acl_changed'] = before['root_sddl'] != after['root_sddl']
            report['ram_root_indexing_attribute_changed'] = before['root_attributes'] != after['root_attributes']
            require_stopped(win)
            # Refresh must take the unchanged manifest path, not replace/rebuild the accepted index.
            if verify_prior_acceptance(win, config) != report['prior_acceptance']:
                raise ValueError('Knowledge or denial evidence changed during readiness')
            report['status'] = 'INITIAL_EXECUTION_READY' if readiness['ready'] else 'INITIALIZED_READINESS_HOLD'
        except BaseException as error:
            report['status'] = 'INITIAL_EXECUTION_FAILED'
            report['failure'] = safe_failure(phase, error)
            report['operator_review_required'] = True
        finally:
            report['finished_at_unix_ms'] = int(time.time() * 1000)
            json.dump(report, receipt, sort_keys=True, indent=2, allow_nan=False)
            receipt.flush(); os.fsync(receipt.fileno())
    return 0 if report['status'] == 'INITIAL_EXECUTION_READY' else 2


def main():
    if not APPROVED_FOR_EXECUTION:
        print('DRAFT HELD: requires reviewed adopted-subtree runtime/configuration update.', file=sys.stderr)
        return 4
    if len(sys.argv) != 2 or not re.fullmatch('[0-9a-f]{32}', sys.argv[1]):
        return 3
    try:
        return run(sys.argv[1])
    except BaseException as error:
        print(json.dumps(safe_failure('pre_receipt', error)), file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
