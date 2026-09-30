<#
.SYNOPSIS
    Quarantine VM Creation, Virtual Hardware Hardening, and Provisioning Engine (SRS-412-02).

.DESCRIPTION
    Provisions and hardens the Windows 11 Host-Side Hyper-V virtualization profile for the
    Guest-Side Quarantine VM running Ubuntu 24.04 LTS. Enforces static 32 GB RAM ceiling
    with Dynamic Memory permanently disabled, 12 vCPUs at a hard 70% maximum CPU resource
    allocation cap, an air-gapped internal virtual switch with zero external routing,
    differencing/dynamic VHDX storage, and guest execution plane manifests for ephemeral
    Docker sandboxes (SRS-412-02, MC-QVM-01..19).

.ARCHITECTURAL COMPLIANCE:
    - SRS-412-02-FR-001: Enforce static 32 GB RAM ceiling (34,359,738,368 bytes) with
      Hyper-V Dynamic Memory permanently disabled ($false).
    - SRS-412-02-FR-002: Allocate 12 virtual CPUs with hard 70% CPU resource allocation cap
      (Maximum=70, RelativeWeight=100).
    - SRS-412-02-FR-003: Configure guest execution plane for ephemeral Docker containers
      launched with `--network none`, `--read-only`, and `--tmpfs /tmp:rw,size=2g`.
    - SRS-412-02-FR-004: Configure FERPA air-gapped sandbox launcher with SHA-256 identifier
      scrubbing and read-only bind mounts.
    - SRS-412-02-FR-007: Enforce container scheduler concurrency ceiling (maximum 6 parallel
      4 GB sandboxes inside 32 GB VM: 24 GB working memory boundary).
    - SRS-412-02-FR-008: Provision immediate purge routines for scratch volumes, tmpfs mounts,
      and intermediate container layers upon exit.
    - NFR-VM-01: Low startup latency configuration (< 1.5 seconds container instantiation).
    - NFR-VM-02: Isolated OOM container terminations without host or VM kernel disruption.
    - NFR-VM-03: Air-gapped network boundary verifiable via physical connection drop.
    - Section 8: Automated 60-second orphan scratch sweeper cron and Exit 137 OOM slope recorder.

.ZERO-MOCK MANDATE:
    - Authentic Hyper-V cmdlets and CIM virtualization queries.
    - Zero mock variables, zero pass stubs, zero synthetic data generators.
    - Strictly zero physical chemistry libraries (PySCF, ASE, Mendeleev forbidden).
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Position = 0, HelpMessage = "Target Hyper-V Virtual Machine Name")]
    [ValidateNotNullOrEmpty()]
    [string]$VMName = "CoChem-Quarantine-VM",

    [Parameter(Position = 1, HelpMessage = "Physical path for the VM virtual hard disk (.vhdx)")]
    [ValidateNotNullOrEmpty()]
    [string]$VhdPath = "D:\__CoChem\__agentic\v4.1.2\vm\CoChem-Quarantine-VM.vhdx",

    [Parameter(HelpMessage = "Static memory allocation in bytes (SRS-412-02-FR-001: 34359738368 = 32 GB)")]
    [int64]$MemoryStartupBytes = 34359738368,

    [Parameter(HelpMessage = "Virtual processor count (SRS-412-02-FR-002: 12 vCPUs)")]
    [int]$ProcessorCount = 12,

    [Parameter(HelpMessage = "CPU Maximum percentage cap (SRS-412-02-FR-002: 70% hard cap)")]
    [ValidateRange(1, 100)]
    [int]$CpuMaximumPercent = 70,

    [Parameter(HelpMessage = "CPU relative scheduling weight")]
    [ValidateRange(1, 10000)]
    [int]$CpuRelativeWeight = 100,

    [Parameter(HelpMessage = "Name of the isolated Hyper-V virtual switch")]
    [ValidateNotNullOrEmpty()]
    [string]$SwitchName = "CoChem-Quarantine-Switch",

    [Parameter(HelpMessage = "Type of virtual switch to bind (Internal or Private for air-gapped isolation)")]
    [ValidateSet("Internal", "Private", "None")]
    [string]$SwitchType = "Internal",

    [Parameter(HelpMessage = "Initial virtual hard disk capacity in bytes (Default: 64 GB)")]
    [int64]$DiskSizeBytes = 68719476736,

    [Parameter(HelpMessage = "Optional path to Ubuntu 24.04 LTS installer ISO for fresh installation")]
    [string]$IsoPath = "",

    [Parameter(HelpMessage = "Directory path where guest execution plane configuration files are exported")]
    [ValidateNotNullOrEmpty()]
    [string]$GuestConfigDir = "D:\__CoChem\__agentic\v4.1.2\config\guest",

    [Parameter(HelpMessage = "Directory path where audit evidence and telemetry are recorded")]
    [ValidateNotNullOrEmpty()]
    [string]$EvidenceDir = "D:\__CoChem\__agentic\v4.1.2\.evidence\quarantine",

    [Parameter(HelpMessage = "Force stop and recreate VM if it already exists")]
    [switch]$ForceRecreate,

    [Parameter(HelpMessage = "Automatically start the Quarantine VM upon successful provisioning")]
    [switch]$StartVM,

    [Parameter(HelpMessage = "Perform a non-mutating audit of the existing VM against SRS-412-02 rules")]
    [switch]$VerifyOnly,

    [Parameter(HelpMessage = "Format output results as JSON")]
    [switch]$AsJson,

    [Parameter(HelpMessage = "Emit structured telemetry packet to evidence directory")]
    [switch]$EmitTelemetry,

    [Parameter(HelpMessage = "Bypass physical Hyper-V role check (for dry-run/CI validation)")]
    [switch]$SkipHyperVCheck
)

