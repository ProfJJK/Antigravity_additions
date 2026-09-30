@echo off
rem ===============================================================================
rem CoChem Elevated Host Bridge (EHB): NVMe HMB Stabilization Launcher
rem Task Identifier: Task 191.01 / WP 1.0 (Task 1.01 / Task 1.02)
rem Target: HKLM:\SYSTEM\CurrentControlSet\Control\StorPort\HmbAllocationPolicy = 0
rem Reference: SRS-CHUNK-019-SYSTEM-STABILITY-BSOD-0x50-V4.5-20260913
rem Compliance: Shell Unification (Task 2.01 / pwsh.exe), PCA-96 (Elevated Host Bridge)
rem ===============================================================================
echo Requesting Windows Administrator Elevation (UAC) via pwsh.exe...
pwsh.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process pwsh.exe -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"D:\__CoChem\__agentic\setup\setup_nvme_hmb_guard.ps1\"' -Verb RunAs -Wait"
echo.
echo Elevation run complete. Verifying physical registry state...
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "D:\__CoChem\__agentic\setup\setup_nvme_hmb_guard.ps1" -VerifyOnly
pause
