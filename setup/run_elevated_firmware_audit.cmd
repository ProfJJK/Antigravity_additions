@echo off
rem ===============================================================================
rem CoChem Elevated Host Bridge (EHB): NVMe Storage Firmware & ASPM Audit Launcher
rem Task Identifier: Task 1.03 (Work Package 1.0 - Hardware & Storage Hardening)
rem Target: Query IOCTL Storage Firmware Slots & Emit nvme_firmware_audit.json
rem Reference: SRS-CHUNK-019-SYSTEM-STABILITY-BSOD-0x50-V4.5-20260913
rem Compliance: Shell Unification (Task 2.01 / pwsh.exe), PCA-96 (Elevated Host Bridge)
rem ===============================================================================
echo Requesting Windows Administrator Elevation (UAC) via pwsh.exe...
pwsh.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process pwsh.exe -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"C:\Users\ansac\.gemini\antigravity-cli\scratch\setup\audit_nvme_firmware.ps1\"' -Verb RunAs -Wait"
echo.
echo Elevation run complete. Inspecting emitted audit artifact...
type "C:\Users\ansac\.gemini\antigravity-cli\scratch\nvme_firmware_audit.json"
pause