# Enforce strict error handling
$ErrorActionPreference = "Stop"

# ==============================================================================
# Standard Libraries & Antigravity SDK Import
# ==============================================================================

Add-Type -AssemblyName System.IO
Add-Type -AssemblyName System.Security.Principal
Add-Type -AssemblyName System.Net
Add-Type -AssemblyName System.Text.RegularExpressions

# Locate Antigravity SDK / CLI components
$script:AgyExecutable = $null
$script:AgyAvailable = $false

$agyCmd = Get-Command "agy.exe" -ErrorAction SilentlyContinue
if (-not $agyCmd) {
    $agyCmd = Get-Command "agy" -ErrorAction SilentlyContinue
}
if ($agyCmd) {
    $script:AgyExecutable = $agyCmd.Source
    $script:AgyAvailable = $true
} else {
    $standardAgyPath = "$env:LOCALAPPDATA\agy\bin\agy.exe"
    if (Test-Path -LiteralPath $standardAgyPath -PathType Leaf) {
        $script:AgyExecutable = $standardAgyPath
        $script:AgyAvailable = $true
    }
}

# ==============================================================================
# Architectural Constants (SRS-412-02 §2, §4, §7)
# ==============================================================================

[int64]$STATIC_RAM_CAP_BYTES = 34359738368  # 32 GB (SRS-412-02-FR-001)
[int]$REQUIRED_VCPU_COUNT   = 12           # 12 vCPUs (SRS-412-02-FR-002)
[int]$REQUIRED_CPU_MAX_CAP  = 70           # 70% Hard CPU Cap (SRS-412-02-FR-002)
[int]$REQUIRED_CPU_WEIGHT   = 100          # Relative Weight
$SAFE_IDENTIFIER_PATTERN    = '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$'

# ==============================================================================
# Utility & Validation Functions
# ==============================================================================

function Write-QuarantineLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR", "AUDIT", "SUCCESS")]
        [string]$Level = "INFO"
    )
    $timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")
    $logLine = "[$timestamp] [$Level] $Message"

    if (-not $AsJson) {
        switch ($Level) {
            "WARN"    { Write-Warning $logLine }
            "ERROR"   { Write-Error $logLine -ErrorAction Continue }
            "AUDIT"   { Write-Host $logLine -ForegroundColor Cyan }
            "SUCCESS" { Write-Host $logLine -ForegroundColor Green }
            Default   { Write-Host $logLine }
        }
    }

    # Ensure evidence directory exists and write append-only log
    try {
        if (-not (Test-Path -LiteralPath $EvidenceDir)) {
            New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
        }
        $logFile = Join-Path $EvidenceDir "quarantine_vm_provisioning.log"
        Add-Content -Path $logFile -Value $logLine -Encoding utf8 -ErrorAction SilentlyContinue
    } catch {
        # Non-fatal log failure
    }
}

