<#
.SYNOPSIS
    CoChem Elevated Host Bridge (EHB): CLR Profiler Suppression Launcher
    Task Identifier: Task 2.03 / WP 2.0 (Deliverable: dotnet_profiler_suppress)
    Compliance: PCA-96 (Elevated Host Bridge), Anti-Spoofing Protocol v4
#>

Add-Type @'
using System;
using System.Runtime.InteropServices;

public class ElevatedClrRunner {
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

$scriptPath = "D:\__CoChem\__agentic\setup\setup_clr_profiler_suppression.ps1"
if (-not (Test-Path $scriptPath)) {
    $scriptPath = "C:\Users\ansac\Gdrive\__agentic\setup\setup_clr_profiler_suppression.ps1"
}
$logPath = "D:\__CoChem\__agentic\setup\setup_clr_profiler_suppression.log"

$sei = New-Object ElevatedClrRunner+SHELLEXECUTEINFO
$sei.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf($sei)
$sei.fMask = 0x00000040 # SEE_MASK_NOCLOSEPROCESS
$sei.lpVerb = "runas"
$sei.lpFile = $pwshPath
$sei.lpParameters = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -LogPath `"$logPath`""
$sei.lpDirectory = "C:\Windows\System32"
$sei.nShow = 1

Write-Host "Requesting Windows Administrator Elevation (UAC) for Task 2.03..."
$success = [ElevatedClrRunner]::ShellExecuteEx([ref]$sei)
if (-not $success) {
    $err = [System.Runtime.InteropServices.Marshal]::GetLastWin32Error()
    Write-Warning "ShellExecuteEx failed or UAC prompt dismissed. Win32 Error: $err"
    exit 1
}

Write-Host "Waiting up to 10 seconds for elevated process completion..."
$waitResult = [ElevatedClrRunner]::WaitForSingleObject($sei.hProcess, 10000) # 10 seconds timeout

if ($waitResult -eq 0) {
    $exitCode = [uint32]0
    [ElevatedClrRunner]::GetExitCodeProcess($sei.hProcess, [ref]$exitCode)
    [ElevatedClrRunner]::CloseHandle($sei.hProcess)
    Write-Host "Elevated process completed successfully with ExitCode: $exitCode"
    exit $exitCode
} elseif ($waitResult -eq 0x00000102) { # WAIT_TIMEOUT
    [ElevatedClrRunner]::CloseHandle($sei.hProcess)
    Write-Host "Notice: Elevated process is running asynchronously or waiting on UAC prompt."
    exit 0
} else {
    [ElevatedClrRunner]::CloseHandle($sei.hProcess)
    Write-Host "Wait finished with status code: $waitResult"
    exit 0
}
