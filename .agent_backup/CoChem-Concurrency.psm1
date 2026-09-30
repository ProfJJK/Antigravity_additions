function Acquire-CoChemFileLock {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [string]$Path,
        
        [Parameter(Mandatory=$false)]
        [int]$TimeoutSeconds = 60
    )

    $start = [DateTime]::UtcNow
    $jitter = New-Object Random

    while ($true) {
        try {
            $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
            return $stream
        } catch [System.Management.Automation.MethodInvocationException], [System.IO.IOException] {
            if (([DateTime]::UtcNow - $start).TotalSeconds -gt $TimeoutSeconds) {
                throw "Failed to acquire lock on '$Path' within $TimeoutSeconds seconds."
            }
            $sleepMs = $jitter.Next(50, 250)
            Start-Sleep -Milliseconds $sleepMs
        }
    }
}

function Release-CoChemFileLock {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [System.IO.FileStream]$Stream
    )

    if ($null -ne $Stream) {
        $Stream.Flush()
        $Stream.Close()
        $Stream.Dispose()
    }
}

function Invoke-CoChemSemaphoreCheck {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$false)]
        [int]$MaxAgents = 10,
        
        [Parameter(Mandatory=$false)]
        [string]$SemaphoreFile = "D:\__CoChem\semaphore.json"
    )

    $jitter = New-Object Random
    $loopStart = [DateTime]::UtcNow

    while ($true) {
        if (([DateTime]::UtcNow - $loopStart).TotalSeconds -gt 3600) {
            throw "Failed to evaluate limits and register in semaphore after 3600 seconds. Halting to prevent infinite loop."
        }
        # Resource Check (Outside Lock)
        $cpuCounter = Get-Counter '\Processor(_Total)\% Processor Time' -ErrorAction SilentlyContinue
        $memCounter = Get-Counter '\Memory\% Committed Bytes In Use' -ErrorAction SilentlyContinue
        
        $cpu = $null
        $mem = $null
        
        if ($null -ne $cpuCounter -and $null -ne $cpuCounter.CounterSamples) {
            $cpu = $cpuCounter.CounterSamples[0].CookedValue
        }
        if ($null -ne $memCounter -and $null -ne $memCounter.CounterSamples) {
            $mem = $memCounter.CounterSamples[0].CookedValue
        }

        if (($null -ne $cpu -and $cpu -gt 50) -or ($null -ne $mem -and $mem -gt 50)) {
            Start-Sleep -Milliseconds ($jitter.Next(2000, 5000))
            continue
        }

        # Lock Acquisition
        $stream = Acquire-CoChemFileLock -Path $SemaphoreFile -TimeoutSeconds 60
        
        try {
            # Stream I/O
            $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8, $true, 1024, $true)
            $content = $reader.ReadToEnd()
            $reader.Dispose()
            
            $agents = @()
            if (![string]::IsNullOrWhiteSpace($content)) {
                try {
                    $agents = $content | ConvertFrom-Json
                    if ($null -eq $agents) { $agents = @() }
                    if ($agents -isnot [array]) {
                        $agents = @($agents)
                    }
                } catch {
                    $agents = @()
                }
            }

            # PID + StartTime Validation
            $validAgents = @()
            foreach ($agent in $agents) {
                try {
                    $proc = Get-Process -Id $agent.PID -ErrorAction SilentlyContinue
                    if ($null -ne $proc) {
                        $procStartTime = $proc.StartTime.ToString("o")
                        if ($procStartTime -eq $agent.StartTime) {
                            $validAgents += $agent
                        }
                    }
                } catch {
                    # Process access denied or other errors, ignore
                }
            }

            # Dynamic Limit Evaluation
            if ($validAgents.Count -ge $MaxAgents) {
                Release-CoChemFileLock -Stream $stream
                $stream = $null
                Start-Sleep -Milliseconds ($jitter.Next(3000, 8000))
                continue
            }

            # Space is available, add current process
            $currentProcess = Get-Process -Id $PID
            $newAgent = [PSCustomObject]@{
                PID = $PID
                StartTime = $currentProcess.StartTime.ToString("o")
            }
            $validAgents += $newAgent

            # Atomic Stream Overwrite
            $stream.Position = 0
            $stream.SetLength(0)
            
            $writer = New-Object System.IO.StreamWriter($stream, [System.Text.Encoding]::UTF8, 1024, $true)
            $jsonOutput = $validAgents | ConvertTo-Json -Depth 5 -Compress
            $writer.Write($jsonOutput)
            $writer.Flush()
            $writer.Dispose()
            
            # Successfully registered, break the loop
            break
            
        } finally {
            if ($null -ne $stream) {
                Release-CoChemFileLock -Stream $stream
            }
        }
    }
}

Export-ModuleMember -Function Acquire-CoChemFileLock, Release-CoChemFileLock, Invoke-CoChemSemaphoreCheck