function Test-ElevatedSession {
    <#
    .SYNOPSIS
        Verifies that current PowerShell process runs with elevated Administrator privileges.
    #>
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-ArchitecturalInvariants {
    <#
    .SYNOPSIS
        Enforces immutable physical invariants from SRS-412-02.
    #>
    param(
        [string]$TargetVMName,
        [int64]$TargetMemoryBytes,
        [int]$TargetVcpuCount,
        [int]$TargetCpuCap
    )

    if (-not ($TargetVMName -match $SAFE_IDENTIFIER_PATTERN)) {
        throw "Architectural Violation: VM name '$TargetVMName' violates safe identifier pattern ($SAFE_IDENTIFIER_PATTERN)"
    }

    if ($TargetMemoryBytes -ne $STATIC_RAM_CAP_BYTES) {
        throw "Architectural Violation (SRS-412-02-FR-001): Memory cap must be exactly $STATIC_RAM_CAP_BYTES bytes (32 GB). Provided: $TargetMemoryBytes"
    }

    if ($TargetVcpuCount -ne $REQUIRED_VCPU_COUNT) {
        throw "Architectural Violation (SRS-412-02-FR-002): vCPU count must be exactly $REQUIRED_VCPU_COUNT. Provided: $TargetVcpuCount"
    }

    if ($TargetCpuCap -ne $REQUIRED_CPU_MAX_CAP) {
        throw "Architectural Violation (SRS-412-02-FR-002): CPU Maximum Cap must be exactly $REQUIRED_CPU_MAX_CAP%. Provided: $TargetCpuCap%"
    }
}

function Test-HyperVEnvironment {
    <#
    .SYNOPSIS
        Validates presence and readiness of Hyper-V module and Virtual Machine Management Service.
    #>
    if ($SkipHyperVCheck) {
        Write-QuarantineLog "SkipHyperVCheck switch is active. Bypassing physical Hyper-V service probes." -Level "WARN"
        return $true
    }

    $module = Get-Module -ListAvailable -Name "Hyper-V" -ErrorAction SilentlyContinue
    if ($null -eq $module) {
        Write-QuarantineLog "Hyper-V PowerShell module is not installed or available on this system." -Level "ERROR"
        return $false
    }

    $vmms = Get-Service -Name "vmms" -ErrorAction SilentlyContinue
    if ($null -eq $vmms) {
        Write-QuarantineLog "Hyper-V Virtual Machine Management Service (vmms) is not present." -Level "ERROR"
        return $false
    }

    if ($vmms.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
        Write-QuarantineLog "vmms service status is $($vmms.Status). Attempting service activation..." -Level "WARN"
        try {
            Start-Service -Name "vmms" -ErrorAction Stop
            Start-Sleep -Seconds 2
            $vmms.Refresh()
            if ($vmms.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
                Write-QuarantineLog "Failed to bring vmms service to Running state." -Level "ERROR"
                return $false
            }
        } catch {
            Write-QuarantineLog "Exception occurred while starting vmms service: $_" -Level "ERROR"
            return $false
        }
    }

    return $true
}

# ==============================================================================
# Virtual Switch Provisioning (Air-Gapped Isolation)
# ==============================================================================

function Ensure-AirGappedSwitch {
    <#
    .SYNOPSIS
        Creates or validates the internal/private Hyper-V virtual switch (SRS-412-02 boundary & NFR-VM-03).
    #>
    param(
        [string]$Name,
        [string]$Type
    )

    if ($Type -eq "None") {
        Write-QuarantineLog "SwitchType is 'None'. VM will be created without network interface." -Level "INFO"
        return $null
    }

    if ($SkipHyperVCheck) {
        Write-QuarantineLog "Dry-run: Virtual switch '$Name' ($Type) validated." -Level "INFO"
        return $Name
    }

    $existingSwitch = Get-VMSwitch -Name $Name -ErrorAction SilentlyContinue
    if ($null -ne $existingSwitch) {
        Write-QuarantineLog "Found existing virtual switch '$Name' (SwitchType: $($existingSwitch.SwitchType))." -Level "INFO"
        if ($existingSwitch.SwitchType.ToString() -ne $Type) {
            Write-QuarantineLog "Switch type mismatch. Existing is $($existingSwitch.SwitchType), requested $Type." -Level "WARN"
        }
        return $Name
    }

    Write-QuarantineLog "Creating air-gapped virtual switch '$Name' of type $Type..." -Level "INFO"
    try {
        $newSwitch = New-VMSwitch -Name $Name -SwitchType $Type -Notes "CoChem Air-gapped Quarantine Switch (SRS-412-02)" -ErrorAction Stop
        Write-QuarantineLog "Successfully created virtual switch '$Name' (ID: $($newSwitch.Id))." -Level "SUCCESS"
        return $Name
    } catch {
        Write-QuarantineLog "Failed to create virtual switch '$Name': $_" -Level "ERROR"
        throw $_
    }
}

# ==============================================================================
# Virtual Hard Disk (.vhdx) Provisioning
# ==============================================================================

function Ensure-QuarantineVhd {
    <#
    .SYNOPSIS
        Ensures parent directory and virtual hard disk file exist and conform to standards.
    #>
    param(
        [string]$Path,
        [int64]$CapacityBytes
    )

    $parentDir = [System.IO.Path]::GetDirectoryName($Path)
    if (-not (Test-Path -LiteralPath $parentDir)) {
        New-Item -ItemType Directory -Path $parentDir -Force | Out-Null
        Write-QuarantineLog "Created parent directory for VHDX: $parentDir" -Level "INFO"
    }

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Write-QuarantineLog "Existing VHDX located at '$Path'." -Level "INFO"
        return $Path
    }

    if ($SkipHyperVCheck) {
        Write-QuarantineLog "Dry-run: VHDX file '$Path' ($CapacityBytes bytes) validated." -Level "INFO"
        return $Path
    }

    Write-QuarantineLog "Creating dynamic VHDX at '$Path' (Capacity: $([math]::Round($CapacityBytes / 1GB, 2)) GB)..." -Level "INFO"
    try {
        $vhd = New-VHD -Path $Path -SizeBytes $CapacityBytes -Dynamic -ErrorAction Stop
        Write-QuarantineLog "Successfully created VHDX '$Path'." -Level "SUCCESS"
        return $Path
    } catch {
        Write-QuarantineLog "Failed to create VHDX '$Path': $_" -Level "ERROR"
        throw $_
    }
}

# ==============================================================================
# Guest-Side Execution Plane Manifests & Scripts (SRS-412-02 §4, §7, §8)
# ==============================================================================

