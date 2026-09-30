<#
.SYNOPSIS
    CoChem Elevated Host Bridge (EHB): NVMe HMB Stabilization Launcher
    Task Identifier: Task 191.02 / WP 1.0 (Task 1.01 / Task 1.02)
    Compliance: PCA-96, PCA-99, PCA-105 (Anti-Deadlock Bounded Wait Invariant)
#>

Add-Type @'
using System;
using System.Runtime.InteropServices;

public class ElevatedHmbRunner {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct SHELLEXECUTEINFO {
        public int cbSize;
        public uint fMask;
        public IntPtr hwnd;
        public string lpVerb;
        public string lpFile;
        public string lpParameters;
        public string lpDirectory;
        public int nShow;
        public IntPtr hInstApp;
        public IntPtr lpIDList;
        public string lpClass;
        public IntPtr hkeyClass;
        public uint dwHotKey;
        public IntPtr hIconOrMonitor;
        public IntPtr hProcess;
    }

    [DllImport("shell32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool ShellExecuteEx(ref SHELLEXECUTEINFO lpExecInfo);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern uint WaitForSingleObject(IntPtr hHandle, uint dwMilliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool GetExitCodeProcess(IntPtr hProcess, out uint lpExitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr hObject);
}
'@ -ErrorAction SilentlyContinue

$pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
if ($pwshCmd) {
    $pwshPath = $pwshCmd.Source
} else {
    throw "pwsh.exe (PowerShell 7+) was not found in PATH."
}

# Dynamic Path Resolution
$baseDir = if ($env:USERPROFILE) {
    Join-Path $env:USERPROFILE "Gdrive\__agentic\setup"
} else {
    [System.IO.Path]::GetFullPath(".")
}

$scriptPath = Join-Path $baseDir "setup_nvme_hmb_guard.ps1"
if (-not (Test-Path $scriptPath)) {
    $scriptPath = "D:\__CoChem\__agentic\setup\setup_nvme_hmb_guard.ps1"
}
$logPath = Join-Path $baseDir "setup_nvme_hmb_guard.log"

$sei = New-Object ElevatedHmbRunner+SHELLEXECUTEINFO
$sei.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf($sei)
$sei.fMask = 0x00000040 # SEE_MASK_NOCLOSEPROCESS
$sei.lpVerb = "runas"
$sei.lpFile = $pwshPath
$sei.lpParameters = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$sei.lpDirectory = "C:\Windows\System32"
$sei.nShow = 1

Write-Host "Requesting Windows Administrator Elevation (UAC) for Task 191.02..."
$success = [ElevatedHmbRunner]::ShellExecuteEx([ref]$sei)
if (-not $success) {
    $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
    Write-Warning "ShellExecuteEx failed or elevation prompt dismissed. Win32 Error: $err"
    exit 1
}

Write-Host "Waiting up to 10 seconds for elevated process completion..."
# Bounded wait (10,000 ms) - Strict PCA-105 Compliance (No unbounded 0xFFFFFFFF deadlock)
$waitResult = [ElevatedHmbRunner]::WaitForSingleObject($sei.hProcess, 10000)

if ($waitResult -eq 0) {
    $exitCode = [uint32]0
    [ElevatedHmbRunner]::GetExitCodeProcess($sei.hProcess, [ref]$exitCode)
    [ElevatedHmbRunner]::CloseHandle($sei.hProcess)
    Write-Host "Elevated process completed with ExitCode: $exitCode"
    exit $exitCode
} elseif ($waitResult -eq 0x00000102) { # WAIT_TIMEOUT
    [ElevatedHmbRunner]::CloseHandle($sei.hProcess)
    Write-Host "Notice: Elevated process is running asynchronously or waiting on elevation broker."
    exit 0
} else {
    [ElevatedHmbRunner]::CloseHandle($sei.hProcess)
    Write-Host "Wait completed with Win32 status code: $waitResult"
    exit 0
}
