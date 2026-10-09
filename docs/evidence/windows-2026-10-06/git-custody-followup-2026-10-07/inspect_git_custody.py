"""Bounded ordinary-user metadata/PE import/ACL inspection; no Git execution.

Only these installed executable/dependency paths and their real hardlink names
are read. This is evidence collection, not an installer or a runtime attestor.
"""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack
import datetime, hashlib, json, msvcrt, os
from pathlib import Path
import stat, struct, subprocess

ROOT = Path(r'C:\Program Files\Git')
OUTPUT = Path(__file__).parent
K32 = C.WinDLL('kernel32', use_last_error=True)
K32.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
K32.CreateFileW.restype = W.HANDLE
K32.FindFirstFileNameW.argtypes = [W.LPCWSTR, W.DWORD, C.POINTER(W.DWORD), W.LPWSTR]
K32.FindFirstFileNameW.restype = W.HANDLE
K32.FindNextFileNameW.argtypes = [W.HANDLE, C.POINTER(W.DWORD), W.LPWSTR]
K32.FindNextFileNameW.restype = W.BOOL
K32.FindClose.argtypes = [W.HANDLE]
K32.FindClose.restype = W.BOOL
K32.CloseHandle.argtypes = [W.HANDLE]
K32.CloseHandle.restype = W.BOOL
INVALID = C.c_void_p(-1).value


def aliases(path):
    length = W.DWORD(32768); buffer = C.create_unicode_buffer(length.value)
    handle = K32.FindFirstFileNameW(str(path), 0, C.byref(length), buffer)
    if handle == INVALID:
        raise C.WinError(C.get_last_error())
    names = []
    try:
        while True:
            names.append(str(Path(path.drive + buffer.value)))
            if len(names) > 128:
                raise ValueError('Hardlink enumeration exceeds 128 names')
            length.value = 32768
            if not K32.FindNextFileNameW(handle, C.byref(length), buffer):
                if C.get_last_error() != 38:
                    raise C.WinError(C.get_last_error())
                break
    finally:
        K32.FindClose(handle)
    return sorted(names, key=str.casefold)


def identity(info):
    return {'volume': info.st_dev, 'file_id': info.st_ino,
            'links': info.st_nlink, 'bytes': info.st_size,
            'mtime_ns': info.st_mtime_ns, 'creation_ns': info.st_ctime_ns,
            'attributes': info.st_file_attributes}