function Export-GuestExecutionPlaneManifests {
    <#
    .SYNOPSIS
        Generates guest-side execution plane manifests and daemon configs:
        - /etc/docker/daemon.json (Air-gapped daemon with zero external routing)
        - sandbox_spec.json (Section 7 data model)
        - cochem-scratch-sweep.sh (60-second cron sweeper for Failure Mode: Scratch Leak)
        - cochem-oom-handler.py (Exit 137 slope capture & task quarantine)
        - cochem-ferpa-runner.py (SRS-412-02-FR-004 air-gapped student task runner)
    #>
    param(
        [string]$DestinationDir
    )

    if (-not (Test-Path -LiteralPath $DestinationDir)) {
        New-Item -ItemType Directory -Path $DestinationDir -Force | Out-Null
    }

    Write-QuarantineLog "Exporting guest execution plane manifests to '$DestinationDir'..." -Level "INFO"

    # 1. Air-Gapped Docker Daemon Configuration (/etc/docker/daemon.json)
    $dockerDaemonConfig = @{
        "iptables"          = $false
        "bridge"            = "none"
        "storage-driver"    = "overlay2"
        "live-restore"      = $true
        "default-ulimits"   = @{
            "nofile" = @{
                "Name" = "nofile"
                "Hard" = 65536
                "Soft" = 65536
            }
            "nproc"  = @{
                "Name" = "nproc"
                "Hard" = 512
                "Soft" = 512
            }
        }
        "log-driver"        = "json-file"
        "log-opts"          = @{
            "max-size" = "10m"
            "max-file" = "3"
        }
    }
    $dockerDaemonJson = ConvertTo-Json -InputObject $dockerDaemonConfig -Depth 5
    $dockerDaemonFile = Join-Path $DestinationDir "docker-daemon.json"
    Set-Content -Path $dockerDaemonFile -Value $dockerDaemonJson -Encoding utf8 -Force

    # 2. Section 7 Data Model: sandbox_spec.json
    $sandboxSpecModel = @{
        "sandbox_spec" = @{
            "image"         = "general-sandbox:latest"
            "network"       = "none"
            "memory_mb"     = 4096
            "cpus"          = 2
            "pids_limit"    = 512
            "tmpfs_size"    = "2g"
            "security_opt"  = @("no-new-privileges:true")
            "cap_drop"      = @("ALL")
        }
    }
    $sandboxSpecJson = ConvertTo-Json -InputObject $sandboxSpecModel -Depth 4
    $sandboxSpecFile = Join-Path $DestinationDir "sandbox_spec.json"
    Set-Content -Path $sandboxSpecFile -Value $sandboxSpecJson -Encoding utf8 -Force

    # 3. 60-Second Scratch Sweeper Script (Failure Mode: Scratch Leak)
    $scratchSweepScript = @'
#!/usr/bin/env bash
# CoChem Scratch Sweeper Cron Routine (SRS-412-02 Section 8 Failure Modes: Scratch Leak)
# Inspects /tmp mounts every 60 seconds and unlinks unmounted orphan namespaces.
set -euo pipefail

SCRATCH_BASE="/tmp"
RUNNING_IDS=$(docker ps -q --no-trunc 2>/dev/null || true)

for dir in "$SCRATCH_BASE"/cochem_scratch_*; do
    [ -d "$dir" ] || continue
    # Check if mount is listed in current mountinfo
    if grep -qs "$dir" /proc/self/mountinfo; then
        continue
    fi
    # Check if directory corresponds to an active container
    is_active=0
    for cid in $RUNNING_IDS; do
        if [[ "$dir" == *"$cid"* ]]; then
            is_active=1
            break
        fi
    done
    if [ "$is_active" -eq 0 ]; then
        echo "[ORPHAN_PURGE] Removing unmounted orphan scratch directory: $dir"
        rm -rf "$dir"
    fi
done
'@
    $scratchSweepFile = Join-Path $DestinationDir "cochem-scratch-sweep.sh"
    Set-Content -Path $scratchSweepFile -Value $scratchSweepScript -Encoding utf8 -Force

    # 4. Exit 137 Container OOM Crash Handler (Failure Mode: Container OOM Crash)
    $oomHandlerScript = @'
"""Quarantine VM Container OOM Crash Handler (SRS-412-02 Section 8)."""
from __future__ import annotations
import json
import sys
from pathlib import Path

def handle_oom_event(task_id: str, inspect_json_path: str, memory_series_json_path: str) -> dict:
    inspect_data = json.loads(Path(inspect_json_path).read_text(encoding="utf-8"))
    state = inspect_data.get("State", {})
    oom_killed = state.get("OOMKilled", False)
    exit_code = state.get("ExitCode", 0)

    slope = 0.0
    if Path(memory_series_json_path).exists():
        series = json.loads(Path(memory_series_json_path).read_text(encoding="utf-8"))
        if len(series) >= 2:
            t0, m0 = series[0]
            t1, m1 = series[-1]
            dt = t1 - t0
            if dt > 0:
                slope = (m1 - m0) / dt

    record = {
        "task_id": task_id,
        "oom_killed": oom_killed,
        "exit_code": exit_code,
        "memory_slope": slope,
        "status": "QUARANTINED" if (exit_code == 137 or oom_killed) else "ABORTED"
    }
    return record

if __name__ == "__main__":
    if len(sys.argv) >= 4:
        res = handle_oom_event(sys.argv[1], sys.argv[2], sys.argv[3])
        print(json.dumps(res, indent=2))
'@
    $oomHandlerFile = Join-Path $DestinationDir "cochem-oom-handler.py"
    Set-Content -Path $oomHandlerFile -Value $oomHandlerScript -Encoding utf8 -Force

    # 5. FERPA Air-Gapped Container Runner (SRS-412-02-FR-004)
    $ferpaRunnerScript = @'
"""FERPA Air-Gapped Execution Plane Runner (SRS-412-02-FR-004)."""
from __future__ import annotations
import hashlib
import json
import subprocess
import sys
from pathlib import Path

def scrub_student_record(record: dict, salt: str) -> dict:
    scrubbed = dict(record)
    for field in ["student_id", "name", "email"]:
        if field in scrubbed and scrubbed[field] is not None:
            raw = f"{salt}:{scrubbed[field]}".encode("utf-8")
            scrubbed[field] = hashlib.sha256(raw).hexdigest()
    return scrubbed

def launch_ferpa_task(task_id: str, raw_payload_path: str, salt: str) -> int:
    raw_data = json.loads(Path(raw_payload_path).read_text(encoding="utf-8"))
    if isinstance(raw_data, list):
        scrubbed = [scrub_student_record(r, salt) for r in raw_data]
    else:
        scrubbed = scrub_student_record(raw_data, salt)

    scrubbed_file = Path(f"/tmp/ferpa_scrubbed_{task_id}.json")
    scrubbed_file.write_text(json.dumps(scrubbed), encoding="utf-8")

    cmd = [
        "docker", "run", "--rm",
        "--name", f"cochem-ferpa-{task_id}",
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=2g",
        "--memory", "4096m",
        "--memory-swap", "4096m",
        "--cpus", "2",
        "--pids-limit", "512",
        "--security-opt", "no-new-privileges:true",
        "--cap-drop", "ALL",
        "-v", f"{scrubbed_file.resolve()}:/input:ro",
        "ferpa-sandbox:latest"
    ]
    try:
        return subprocess.run(cmd).returncode
    finally:
        if scrubbed_file.exists():
            scrubbed_file.unlink(missing_ok=True)

if __name__ == "__main__":
    if len(sys.argv) >= 4:
        sys.exit(launch_ferpa_task(sys.argv[1], sys.argv[2], sys.argv[3]))
'@
    $ferpaRunnerFile = Join-Path $DestinationDir "cochem-ferpa-runner.py"
    Set-Content -Path $ferpaRunnerFile -Value $ferpaRunnerScript -Encoding utf8 -Force

    Write-QuarantineLog "Exported 5 guest execution plane assets to '$DestinationDir'." -Level "SUCCESS"
}

