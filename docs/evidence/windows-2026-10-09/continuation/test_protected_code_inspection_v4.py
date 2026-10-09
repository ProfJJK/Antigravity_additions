"""Ordinary-token Windows fixtures for the frozen protected-code scanner.

The real scanner core and pinned native file-identity checker run on disposable
workspace paths. ACL inputs are synthetic objects; no host ACL is changed.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest


HERE = Path(__file__).resolve().parent
SCANNER = HERE / "protected-code-inspection-v4.ps1"
SCANNER_SHA = "5c01543cbb8b8d64b2b9f1bab4a13f87f8ff9e1ab3e82dfb6fc547144c77b95d"
IDENTITY = Path(r"D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions\scripts\stage_aetherdesk_427_payloads.ps1")
IDENTITY_SHA = "0300b82731c0fddb9fad3e2ddb6a20b8368cfa91faf7aceb821752c65b5f6e0b"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
WRITE_BITS = (0x2, 0x4, 0x10, 0x100, 0x10000, 0x40000, 0x80000, 0x10000000, 0x40000000)


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


HARNESS = r"""
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Principal;
using System.Threading;
using Microsoft.Win32.SafeHandles;

public static class InspectionFixturesV4 {
    const string SystemSid = "S-1-5-18";
    const string AdminSid = "S-1-5-32-544";
    const string InstallerSid = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";
    const string UsersSid = "S-1-5-32-545";
    static MethodInfo scan, aclCheck;
    static readonly BindingFlags Hidden = BindingFlags.Static | BindingFlags.NonPublic;

    // Build only an in-memory security descriptor. Generic access bits must be
    // represented as raw ACE masks, since public FileSystemAccessRule validation
    // otherwise rejects those masks before the scanner can inspect them.
    static FileSystemSecurity Acl(string owner, string trustee, int mask,
        AceQualifier qualifier, AceFlags flags) {
        SecurityIdentifier sid = new SecurityIdentifier(owner);
        RawAcl dacl = new RawAcl(2, 1);
        if (trustee != null) dacl.InsertAce(0, new CommonAce(flags, qualifier, mask,
            new SecurityIdentifier(trustee), false, null));
        RawSecurityDescriptor descriptor = new RawSecurityDescriptor(
            ControlFlags.DiscretionaryAclPresent, sid, sid, null, dacl);
        byte[] data = new byte[descriptor.BinaryLength];
        descriptor.GetBinaryForm(data, 0);
        FileSecurity result = new FileSecurity();
        result.SetSecurityDescriptorBinaryForm(data);
        return result;
    }
    static FileSystemSecurity TrustedAcl() {
        return Acl(SystemSid, null, 0, AceQualifier.AccessAllowed, AceFlags.None);
    }
    static string Reject(FileSystemSecurity acl) {
        return (string)aclCheck.Invoke(null, new object[] { acl });
    }
    static int Scan(string root, Func<FileSystemInfo, FileSystemSecurity> readAcl,
        Action<FileStream, string> check, int maximumEntries, int maximumMilliseconds) {
        try {
            return (int)scan.Invoke(null, new object[] {
                root, readAcl, check, maximumEntries, maximumMilliseconds });
        } catch (TargetInvocationException error) {
            throw error.InnerException;
        }
    }
    static Dictionary<string, object> Refusal(Action run) {
        try { run(); throw new Exception("Fixture unexpectedly succeeded."); }
        catch (IOException error) {
            return new Dictionary<string, object> {
                { "message", error.Message }, { "exception", error.GetType().FullName }
            };
        }
    }
    static void Write(string path) { File.WriteAllText(path, "inert inspection fixture"); }

