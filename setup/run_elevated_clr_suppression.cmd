@echo off
rem ===============================================================================
rem CoChem Elevated Host Bridge (EHB): CLR Profiler Suppression Launcher
rem Task Identifier: Task 2.03 / WP 2.0 (Deliverable: dotnet_profiler_suppress)
rem Target: COMPlus_ProfAPI_DefaultAttachEnabled=0 and COMPlus_AttachProfiler=0
rem Reference: SRS-CHUNK-019-SYSTEM-STABILITY-BSOD-0x50-V4.5-20260913
rem Compliance: Shell Unification (Task 2.01 / pwsh.exe), PCA-96 (Elevated Host Bridge)
rem ===============================================================================
echo Requesting Windows Administrator Elevation (UAC) via pwsh.exe...
pwsh.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process pwsh.exe -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"D:\__CoChem\__agentic\setup\setup_clr_profiler_suppression.ps1\"' -Verb RunAs -Wait"
echo.
echo Elevation run complete. Verifying physical registry state...
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "D:\__CoChem\__agentic\setup\setup_clr_profiler_suppression.ps1" -VerifyOnly
pause