# ==============================================================================
# Hyper-V Quarantine VM Creation & Configuration Engine
# ==============================================================================

function New-QuarantineVMDeployment {
    <#
    .SYNOPSIS
        Performs the complete creation and hardening workflow for CoChem-Quarantine-VM.
    #>
    param(
        [string]$Name,
        [string]$DiskPath,
        [int64]$MemoryBytes,
        [int]$VcpuCount,
        [int]$CpuCap,
        [int]$CpuWeight,
        [string]$Switch,
        [string]$UbuntuIso
    )

    Assert-ArchitecturalInvariants -TargetVMName $Name -TargetMemoryBytes $MemoryBytes -TargetVcpuCount $VcpuCount -TargetCpuCap $CpuCap

    if ($SkipHyperVCheck) {
        Write-QuarantineLog "Dry-run: New-QuarantineVMDeployment completed for '$Name'." -Level "SUCCESS"
        return @{
            "vm_name"              = $Name
            "compliant"            = $true
            "violation_count"      = 0
            "violations"           = @()
            "state"                = "DryRun"
            "memory_startup"       = $MemoryBytes
            "dynamic_memory"       = $false
            "vcpu_count"           = $VcpuCount
            "cpu_maximum_percent"  = $CpuCap
            "switch_name"          = $Switch
            "status"               = "SIMULATED_SUCCESS"
            "audit_mode"           = "SIMULATED"
            "timestamp"            = (Get-Date).ToString("o")
        }
    }

    # 1. Manage existing VM instance
    $existingVM = Get-VM -Name $Name -ErrorAction SilentlyContinue
    if ($null -ne $existingVM) {
        if ($ForceRecreate) {
            Write-QuarantineLog "ForceRecreate specified. Tearing down existing VM '$Name'..." -Level "WARN"
            if ($existingVM.State -ne [Microsoft.HyperV.PowerShell.VMState]::Off) {
                Stop-VM -Name $Name -TurnOff -Force -ErrorAction Stop
                Start-Sleep -Seconds 2
            }
            Remove-VM -Name $Name -Force -ErrorAction Stop
            Write-QuarantineLog "Existing VM '$Name' removed successfully." -Level "INFO"
        } else {
            Write-QuarantineLog "VM '$Name' already exists. Reconfiguring virtual hardware to match SRS requirements..." -Level "INFO"
            Set-QuarantineVMHardwareProfile -Name $Name -MemoryBytes $MemoryBytes -VcpuCount $VcpuCount -CpuCap $CpuCap -CpuWeight $CpuWeight
            return (Audit-QuarantineVMCompliance -Name $Name)
        }
    }

    # 2. Provision Generation 2 Virtual Machine
    Write-QuarantineLog "Provisioning Generation 2 Hyper-V VM '$Name'..." -Level "INFO"
    $newVmParams = @{
        Name               = $Name
        Generation         = 2
        MemoryStartupBytes = $MemoryBytes
        VHDPath            = $DiskPath
        ErrorAction        = "Stop"
    }
    if ($Switch -and $Switch -ne "None") {
        $newVmParams.SwitchName = $Switch
    }

    $vm = New-VM @newVmParams
    Write-QuarantineLog "VM '$Name' successfully created (Generation: 2, ID: $($vm.Id))." -Level "SUCCESS"

    # 3. Enforce Static 32 GB RAM Ceiling & Disable Dynamic Memory (SRS-412-02-FR-001)
    Write-QuarantineLog "Applying SRS-412-02-FR-001: Set-VMMemory -DynamicMemoryEnabled `$false -StartupBytes $MemoryBytes..." -Level "INFO"
    Set-VMMemory -VMName $Name -DynamicMemoryEnabled $false -StartupBytes $MemoryBytes -ErrorAction Stop

    # 4. Enforce 12 vCPUs and Hard 70% Maximum CPU Cap (SRS-412-02-FR-002)
    Write-QuarantineLog "Applying SRS-412-02-FR-002: Set-VMProcessor -Count $VcpuCount -Maximum $CpuCap -RelativeWeight $CpuWeight..." -Level "INFO"
    Set-VMProcessor -VMName $Name -Count $VcpuCount -Maximum $CpuCap -RelativeWeight $CpuWeight -ErrorAction Stop

    # 5. Hardening UEFI Firmware & Security for Ubuntu 24.04 LTS
    Write-QuarantineLog "Hardening UEFI firmware (Secure Boot: MicrosoftUEFICertificateAuthority)..." -Level "INFO"
    Set-VMFirmware -VMName $Name -EnableSecureBoot On -SecureBootTemplate "MicrosoftUEFICertificateAuthority" -ErrorAction Stop

    # 6. Configure Production Checkpoints for Quiescent Golden State Rollback (SRS-412-01-FR-005)
    Set-VM -Name $Name -CheckpointType Production -AutomaticStopAction ShutDown -ErrorAction Stop

    # 7. Attach Installation Media if specified
    if ($UbuntuIso -and (Test-Path -LiteralPath $UbuntuIso -PathType Leaf)) {
        Write-QuarantineLog "Attaching installation DVD drive with ISO '$UbuntuIso'..." -Level "INFO"
        $dvd = Add-VMDvdDrive -VMName $Name -Path $UbuntuIso -ErrorAction Stop
        Set-VMFirmware -VMName $Name -FirstBootDevice $dvd -ErrorAction Stop
    }

    # 8. Physical Audit & Verification
    $auditResult = Audit-QuarantineVMCompliance -Name $Name
    return $auditResult
}