    public static Dictionary<string, object> Run(Type scanner,
        Action<FileStream, string> realIdentity, string root) {
        scan = scanner.GetMethod("ScanCore", Hidden);
        aclCheck = scanner.GetMethod("AclRejection", Hidden);
        if (scan == null || aclCheck == null) throw new Exception("Internal fixture seam missing.");
        Dictionary<string, object> result = new Dictionary<string, object>();
        Dictionary<string, object> acls = new Dictionary<string, object>();
        foreach (string owner in new string[] { SystemSid, AdminSid, InstallerSid }) {
            acls["owner:" + owner] = Reject(Acl(owner, null, 0, AceQualifier.AccessAllowed, AceFlags.None));
            acls["trusted-writer:" + owner] = Reject(Acl(SystemSid, owner,
                unchecked((int)0x500D0116), AceQualifier.AccessAllowed, AceFlags.None));
        }
        foreach (string owner in new string[] { UsersSid, "S-1-1-0" })
            acls["owner:" + owner] = Reject(Acl(owner, null, 0, AceQualifier.AccessAllowed, AceFlags.None));
        foreach (int mask in new int[] { 2, 4, 16, 256, 65536, 262144, 524288, 268435456, 1073741824 })
            acls["write:" + mask] = Reject(Acl(SystemSid, UsersSid, mask,
                AceQualifier.AccessAllowed, AceFlags.None));
        acls["read-execute"] = Reject(Acl(SystemSid, UsersSid, 0x1200A9,
            AceQualifier.AccessAllowed, AceFlags.None));
        acls["deny-writer"] = Reject(Acl(SystemSid, UsersSid, 0x1F01FF,
            AceQualifier.AccessDenied, AceFlags.None));
        acls["inherit-only-writer"] = Reject(Acl(SystemSid, UsersSid, 2,
            AceQualifier.AccessAllowed, AceFlags.ObjectInherit | AceFlags.ContainerInherit | AceFlags.InheritOnly));
        acls["inherited-writer"] = Reject(Acl(SystemSid, UsersSid, 2,
            AceQualifier.AccessAllowed, AceFlags.Inherited));
        acls["effective-container-writer"] = Reject(Acl(SystemSid, UsersSid, 2,
            AceQualifier.AccessAllowed, AceFlags.ContainerInherit));
        result["acls"] = acls;
        result["maximum_entries"] = scanner.GetField("MaximumEntries", Hidden).GetRawConstantValue();
        result["maximum_milliseconds"] = scanner.GetField("MaximumMilliseconds", Hidden).GetRawConstantValue();
        result["write_mask"] = scanner.GetField("UntrustedWriteMask", Hidden).GetRawConstantValue();
        WindowsPrincipal principal = new WindowsPrincipal(WindowsIdentity.GetCurrent());
        result["is_administrator"] = principal.IsInRole(WindowsBuiltInRole.Administrator);

        string tree = Path.Combine(root, "tree");
        Directory.CreateDirectory(Path.Combine(tree, "nested", "deeper"));
        Write(Path.Combine(tree, "a.txt"));
        Write(Path.Combine(tree, "nested", "b.txt"));
        Write(Path.Combine(tree, "nested", "deeper", "c.txt"));
        List<FileStream> streams = new List<FileStream>();
        List<SafeFileHandle> handles = new List<SafeFileHandle>();
        int lockedDuringCheck = 0, verified = 0, aclReads = 0;
        int count = Scan(tree, delegate(FileSystemInfo entry) { aclReads++; return TrustedAcl(); },
            delegate(FileStream stream, string path) {
                if (!stream.CanRead || stream.SafeFileHandle.IsClosed)
                    throw new Exception("Callback received a closed handle.");
                streams.Add(stream); handles.Add(stream.SafeFileHandle);
                try {
                    using (FileStream writer = new FileStream(path, FileMode.Open, FileAccess.Write, FileShare.None)) {}
                    throw new Exception("Held read handle did not block exclusive write.");
                } catch (IOException) { lockedDuringCheck++; }
                realIdentity(stream, path); verified++;
            }, 50000, 600000);
        bool closedAfter = true;
        for (int index = 0; index < streams.Count; ++index)
            closedAfter &= !streams[index].CanRead && handles[index].IsClosed;
        result["tree"] = new Dictionary<string, object> {
            { "entries", count }, { "acl_reads", aclReads }, { "verified_files", verified },
            { "held_during_check", lockedDuringCheck }, { "closed_after", closedAfter }
        };
        result["single_file_entries"] = Scan(Path.Combine(tree, "a.txt"),
            delegate(FileSystemInfo entry) { return TrustedAcl(); }, realIdentity, 50000, 600000);

        FileStream refusedStream = null; SafeFileHandle refusedHandle = null;
        Dictionary<string, object> callbackRefusal = Refusal(delegate {
            Scan(Path.Combine(tree, "a.txt"), delegate(FileSystemInfo entry) { return TrustedAcl(); },
                delegate(FileStream stream, string path) {
                    refusedStream = stream; refusedHandle = stream.SafeFileHandle;
                    throw new IOException("Synthetic callback refusal.");
                }, 50000, 600000);
        });
        callbackRefusal["closed_after"] = refusedStream != null && !refusedStream.CanRead && refusedHandle.IsClosed;
        result["callback_refusal"] = callbackRefusal;

        foreach (string kind in new string[] { "UNTRUSTED_OWNER", "UNTRUSTED_WRITER", "ACL_READ_FAILED" }) {
            int calls = 0;
            Dictionary<string, object> rejected = Refusal(delegate {
                Scan(tree, delegate(FileSystemInfo entry) {
                    if (kind == "ACL_READ_FAILED") throw new IOException("Synthetic ACL failure.");
                    return kind == "UNTRUSTED_OWNER"
                        ? Acl(UsersSid, null, 0, AceQualifier.AccessAllowed, AceFlags.None)
                        : Acl(SystemSid, UsersSid, 2, AceQualifier.AccessAllowed, AceFlags.None);
                }, delegate(FileStream stream, string path) { calls++; }, 50000, 600000);
            });
            rejected["identity_calls"] = calls;
            result[kind] = rejected;
        }
        result["missing_root"] = Refusal(delegate {
            Scan(Path.Combine(root, "absent-root"), delegate(FileSystemInfo entry) { return TrustedAcl(); },
                realIdentity, 50000, 600000);
        });
        string hardlinkRoot = Path.Combine(root, "hardlink");
        int hardlinkCalls = 0;
        Dictionary<string, object> hardlink = Refusal(delegate {
            Scan(hardlinkRoot, delegate(FileSystemInfo entry) { return TrustedAcl(); },
                delegate(FileStream stream, string path) { hardlinkCalls++; realIdentity(stream, path); }, 50000, 600000);
        });
        hardlink["identity_calls"] = hardlinkCalls;
        result["hardlink"] = hardlink;

        int reparseCalls = 0;
        string junction = Path.Combine(root, "reparse", "junction");
        Dictionary<string, object> reparse = Refusal(delegate {
            Scan(Path.Combine(root, "reparse"), delegate(FileSystemInfo entry) { return TrustedAcl(); },
                delegate(FileStream stream, string path) { reparseCalls++; realIdentity(stream, path); }, 50000, 600000);
        });
        reparse["identity_calls"] = reparseCalls;
        reparse["actual_reparse_attribute"] = (File.GetAttributes(junction) & FileAttributes.ReparsePoint) != 0;
        result["reparse"] = reparse;
        result["reparse_root"] = Refusal(delegate {
            Scan(junction, delegate(FileSystemInfo entry) { return TrustedAcl(); }, realIdentity, 50000, 600000);
        });

        string queue = Path.Combine(root, "queue");
        Directory.CreateDirectory(queue);
        for (int index = 0; index < 49999; ++index)
            using (FileStream file = File.Create(Path.Combine(queue, index.ToString("D5") + ".txt"))) {}
        int boundaryCalls = 0, boundaryAclReads = 0;
        Stopwatch boundaryClock = Stopwatch.StartNew();
        result["entry_boundary"] = new Dictionary<string, object> {
            { "entries", Scan(queue, delegate(FileSystemInfo entry) { boundaryAclReads++; return TrustedAcl(); },
                delegate(FileStream stream, string path) { boundaryCalls++; }, 50000, 600000) },
            { "identity_calls", boundaryCalls }, { "acl_reads", boundaryAclReads },
            { "elapsed_seconds", boundaryClock.Elapsed.TotalSeconds }
        };
        using (FileStream file = File.Create(Path.Combine(queue, "overflow.txt"))) {}
        int overflowCalls = 0, overflowAclReads = 0;
        Dictionary<string, object> overflow = Refusal(delegate {
            Scan(queue, delegate(FileSystemInfo entry) { overflowAclReads++; return TrustedAcl(); },
                delegate(FileStream stream, string path) { overflowCalls++; }, 50000, 600000);
        });
        overflow["identity_calls"] = overflowCalls; overflow["acl_reads"] = overflowAclReads;
        overflow["fixture_children"] = 50000;
        result["entry_overflow"] = overflow;

        int timeCalls = 0;
        Dictionary<string, object> timeRefusal = Refusal(delegate {
            Scan(tree, delegate(FileSystemInfo entry) { Thread.Sleep(30); return TrustedAcl(); },
                delegate(FileStream stream, string path) { timeCalls++; }, 50000, 1);
        });
        timeRefusal["identity_calls"] = timeCalls;
        result["time_limit"] = timeRefusal;
        return result;
    }
}
"""


@pytest.fixture(scope="session")
def windows_results(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("Actual Windows filesystem, ACL objects and native handle identity are required.")
    assert hashlib.sha256(SCANNER.read_bytes()).hexdigest() == SCANNER_SHA
    assert hashlib.sha256(IDENTITY.read_bytes()).hexdigest() == IDENTITY_SHA
    root = tmp_path_factory.mktemp("protected-inspection-v4")
    # Every created path stays in the explicit workspace test directory.
    assert HERE.parent in root.resolve().parents
    hardlink = root / "hardlink"
    hardlink.mkdir()
    (hardlink / "original.txt").write_text("inert hardlink fixture", encoding="utf-8")
    os.link(hardlink / "original.txt", hardlink / "alias.txt")
    (root / "target").mkdir()
    (root / "target" / "must-not-open.txt").write_text("junction target", encoding="utf-8")
    (root / "reparse").mkdir()
    harness = root / "fixtures.cs"
    harness.write_text(HARNESS, encoding="utf-8")
    code = rf"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(IDENTITY)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'Pinned identity source parse failure.'}}
$functions=@($ast.FindAll({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Initialize-FileIdentity'}},$true))
if($functions.Count -ne 1){{throw 'Pinned identity initializer missing.'}}
. ([scriptblock]::Create($functions[0].Extent.Text));Initialize-FileIdentity
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(SCANNER)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'Scanner source parse failure.'}}
$addTypes=@($ast.FindAll({{param($n)$n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -ceq 'Add-Type'}},$true))
if($addTypes.Count -ne 1){{throw 'Scanner core initializer missing.'}}
$source=@($addTypes[0].CommandElements | Where-Object {{$_ -is [Management.Automation.Language.StringConstantExpressionAst] -and $_.StringConstantType -eq [Management.Automation.Language.StringConstantType]::SingleQuotedHereString}})
if($source.Count -ne 1){{throw 'Scanner CSharp source missing.'}}
Add-Type -TypeDefinition $source[0].Value
Add-Type -TypeDefinition ([IO.File]::ReadAllText({quote(harness)}))
New-Item -ItemType Junction -Path {quote(root / 'reparse' / 'junction')} -Target {quote(root / 'target')} | Out-Null
$method=[CoChemStagedFileIdentity].GetMethod('Check',[type[]]@([IO.FileStream],[string]))
$checker=[Delegate]::CreateDelegate([Action[IO.FileStream,string]],$method)
[InspectionFixturesV4]::Run([CoChemProtectedCodeInspectionV4],$checker,{quote(root)}) | ConvertTo-Json -Depth 8
"""
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    report = HERE / f"protected-inspection-v4-fixtures-{run_id}"
    report.with_suffix(".command.ps1").write_text(code, encoding="utf-8")
    completed = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True, text=True, timeout=300,
    )
    report.with_suffix(".stdout.json").write_text(completed.stdout, encoding="utf-8")
    report.with_suffix(".stderr.txt").write_text(completed.stderr, encoding="utf-8")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["is_administrator"] is False, "These fixtures must run with the ordinary Windows token."
    return result