def pe_imports(data):
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Not a PE file')
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Missing PE header')
    machine, count = struct.unpack_from('<HH', data, pe + 4)
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    if magic not in (0x10B, 0x20B) or count > 96:
        raise ValueError('Unexpected PE layout')
    data_dir = optional + (96 if magic == 0x10B else 112)
    sections = []
    for index in range(count):
        off = optional + optional_size + index * 40
        virtual_size, rva, raw_size, raw_off = struct.unpack_from('<IIII', data, off + 8)
        sections.append((rva, max(virtual_size, raw_size), raw_off))
    def offset(rva):
        for start, size, raw in sections:
            if start <= rva < start + size:
                answer = raw + rva - start
                if answer >= len(data):
                    raise ValueError('PE RVA exceeds file')
                return answer
        if 0 <= rva < optional + optional_size:
            return rva
        raise ValueError('Unknown PE RVA')
    def name(rva):
        start = offset(rva); end = data.find(b'\0', start, start+256)
        if end < 0:
            raise ValueError('Unbounded PE name')
        value = data[start:end].decode('ascii').lower()
        if '/' in value or '\\' in value or ':' in value:
            raise ValueError('Non-basename import')
        return value
    imports, delayed = [], []
    imp_rva, imp_size = struct.unpack_from('<II', data, data_dir + 8)
    if imp_rva:
        start = offset(imp_rva)
        for index in range(min(imp_size // 20 + 1, 256)):
            entry = struct.unpack_from('<IIIII', data, start + index*20)
            if not any(entry):
                break
            imports.append(name(entry[3]))
        else:
            raise ValueError('Import list exceeds bound')
    delay_rva, delay_size = struct.unpack_from('<II', data, data_dir + 13*8)
    if delay_rva:
        start = offset(delay_rva)
        for index in range(min(delay_size // 32 + 1, 256)):
            entry = struct.unpack_from('<IIIIIIII', data, start+index*32)
            if not any(entry):
                break
            if entry[0] != 1:
                raise ValueError('Unsupported non-RVA delay imports')
            delayed.append(name(entry[1]))
        else:
            raise ValueError('Delay import list exceeds bound')
    return {'machine': hex(machine), 'imports': sorted(set(imports)), 'delay_imports': sorted(set(delayed))}


def run():
    seeds = [ROOT/'cmd/git.exe', ROOT/'bin/git.exe', ROOT/'mingw64/bin/git.exe',
             ROOT/'mingw64/libexec/git-core/git.exe']
    rows, edges, unresolved, system_imports, held = {}, [], [], set(), {}
    with ExitStack() as stack:
        queue = list(seeds)
        while queue:
            path = queue.pop(0)
            if str(path).casefold() in rows:
                continue
            if len(rows) >= 64:
                raise ValueError('Dependency closure exceeds 64 files')
            before = path.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_file_attributes & 0x400 or before.st_size > 32*1024*1024:
                raise ValueError('Unexpected binary type or size')
            handle = K32.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
            if handle == INVALID:
                raise C.WinError(C.get_last_error())
            try:
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except Exception:
                K32.CloseHandle(handle); raise
            stream = stack.enter_context(os.fdopen(fd, 'rb'))
            actual = os.fstat(stream.fileno())
            if (actual.st_dev, actual.st_ino, actual.st_size) != (before.st_dev, before.st_ino, before.st_size):
                raise ValueError('File identity changed while opening')
            raw = stream.read(32*1024*1024+1)
            if len(raw) != actual.st_size:
                raise ValueError('Binary size changed')
            names = aliases(path)
            if len(names) != actual.st_nlink:
                raise ValueError('Hardlink names/count differ')
            for alias in names:
                ap = Path(alias)
                if not ap.is_relative_to(ROOT):
                    raise ValueError('Hardlink alias outside the bounded installed Git root')
                ai = ap.lstat()
                if ai.st_file_attributes & 0x400 or (ai.st_dev, ai.st_ino) != (actual.st_dev, actual.st_ino):
                    raise ValueError('Hardlink identity differs')
            imports = pe_imports(raw)
            record = {'path':str(path),'metadata':identity(before),'sha256':hashlib.sha256(raw).hexdigest(),
                      'hardlink_aliases':names,'pe':imports}
            rows[str(path).casefold()] = record; held[str(path)] = (stream, identity(before), names)
            for dll in imports['imports']+imports['delay_imports']:
                candidate = path.parent / dll
                if candidate.is_file():
                    edges.append({'from':str(path),'import':dll,'resolved_installed_candidate':str(candidate)})
                    queue.append(candidate)
                elif dll.startswith(('api-ms-win-', 'ext-ms-win-')) or (Path(r'C:\Windows\System32') / dll).is_file():
                    system_imports.add(dll)
                else:
                    unresolved.append({'from':str(path),'import':dll})
        # Capture every actual alias and each protected ancestor ONCE. No
        # system/global/user Git config or credentials are opened.
        targets = set()
        for row in rows.values():
            for alias in row['hardlink_aliases']:
                current = Path(alias)
                while True:
                    targets.add(str(current))
                    if current == Path(r'C:\Program Files'):
                        break
                    current = current.parent
        if len(targets) > 256:
            raise ValueError('ACL inventory exceeds bound')
        pathfile=OUTPUT/'acl-paths.json'
        with pathfile.open('x',encoding='utf-8') as f:json.dump(sorted(targets),f)
        ps = r'''
param([string]$PathList)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
foreach($m in @('Microsoft.PowerShell.Utility','Microsoft.PowerShell.Security')){Import-Module (Join-Path $PSHOME "Modules\$m\$m.psd1")}
$trusted=@('S-1-5-18','S-1-5-32-544','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
$result=@(foreach($path in (Get-Content -LiteralPath $PathList -Raw|ConvertFrom-Json)){
    $item=Get-Item -LiteralPath $path -Force;$acl=Get-Acl -LiteralPath $path
    $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    $rules=@(foreach($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])){
        [ordered]@{sid=$rule.IdentityReference.Value;type=[string]$rule.AccessControlType;rights=[int64]$rule.FileSystemRights;inheritance=[string]$rule.InheritanceFlags;propagation=[string]$rule.PropagationFlags;inherited=$rule.IsInherited}})
    $writers=@($rules|Where-Object {$_.type -eq 'Allow' -and $_.sid -notin $trusted -and $_.propagation -notmatch 'InheritOnly' -and ($_.rights -band 0x500D0156)})
    [ordered]@{path=$path;owner_sid=$owner;trusted_owner=($owner -in $trusted);reparse=[bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint);sddl=$acl.Sddl;effective_untrusted_writers=$writers;aces=$rules}
})
ConvertTo-Json -InputObject $result -Depth 9
'''
        psfile=OUTPUT/'inspect-git-acls.ps1'
        with psfile.open('x',encoding='utf-8') as f:f.write(ps)
        proc=subprocess.run([r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe','-NoProfile','-NonInteractive','-File',str(psfile),'-PathList',str(pathfile)],capture_output=True,text=True,timeout=45)
        if proc.returncode:
            raise ValueError('Bounded ACL query failed: '+proc.stderr[-2048:])
        acls=json.loads(proc.stdout)
        for row in rows.values():
            stream, before, names=held[row['path']]
            if identity(Path(row['path']).lstat()) != before or aliases(Path(row['path'])) != names:
                raise ValueError('Binary metadata/alias set changed during capture')
            stream.seek(0)
            if hashlib.sha256(stream.read()).hexdigest() != row['sha256']:
                raise ValueError('Held source bytes changed')
        report={'schema':'cochem-git-static-custody-inspection/1','captured_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'scope':'Ordinary-user held-read binary bytes, PE imports/delay imports, complete actual hardlink names and SID/DACL ancestry. No Git/project command or SYSTEM execution.',
                'configured_launcher':str(seeds[0]),'binaries':list(rows.values()),'static_dependency_edges':edges,
                'windows_platform_import_names':sorted(system_imports),'unresolved_imports':unresolved,'acl_entries':acls,
                'all_observed_alias_owners_and_ancestors_trusted':all(r['trusted_owner'] and not r['reparse'] and not r['effective_untrusted_writers'] for r in acls),
                'static_non_system_import_closure_complete':not unresolved,
                'limitations':['Static dependency closure is not a dynamic loaded-module attestation or SYSTEM execution proof.','Windows platform/API-set imports use the OS trust boundary; their entire transitive platform closure is not inventoried.','Existing Git links were enumerated and inspected; no ACL/link changes and no weakening of single-link payload helpers.','Only metadata and bytes of installed binaries were read; no Git configuration, credentials, project state, protected DB, RAM or tasks were read or modified.']}
        output=OUTPUT/'git-static-custody.json'
        with output.open('x',encoding='utf-8') as f:json.dump(report,f,indent=2);f.write('\n')
        print(json.dumps({'files':len(rows),'alias_acl_paths':len(acls),'unresolved':unresolved,'trusted':report['all_observed_alias_owners_and_ancestors_trusted'],'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__=='__main__':run()
