"""Prepare additive downstream wrappers after reviewed original-task ACL recovery.

This generator never invokes PowerShell, Task Scheduler, a provider, or Apply.
The final reviewed helper/recovery pins must be supplied explicitly. Existing
outputs are preserved and make generation fail before any output is written.
The original commissioning Python and every protected host namespace stay bound
to their existing values; the downstream batch only reattests that first start.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re


W = Path(__file__).resolve().parent
PINS = {
    'install-held-supervisor-observation-r3-v2.ps1':
        '9fb90f828ff3b0a7776af9bc48acd99eb2a520508a80271a1e87b785a2ce7313',
    'install-resource-observer-r3-v4.ps1':
        '106d95dd5b22613cb871f3649d4376dc0a75745b6fce38469ddf94d2fcb9ec65',
    'run-post-commissioning-setup-r3-v3.ps1':
        '31ad291744eda4a2a453912b64c1b969c943b3fee70b387a503def6d6cd6589d',
}
REGISTRATION_SHA = 'eccc593f36cbf488a54775b4343bcf3cf8b261b7cd81e5536fb7f2f18466a198'
OLD_FIRST_START_FILE = 'run-pipeline-commissioning-r3-v6.ps1'
OLD_FIRST_START_SHA = '42cec356b787f3edd1529f8f9c81859459f932b0630494636477a0b2e1c7e8f7'
ACL_FILE = 'task-private-acl-v8.ps1'
RECOVERY_FILE = 'resume-pending-warden-r3-v7.ps1'
RECOVERY_PLAN_SCHEMA = 'cochem-pending-warden-recovery-plan/1'
NEW_HELD = 'install-held-supervisor-observation-r3-v3.ps1'
NEW_OBSERVER = 'install-resource-observer-r3-v5.ps1'
NEW_BATCH = 'run-post-commissioning-setup-r3-v4.ps1'


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def checked_source(name: str, expected: str) -> str:
    if not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise ValueError('An explicit final lowercase SHA256 is required')
    if not re.fullmatch(r'[a-zA-Z0-9_-]+\.ps1', name):
        raise ValueError('An exact local PowerShell filename is required')
    path = W / name
    if path.is_symlink():
        raise ValueError('Reviewed source cannot be a symbolic link: ' + name)
    raw = path.read_bytes()
    if digest(raw) != expected:
        raise ValueError('Reviewed source changed: ' + name)
    return raw.decode('utf-8-sig')


def once(text: str, before: str, after: str) -> str:
    if text.count(before) != 1:
        raise ValueError('Expected one exact reviewed replacement')
    return text.replace(before, after)


def split_acl_import(text: str, importer: str, variable: str,
                     acl_sha256: str) -> str:
    old = (
        f" foreach(${variable} in {importer} (Join-Path $PSScriptRoot "
        f"'register-stopped-warden-r3.ps1') '{REGISTRATION_SHA}' "
        "@('Get-RegistrationTask','Assert-RegisteredTaskAcl','Write-RegistrationControl'))"
        f"{{. ([scriptblock]::Create(${variable}))}}"
    )
    kept = old.replace(
        "@('Get-RegistrationTask','Assert-RegisteredTaskAcl','Write-RegistrationControl')",
        "@('Get-RegistrationTask','Write-RegistrationControl')",
    )
    added = (
        f" foreach(${variable} in {importer} (Join-Path $PSScriptRoot "
        f"'{ACL_FILE}') '{acl_sha256}' @('Assert-RegisteredTaskAcl'))"
        f"{{. ([scriptblock]::Create(${variable}))}}"
    )
    newline = '\r\n' if '\r\n' in text else '\n'
    return once(text, old, kept + newline + added)


def render(*, acl_sha256: str, recovery_file: str,
           recovery_sha256: str) -> dict[str, bytes]:
    """Return frozen successor bytes; this function performs no writes."""
    checked_source(ACL_FILE, acl_sha256)
    checked_source(recovery_file, recovery_sha256)
    if recovery_file != RECOVERY_FILE:
        raise ValueError('The exact reviewed pending-Warden recovery is required')
    old = {name: checked_source(name, pin) for name, pin in PINS.items()}
    outputs: dict[str, bytes] = {}

    def add(name: str, text: str) -> str:
        outputs[name] = text.encode('utf-8')
        return digest(outputs[name])

    held = old['install-held-supervisor-observation-r3-v2.ps1']
    held = once(
        held,
        "[pscustomobject]@{path=(Join-Path $PSScriptRoot 'register-stopped-warden-r3.ps1');"
        f"hash='{REGISTRATION_SHA}';names=@('Assert-RegisteredTaskAcl')}}",
        f"[pscustomobject]@{{path=(Join-Path $PSScriptRoot '{ACL_FILE}');"
        f"hash='{acl_sha256}';names=@('Assert-RegisteredTaskAcl')}}",
    )
    held = once(
        held, 'Assert-RegisteredTaskAcl ($Task.GetSecurityDescriptor(7))',
        'Assert-RegisteredTaskAcl ($Task.GetSecurityDescriptor(7)) $Task.Name',
    )
    held_sha = add(NEW_HELD, held)

    observer = split_acl_import(
        old['install-resource-observer-r3-v4.ps1'],
        'Import-ObserverFunctions', 'text', acl_sha256,
    )
    for task_variable in ('task', 'Task'):
        observer = once(
            observer,
            f'Assert-RegisteredTaskAcl (${task_variable}.GetSecurityDescriptor(7))',
            f'Assert-RegisteredTaskAcl (${task_variable}.GetSecurityDescriptor(7)) ${task_variable}.Name',
        )
    observer_sha = add(NEW_OBSERVER, observer)

    batch = split_acl_import(
        old['run-post-commissioning-setup-r3-v3.ps1'],
        'Import-SetupFunctions', 'd', acl_sha256,
    )
    # This phase consumes the already-running controller receipt and witness;
    # Get-SetupDisposition and Invoke-SetupPhases still prohibit replay/start.
    batch = once(batch, OLD_FIRST_START_FILE, recovery_file)
    batch = once(batch, OLD_FIRST_START_SHA, recovery_sha256)
    batch = once(batch, 'cochem-pipeline-commissioning-series-plan/1',
                 RECOVERY_PLAN_SCHEMA)
    batch = once(
        batch, 'Assert-RegisteredTaskAcl ($task.GetSecurityDescriptor(7))',
        'Assert-RegisteredTaskAcl ($task.GetSecurityDescriptor(7)) $task.Name',
    )
    batch = batch.replace('install-held-supervisor-observation-r3-v2.ps1', NEW_HELD)
    batch = batch.replace(PINS['install-held-supervisor-observation-r3-v2.ps1'], held_sha)
    batch = batch.replace('install-resource-observer-r3-v4.ps1', NEW_OBSERVER)
    batch = batch.replace(PINS['install-resource-observer-r3-v4.ps1'], observer_sha)
    batch = once(
        batch, 'PostCommissioningSetup4.2.7-windows-20261008-r3-v3',
        'PostCommissioningSetup4.2.7-windows-20261008-r3-v4',
    )
    add(NEW_BATCH, batch)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acl-sha256', required=True)
    parser.add_argument('--recovery-file', required=True)
    parser.add_argument('--recovery-sha256', required=True)
    args = parser.parse_args()
    outputs = render(acl_sha256=args.acl_sha256, recovery_file=args.recovery_file,
                     recovery_sha256=args.recovery_sha256)
    if any((W / name).exists() or (W / name).is_symlink() for name in outputs):
        raise FileExistsError('Fresh downstream outputs exist; preserve all files')
    for name, raw in outputs.items():
        with (W / name).open('xb') as stream:
            stream.write(raw)
        print(name, digest(raw))


if __name__ == '__main__':
    main()