def refusal(value, kind, entries=None):
    assert value["exception"] == "System.IO.IOException"
    message = value["message"]
    assert message.startswith("Protected code inspection refused; root=")
    assert "; elapsed_seconds=" in message
    assert f"; kind={kind}; no Python was executed." in message
    if entries is not None:
        assert f"; entries={entries};" in message


def test_exact_frozen_scanner_and_pinned_identity_source():
    assert hashlib.sha256(SCANNER.read_bytes()).hexdigest() == SCANNER_SHA
    assert hashlib.sha256(IDENTITY.read_bytes()).hexdigest() == IDENTITY_SHA


def test_only_export_preserves_ancestry_and_direct_delegate_binding():
    source = SCANNER.read_text(encoding="utf-8-sig")
    assert source.count("function ") == 1
    assert "function Assert-CodeTreeOnce" in source
    assert "Assert-ProtectedPath $Path" in source
    assert "[Delegate]::CreateDelegate([Action[IO.FileStream,string]], $method)" in source
    assert "[CoChemProtectedCodeInspectionV4]::Scan($Path, $checker)" in source


@pytest.mark.parametrize("sid", [SYSTEM, ADMINISTRATORS, INSTALLER])
def test_each_trusted_owner_and_writer_is_permitted(windows_results, sid):
    assert windows_results["acls"]["owner:" + sid] is None
    assert windows_results["acls"]["trusted-writer:" + sid] is None


