<#
.SYNOPSIS
CoChem Concurrency and Locking Module
Provides OS-level atomic file streams to prevent file-write race conditions and 
dynamically polls system resources to manage a global Agent Semaphore without deadlocks.
#>

function Acquire-CoChemFileLock {
    [CmdletBinding()]
    param (
        [Parameter(Mandatory=$true)]
        [string]$TargetFile
    )

    $fileName = [System.IO.Path]::GetFileName($TargetFile)
    $lockDir = "D:\__CoChem\.agent_artifacts\locks"
    $lockPath = Join-Path -Path $lockDir -ChildPath "$fileName.lock"

    if (-not (Test-Path $lockDir)) {
        New-Item -ItemType Directory -Force -Path $lockDir | Out-Null
    }

    $stream = $null
    $acquired = $false
    while (-not $acquired) {
        try {
            $stream = [System.IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
            $acquired = $true
        } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
            $jitter = Get-Random -Minimum 100 -Maximum 1000
            Start-Sleep -Milliseconds $jitter
        }
    }
    
    return $stream
}

function Release-CoChemFileLock {
    [CmdletBinding()]
    param (
        [Parameter(Mandatory=$true)]
        [System.IO.FileStream]$Stream
    )

    $lockPath = $Stream.Name
    $Stream.Close()
    $Stream.Dispose()
    
    try {
        Remove-Item -Path $lockPath -Force -ErrorAction SilentlyContinue
    } catch {}
}

function Invoke-CoChemSemaphoreCheck {
    [CmdletBinding()]
    param (
        [Parameter(Mandatory=$false)]
        [int]$RamReservePerAgentMB = 500
    )

    $registryPath = "D:\__CoChem\.agent_artifacts\semaphore.json"
    
    while ($true) {
        # Polling OS metrics
        $os = Get-CimInstance Win32_OperatingSystem
        $cpu = Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average | Select-Object -ExpandProperty Average
        
        $freeRamMB = [math]::Round($os.FreePhysicalMemory / 1024, 0)
        $totalRamMB = [math]::Round($os.TotalVisibleMemorySize / 1024, 0)
        
        # Max limits: Agents can use up to 50% of the total system capacity
        $maxAllowedRamMB = [math]::Round($totalRamMB * 0.5, 0)
        $usedByAgentsRamMB = $totalRamMB - $freeRamMB
        
        # If CPU > 50%, or we're eating into the reserved 50% RAM, we wait
        if ($cpu -ge 50 -or $freeRamMB -le $maxAllowedRamMB) {
            Start-Sleep -Seconds (Get-Random -Minimum 1 -Maximum 5)
            continue
        }

        # We have resources. Acquire the master registry lock to update.
        $registryLock = Acquire-CoChemFileLock -TargetFile "master_semaphore"

        try {
            $registry = @()
            if (Test-Path $registryPath) {
                $content = Get-Content $registryPath -Raw -ErrorAction SilentlyContinue
                if (-not [string]::IsNullOrWhiteSpace($content)) {
                    $registry = $content | ConvertFrom-Json -AsHashtable
                }
            }

            # Prune dead agents using robust PID + StartTime
            $activeAgents = @()
            foreach ($agent in $registry) {
                $process = Get-Process -Id $agent.pid -ErrorAction SilentlyContinue
                if ($process -and $process.StartTime.ToString("o") -eq $agent.startTime) {
                    $activeAgents += $agent
                }
            }
            
            # Check if this specific PID is already registered
            $myProcess = Get-Process -Id $PID
            $myStartTime = $myProcess.StartTime.ToString("o")
            $alreadyRegistered = $activeAgents | Where-Object { $_.pid -eq $PID }

            if (-not $alreadyRegistered) {
                # Calculate if adding one more agent will exceed limits
                $estimatedFutureRamMB = $freeRamMB - $RamReservePerAgentMB
                if ($estimatedFutureRamMB -le $maxAllowedRamMB) {
                    # Not enough room for another agent right now
                    Release-CoChemFileLock -Stream $registryLock
                    Start-Sleep -Seconds (Get-Random -Minimum 1 -Maximum 5)
                    continue
                }

                # Register ourselves
                $activeAgents += @{
                    pid = $PID
                    startTime = $myStartTime
                }
                
                # Atomic Write
                $tmpPath = "$registryPath.tmp"
                $activeAgents | ConvertTo-Json -Compress | Set-Content -Path $tmpPath -Force
                [System.IO.File]::Replace($tmpPath, $registryPath, $null)
            }
            
            # Registration complete, release lock and proceed
            Release-CoChemFileLock -Stream $registryLock
            break
            
        } catch {
            Release-CoChemFileLock -Stream $registryLock
            Start-Sleep -Seconds 1
        }
    }
}
