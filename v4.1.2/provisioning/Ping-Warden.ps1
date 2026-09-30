# Host Warden Health Probe (MC-HW-68)
# Compliant with SRS-412-01-FR-003 and Host Warden SRE architecture.
param(
    [string]$PipePath = "\\.\pipe\cochem_warden_vm",
    [int]$TimeoutMs = 2000
)

Write-Host "Probing Host Warden named pipe: $PipePath (timeout: ${TimeoutMs}ms)..."

$client = $null
try {
    # Extract pipe name from local pipe path prefix if present
    $pipeName = $PipePath -replace '^\\\\\.\\pipe\\', ''

    # Connect physical .NET Named Pipe client stream
    $client = New-Object System.IO.Pipes.NamedPipeClientStream(".", $pipeName, [System.IO.Pipes.PipeDirection]::InOut)
    $client.Connect($TimeoutMs)

    if ($client.IsConnected) {
        Write-Host "SUCCESS: Host Warden pipe is online and responding."
        $client.Close()
        exit 0
    } else {
        Write-Warning "FAIL: Host Warden named pipe client reported disconnected state."
        exit 1
    }
} catch [System.TimeoutException] {
    Write-Warning "TIMED OUT: Named pipe $PipePath did not respond within $TimeoutMs ms."
    exit 2
} catch {
    Write-Host "Host Warden named pipe $PipePath is currently offline (Endpoint inactive)."
    exit 1
} finally {
    if ($null -ne $client) {
        $client.Dispose()
    }
}