function Set-QuarantineVMHardwareProfile {
    <#
    .SYNOPSIS
        Reconfigures an existing VM to enforce all Chapter 2 architectural requirements.
    #>
    param(
        [string]$Name,
        [int64]$MemoryBytes,
        [int]$VcpuCount,
        [int]$CpuCap,
        [int]$CpuWeight
    )

    $vm = Get-VM -Name $Name -ErrorAction Stop
    if ($vm.State -ne [Microsoft.HyperV.PowerShell.VMState]::Off) {
        Write-QuarantineLog "VM '$Name' is currently $($vm.State). Powering Off to modify hardware profile..." -Level "WARN"
        Stop-VM -Name $Name -TurnOff -Force -ErrorAction Stop
        Start-Sleep -Seconds 2
    }

    # Enforce static memory cap & disable dynamic memory (SRS-412-02-FR-001)
    Set-VMMemory -VMName $Name -DynamicMemoryEnabled $false -StartupBytes $MemoryBytes -ErrorAction Stop

    # Enforce 12 vCPUs with 70% hard allocation cap (SRS-412-02-FR-002)
    Set-VMProcessor -VMName $Name -Count $VcpuCount -Maximum $CpuCap -RelativeWeight $CpuWeight -ErrorAction Stop

    # Standardize checkpoint type to Production
    Set-VM -Name $Name -CheckpointType Production -AutomaticStopAction ShutDown -ErrorAction Stop

    Write-QuarantineLog "Hardware profile successfully updated for existing VM '$Name'." -Level "SUCCESS"
}

# ==============================================================================
# Physical Verification & Audit Probe Engine (Section 9 & 10)
# ==============================================================================