@pytest.mark.parametrize("sid", ["S-1-5-32-545", "S-1-1-0"])
def test_untrusted_owners_are_refused(windows_results, sid):
    assert windows_results["acls"]["owner:" + sid] == "UNTRUSTED_OWNER"


@pytest.mark.parametrize("mask", WRITE_BITS)
def test_every_effective_untrusted_write_mask_bit_is_refused(windows_results, mask):
    assert windows_results["acls"]["write:" + str(mask)] == "UNTRUSTED_WRITER"


@pytest.mark.parametrize("name", ["read-execute", "deny-writer", "inherit-only-writer"])
def test_read_deny_and_inherit_only_rules_are_permitted(windows_results, name):
    assert windows_results["acls"][name] is None


@pytest.mark.parametrize("name", ["inherited-writer", "effective-container-writer"])
def test_inheritance_does_not_hide_effective_write_access(windows_results, name):
    assert windows_results["acls"][name] == "UNTRUSTED_WRITER"


def test_non_reparse_tree_counts_directories_and_files_once(windows_results):
    assert windows_results["tree"] == {
        "entries": 6, "acl_reads": 6, "verified_files": 3,
        "held_during_check": 3, "closed_after": True,
    }
    assert windows_results["single_file_entries"] == 1


def test_native_identity_callback_holds_handles_and_closes_them_after_refusal(windows_results):
    refusal(windows_results["callback_refusal"], "FILE_IDENTITY_REFUSED", 1)
    assert windows_results["callback_refusal"]["closed_after"] is True


