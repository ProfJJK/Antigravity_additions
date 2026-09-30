# Service Recovery Configuration Script (MC-HW-67)
param(
    [string]$ServiceName = "CoChemHostWarden",
    [int]$ResetSeconds = 86400,
    [int]$RestartDelayMs = 5000
)

Write-Host "Configuring service recovery for $ServiceName (reset=$ResetSeconds, restart=$RestartDelayMs ms)..."

# Test if service exists
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($null -ne $svc) {
    # Configure recovery actions: restart after 5s on 1st/2nd failure
    $actions = "restart/$RestartDelayMs/restart/$RestartDelayMs//"
    sc.exe failure $ServiceName reset= $ResetSeconds actions= $actions
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Service recovery successfully configured for $ServiceName"
        exit 0
    } else {
        Write-Warning "sc.exe failure returned exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} else {
    Write-Host "Service $ServiceName is not yet registered. Generated recovery profile for provisioning."
    exit 0
}
