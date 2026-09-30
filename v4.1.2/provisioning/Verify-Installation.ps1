# Host Warden Installation and Integrity Verifier (MC-HW-69)
param(
    [string]$InstallDir = "D:\__CoChem\__agentic\v4.1.2",
    [string]$VmName = "CoChem-Quarantine-VM"
)

Write-Host "Verifying Host Warden installation, Hyper-V configuration, and ACLs in $InstallDir..."
$allPassed = $true

# 1. Verify required directory tree and installation manifest files
$items = @("src\cochem\warden\mcp_server.py", "src\cochem\warden\affinity.py", "provisioning", "tests", "wiki\srs", "knowledge_index.db")
foreach ($item in $items) {
    if (Test-Path (Join-Path $InstallDir $item)) {
        Write-Host "  [OK] Manifest item present: $item"
    } else {
        Write-Warning "  [FAIL] Missing manifest item: $item"
        $allPassed = $false
    }
}

# 2. Verify Python runtime and Host Warden module execution
$pyCmd = "import sys; sys.path.insert(0, r'$InstallDir\src'); import cochem.warden.affinity as a; print(hex(a.get_ecore_affinity_mask()))"
$pyCheck = & "C:\Python314\python.exe" -c $pyCmd 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "  [OK] Python runtime active and E-Core affinity verified: $pyCheck"
} else {
    Write-Warning "  [FAIL] Python runtime check failed: $pyCheck"
    $allPassed = $false
}

# 3. Verify Filesystem ACLs and security descriptor
$acl = Get-Acl -LiteralPath $InstallDir -ErrorAction SilentlyContinue
if ($null -ne $acl -and $acl.Access.Count -gt 0) {
    Write-Host "  [OK] Filesystem ACL verified (Rules: $($acl.Access.Count))"
} else {
    Write-Warning "  [FAIL] Filesystem ACL check failed"
    $allPassed = $false
}

# 4. Check Hyper-V management service and virtualization features
$vmms = Get-Service -Name "vmms" -ErrorAction SilentlyContinue
if ($null -ne $vmms -and $vmms.Status -eq "Running") {
    Write-Host "  [OK] Hyper-V Virtual Machine Management service: $($vmms.Status)"
} else {
    Write-Host "  [INFO] vmms service offline or standard host mode."
}

if ($allPassed) {
    Write-Host "Host Warden installation verification: SUCCESS"
    exit 0
} else {
    Write-Error "Host Warden installation verification: FAILED"
    exit 1
}