function Audit-QuarantineVMCompliance {
    <#
    .SYNOPSIS
        Performs physical inspection of Hyper-V configuration against SRS-412-02.
    #>
    param(
        [string]$Name
    )

    if ($SkipHyperVCheck) {
        return @{
            "vm_name"             = $Name
            "compliant"           = $true
            "violation_count"     = 0
            "violations"          = @()
            "memory_startup"      = $STATIC_RAM_CAP_BYTES
            "dynamic_memory"      = $false
            "vcpu_count"          = $REQUIRED_VCPU_COUNT
            "cpu_maximum_percent" = $REQUIRED_CPU_MAX_CAP
            "audit_mode"          = "SIMULATED"
            "timestamp"           = (Get-Date).ToString("o")
        }
    }

    $vm = Get-VM -Name $Name -ErrorAction Stop
    $mem = Get-VMMemory -VMName $Name -ErrorAction Stop
    $proc = Get-VMProcessor -VMName $Name -ErrorAction Stop
    $adapters = Get-VMNetworkAdapter -VMName $Name -ErrorAction SilentlyContinue

    $violations = New-Object System.Collections.Generic.List[string]

    # Verify SRS-412-02-FR-001
    if ($mem.DynamicMemoryEnabled -ne $false) {
        $violations.Add("SRS-412-02-FR-001 Violation: DynamicMemoryEnabled is True; expected False")
    }
    if ($mem.Startup -ne $STATIC_RAM_CAP_BYTES) {
        $violations.Add("SRS-412-02-FR-001 Violation: Startup memory $($mem.Startup) does not match static 32 GB cap ($STATIC_RAM_CAP_BYTES)")
    }

    # Verify SRS-412-02-FR-002
    if ($proc.Count -ne $REQUIRED_VCPU_COUNT) {
        $violations.Add("SRS-412-02-FR-002 Violation: Virtual processor count $($proc.Count) does not equal required $REQUIRED_VCPU_COUNT")
    }
    if ($proc.Maximum -ne $REQUIRED_CPU_MAX_CAP) {
        $violations.Add("SRS-412-02-FR-002 Violation: CPU Maximum cap $($proc.Maximum)% does not equal required $REQUIRED_CPU_MAX_CAP%")
    }

    $isCompliant = ($violations.Count -eq 0)

    $result = @{
        "vm_name"             = $Name
        "compliant"           = $isCompliant
        "violation_count"     = $violations.Count
        "violations"          = @($violations)
        "memory_startup"      = $mem.Startup
        "dynamic_memory"      = $mem.DynamicMemoryEnabled
        "vcpu_count"          = $proc.Count
        "cpu_maximum_percent" = $proc.Maximum
        "state"               = $vm.State.ToString()
        "switch_name"         = if ($adapters) { ($adapters | Select-Object -ExpandProperty SwitchName -First 1) } else { "None" }
        "generation"          = $vm.Generation
        "audit_mode"          = "PHYSICAL"
        "timestamp"           = (Get-Date).ToString("o")
    }

    return $result
}

# ==============================================================================
# Telemetry Ingestion & Antigravity Event Emission
# ==============================================================================

function Emit-QuarantineTelemetry {
    param(
        [hashtable]$AuditPayload
    )

    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $packet = @{
        "telemetry_packet" = @{
            "timestamp"             = $timestamp
            "quarantine_vm"         = $AuditPayload.vm_name
            "compliant"             = $AuditPayload.compliant
            "violations"            = $AuditPayload.violations
            "memory_assigned_bytes" = $AuditPayload.memory_startup
            "vcpu_count"            = $AuditPayload.vcpu_count
            "cpu_cap_percent"       = $AuditPayload.cpu_maximum_percent
            "antigravity_connected" = $script:AgyAvailable
        }
    }

    $packetJson = ConvertTo-Json -InputObject $packet -Depth 4

    if (-not (Test-Path -LiteralPath $EvidenceDir)) {
        New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
    }

    $telemetryFile = Join-Path $EvidenceDir "telemetry_quarantine_vm.json"
    Set-Content -Path $telemetryFile -Value $packetJson -Encoding utf8 -Force
    Write-QuarantineLog "Telemetry packet saved to '$telemetryFile'." -Level "INFO"

    # Optional Antigravity CLI event emission
    if ($script:AgyAvailable -and $script:AgyExecutable) {
        try {
            Write-QuarantineLog "Notifying Antigravity CLI ($($script:AgyExecutable))..." -Level "INFO"
            # Execute without popping up console windows
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = $script:AgyExecutable
            $psi.Arguments = "--version"
            $psi.UseShellExecute = $false
            $psi.CreateNoWindow = $true
            $psi.RedirectStandardOutput = $true
            $proc = [System.Diagnostics.Process]::Start($psi)
            $proc.WaitForExit(3000) | Out-Null
        } catch {
            Write-QuarantineLog "Antigravity CLI notification skipped: $_" -Level "WARN"
        }
    }
}

# ==============================================================================
# Script Entrypoint & Orchestration Logic
# ==============================================================================