@pytest.mark.parametrize("kind", ["UNTRUSTED_OWNER", "UNTRUSTED_WRITER", "ACL_READ_FAILED"])
def test_acl_refusals_precede_file_identity(windows_results, kind):
    refusal(windows_results[kind], kind, 1)
    assert windows_results[kind]["identity_calls"] == 0


def test_missing_root_has_bounded_diagnostic(windows_results):
    refusal(windows_results["missing_root"], "ROOT_METADATA_FAILED", 0)


def test_actual_hardlink_is_refused_by_pinned_native_identity(windows_results):
    refusal(windows_results["hardlink"], "FILE_IDENTITY_REFUSED", 2)
    assert windows_results["hardlink"]["identity_calls"] == 1


def test_actual_junction_and_reparse_root_are_refused_before_identity(windows_results):
    refusal(windows_results["reparse"], "REPARSE_ENTRY", 2)
    refusal(windows_results["reparse_root"], "REPARSE_ENTRY", 1)
    assert windows_results["reparse"]["actual_reparse_attribute"] is True
    assert windows_results["reparse"]["identity_calls"] == 0


def test_full_50000_entry_boundary_and_queue_overflow(windows_results):
    boundary = windows_results["entry_boundary"]
    assert boundary["entries"] == boundary["acl_reads"] == 50000
    assert boundary["identity_calls"] == 49999
    overflow = windows_results["entry_overflow"]
    refusal(overflow, "ENTRY_LIMIT", 1)
    assert overflow["fixture_children"] == 50000
    assert overflow["acl_reads"] == 1
    assert overflow["identity_calls"] == 0


def test_time_bound_fails_during_directory_processing(windows_results):
    refusal(windows_results["time_limit"], "TIME_LIMIT", 1)
    assert windows_results["time_limit"]["identity_calls"] == 0


def test_production_bounds_and_write_mask_are_fixed(windows_results):
    assert windows_results["maximum_entries"] == 50000
    assert windows_results["maximum_milliseconds"] == 600000
    assert windows_results["write_mask"] == sum(WRITE_BITS) == 0x500D0116
