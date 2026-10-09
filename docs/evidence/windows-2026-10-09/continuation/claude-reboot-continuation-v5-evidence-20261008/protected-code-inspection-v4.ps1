#Requires -Version 5.1
# Definitions only. Callers must hash-bind this source and provide the reviewed
# Assert-ProtectedPath and initialized CoChemStagedFileIdentity implementation.
# No native executable, task, registry, ACL or filesystem mutation is performed.
function Assert-CodeTreeOnce {
    param([Parameter(Mandatory=$true)][string]$Path)
    # Preserve the reviewed ancestry check, including the Program Files anchor.
    Assert-ProtectedPath $Path
    if (-not ('CoChemStagedFileIdentity' -as [type])) {
        throw 'Protected code inspection requires the initialized reviewed file-identity checker.'
    }
    if (-not ('CoChemProtectedCodeInspectionV4' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.AccessControl;
using System.Security.Principal;

public static class CoChemProtectedCodeInspectionV4 {
    const int MaximumEntries = 50000;
    const int MaximumMilliseconds = 600000;
    const long UntrustedWriteMask = 0x500D0116L;
    static readonly HashSet<string> Trusted = new HashSet<string>(StringComparer.Ordinal) {
        "S-1-5-18", "S-1-5-32-544",
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
    };

    static string DisplayRoot(string root) {
        string value = root.Replace("\r", " ").Replace("\n", " ");
        return value.Length <= 1024 ? value : value.Substring(0, 1024);
    }
    static IOException Refusal(string root, int count, Stopwatch clock, string kind) {
        return new IOException("Protected code inspection refused; root=" + DisplayRoot(root)
            + "; entries=" + count.ToString(CultureInfo.InvariantCulture)
            + "; elapsed_seconds=" + clock.Elapsed.TotalSeconds.ToString("F3", CultureInfo.InvariantCulture)
            + "; kind=" + kind + "; no Python was executed.");
    }
    static void CheckBounds(string root, int count, Stopwatch clock, int maxEntries, int maxMilliseconds) {
        if (count > maxEntries) throw Refusal(root, count, clock, "ENTRY_LIMIT");
        if (clock.ElapsedMilliseconds > maxMilliseconds) throw Refusal(root, count, clock, "TIME_LIMIT");
    }
    static FileSystemSecurity ReadAcl(FileSystemInfo item) {
        const AccessControlSections sections = AccessControlSections.Owner | AccessControlSections.Access;
        DirectoryInfo directory = item as DirectoryInfo;
        if (directory != null) return directory.GetAccessControl(sections);
        return ((FileInfo)item).GetAccessControl(sections);
    }
    internal static string AclRejection(FileSystemSecurity acl) {
        string owner = ((SecurityIdentifier)acl.GetOwner(typeof(SecurityIdentifier))).Value;
        if (!Trusted.Contains(owner)) return "UNTRUSTED_OWNER";
        foreach (FileSystemAccessRule rule in acl.GetAccessRules(true, true, typeof(SecurityIdentifier))) {
            string sid = ((SecurityIdentifier)rule.IdentityReference).Value;
            if (rule.AccessControlType == AccessControlType.Allow && !Trusted.Contains(sid)
                && (rule.PropagationFlags & PropagationFlags.InheritOnly) == 0
                && (((long)rule.FileSystemRights & UntrustedWriteMask) != 0))
                return "UNTRUSTED_WRITER";
        }
        return null;
    }

    // Production always supplies real ACL reads and these fixed bounds. The
    // internal core permits disposable ordinary-token tests of the traversal
    // with explicitly synthetic trusted ACLs, without modifying any host ACL.
    public static int Scan(string root, Action<FileStream, string> checkFileIdentity) {
        return ScanCore(root, ReadAcl, checkFileIdentity, MaximumEntries, MaximumMilliseconds);
    }
    internal static int ScanCore(string root, Func<FileSystemInfo, FileSystemSecurity> readAcl,
        Action<FileStream, string> checkFileIdentity, int maxEntries, int maxMilliseconds) {
        if (readAcl == null || checkFileIdentity == null || maxEntries <= 0 || maxMilliseconds < 0)
            throw new ArgumentException("Inspection dependencies or bounds are invalid.");
        string fullRoot = Path.GetFullPath(root);
        Stopwatch clock = Stopwatch.StartNew();
        int count = 0;
        var queue = new Queue<FileSystemInfo>();
        try {
            FileAttributes attributes = File.GetAttributes(fullRoot);
            queue.Enqueue((attributes & FileAttributes.Directory) != 0
                ? (FileSystemInfo)new DirectoryInfo(fullRoot) : new FileInfo(fullRoot));
        } catch { throw Refusal(fullRoot, count, clock, "ROOT_METADATA_FAILED"); }
        while (queue.Count != 0) {
            ++count;
            CheckBounds(fullRoot, count, clock, maxEntries, maxMilliseconds);
            FileSystemInfo item = queue.Dequeue();
            FileAttributes attributes;
            try {
                item.Refresh();
                if (!item.Exists) throw new IOException();
                attributes = item.Attributes;
            } catch { throw Refusal(fullRoot, count, clock, "ENTRY_METADATA_FAILED"); }
            if ((attributes & FileAttributes.ReparsePoint) != 0)
                throw Refusal(fullRoot, count, clock, "REPARSE_ENTRY");
            string rejected;
            try { rejected = AclRejection(readAcl(item)); }
            catch { throw Refusal(fullRoot, count, clock, "ACL_READ_FAILED"); }
            if (rejected != null) throw Refusal(fullRoot, count, clock, rejected);
            if ((attributes & FileAttributes.Directory) != 0) {
                IEnumerator<FileSystemInfo> children = null;
                try { children = ((DirectoryInfo)item).EnumerateFileSystemInfos().GetEnumerator(); }
                catch { throw Refusal(fullRoot, count, clock, "ENUMERATION_FAILED"); }
                using (children) {
                    while (true) {
                        CheckBounds(fullRoot, count, clock, maxEntries, maxMilliseconds);
                        bool more;
                        try { more = children.MoveNext(); }
                        catch { throw Refusal(fullRoot, count, clock, "ENUMERATION_FAILED"); }
                        if (!more) break;
                        // Bound queued metadata too; do not materialize an
                        // unbounded directory before reaching the visit cap.
                        if ((long)count + queue.Count + 1 > maxEntries)
                            throw Refusal(fullRoot, count, clock, "ENTRY_LIMIT");
                        queue.Enqueue(children.Current);
                    }
                }
            } else {
                FileStream stream;
                try { stream = new FileStream(item.FullName, FileMode.Open, FileAccess.Read, FileShare.Read); }
                catch { throw Refusal(fullRoot, count, clock, "FILE_OPEN_FAILED"); }
                using (stream) {
                    try { checkFileIdentity(stream, item.FullName); }
                    catch { throw Refusal(fullRoot, count, clock, "FILE_IDENTITY_REFUSED"); }
                }
            }
        }
        CheckBounds(fullRoot, count, clock, maxEntries, maxMilliseconds);
        return count;
    }
}
'@
    }
    # Bind directly to the reviewed existing method, avoiding a PowerShell
    # scriptblock invocation for every file. Its handle stays open for Check.
    $method = [CoChemStagedFileIdentity].GetMethod('Check', [type[]]@([IO.FileStream], [string]))
    if ($null -eq $method) { throw 'Reviewed file-identity method is unavailable.' }
    $checker = [Delegate]::CreateDelegate([Action[IO.FileStream,string]], $method)
    [CoChemProtectedCodeInspectionV4]::Scan($Path, $checker)
}