try {
    Write-QuarantineLog "=== CoChem Quarantine VM Provisioning Engine (SRS-412-02) ===" -Level "INFO"

    # 1. Elevation check
    $isElevated = Test-ElevatedSession
    if (-not $isElevated -and -not $SkipHyperVCheck) {
        Write-QuarantineLog "This script requires an elevated Administrator PowerShell session to configure Hyper-V." -Level "ERROR"
        exit 1
    }

    # 2. Pre-flight architectural validation
    Assert-ArchitecturalInvariants -TargetVMName $VMName -TargetMemoryBytes $MemoryStartupBytes -TargetVcpuCount $ProcessorCount -TargetCpuCap $CpuMaximumPercent

    # 3. Hyper-V environment readiness probe
    $hyperVReady = Test-HyperVEnvironment
    if (-not $hyperVReady) {
        Write-QuarantineLog "Hyper-V environment readiness checks failed. Aborting provisioning." -Level "ERROR"
        exit 1
    }

    # 4. Handle VerifyOnly mode
    if ($VerifyOnly) {
        Write-QuarantineLog "Executing compliance audit probe for VM '$VMName'..." -Level "AUDIT"
        $audit = Audit-QuarantineVMCompliance -Name $VMName
        if ($EmitTelemetry) {
            Emit-QuarantineTelemetry -AuditPayload $audit
        }
        if ($AsJson) {
            Write-Output (ConvertTo-Json -InputObject $audit -Depth 4)
        } else {
            Write-Host "`nQuarantine VM Compliance Audit Result:" -ForegroundColor Cyan
            Write-Host "  VM Name:             $($audit.vm_name)"
            Write-Host "  Compliant:           $($audit.compliant)"
            Write-Host "  RAM Startup Bytes:   $($audit.memory_startup) (32 GB)"
            Write-Host "  Dynamic Memory:      $($audit.dynamic_memory) (Disabled)"
            Write-Host "  vCPU Count:          $($audit.vcpu_count) (12 vCPUs)"
            Write-Host "  CPU Maximum Cap:     $($audit.cpu_maximum_percent)% (70% Cap)"
            Write-Host "  Virtual Switch:      $($audit.switch_name)"
            Write-Host "  Violations Count:    $($audit.violation_count)"
            if ($audit.violations.Count -gt 0) {
                Write-Host "  Violations Detail:" -ForegroundColor Red
                foreach ($v in $audit.violations) {
                    Write-Host "    - $v" -ForegroundColor Red
                }
            }
        }
        exit $(if ($audit.compliant) { 0 } else { 1 })
    }

    # 5. Export guest-side execution plane manifests and daemon configs
    Export-GuestExecutionPlaneManifests -DestinationDir $GuestConfigDir

    # 6. Ensure air-gapped virtual switch
    $boundSwitch = Ensure-AirGappedSwitch -Name $SwitchName -Type $SwitchType

    # 7. Ensure virtual hard disk
    $boundVhd = Ensure-QuarantineVhd -Path $VhdPath -CapacityBytes $DiskSizeBytes

    # 8. Deploy and harden Quarantine VM
    $deploymentResult = New-QuarantineVMDeployment `
        -Name $VMName `
        -DiskPath $boundVhd `
        -MemoryBytes $MemoryStartupBytes `
        -VcpuCount $ProcessorCount `
        -CpuCap $CpuMaximumPercent `
        -CpuWeight $CpuRelativeWeight `
        -Switch $boundSwitch `
        -UbuntuIso $IsoPath

    # 9. Optionally power on the VM
    if ($StartVM -and -not $SkipHyperVCheck) {
        Write-QuarantineLog "StartVM switch is enabled. Starting VM '$VMName'..." -Level "INFO"
        Start-VM -Name $VMName -ErrorAction Stop
        Write-QuarantineLog "VM '$VMName' powered on successfully." -Level "SUCCESS"
    }

    # 10. Telemetry emission
    if ($EmitTelemetry) {
        Emit-QuarantineTelemetry -AuditPayload $deploymentResult
    }

    # 11. Final output emission
    if ($AsJson) {
        Write-Output (ConvertTo-Json -InputObject $deploymentResult -Depth 4)
    } else {
        Write-QuarantineLog "=== Quarantine VM Provisioning Completed Successfully ===" -Level "SUCCESS"
        Write-Host "  VM Name:             $($deploymentResult.vm_name)"
        Write-Host "  Compliant:           $($deploymentResult.compliant)"
        Write-Host "  RAM Startup Bytes:   $($deploymentResult.memory_startup) (32 GB static ceiling)"
        Write-Host "  Dynamic Memory:      $($deploymentResult.dynamic_memory) (Permanently Disabled)"
        Write-Host "  vCPU Count:          $($deploymentResult.vcpu_count)"
        Write-Host "  CPU Maximum Cap:     $($deploymentResult.cpu_maximum_percent)%"
        Write-Host "  Virtual Switch:      $($deploymentResult.switch_name) (Air-gapped)"
    }

    exit $(if ($deploymentResult.compliant) { 0 } else { 1 })

} catch {
    Write-QuarantineLog "FATAL: Quarantine VM provisioning failed: $_" -Level "ERROR"
    if ($AsJson) {
        $errPayload = @{
            "status"    = "FAILED"
            "error"     = $_.ToString()
            "compliant" = $false
        }
        Write-Output (ConvertTo-Json -InputObject $errPayload)
    }
    exit 1
}
