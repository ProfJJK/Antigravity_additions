@echo off
setlocal
REM ==========================================================================
REM Pipeline_3_0_0_Startup.bat - headless, windowless launcher for the
REM CoChem Pipeline 3.0.0 daemons (task_work_loop, HA watchdog, idle sidecar).
REM Non-interactive: no prompts, no pauses, no credentials.
REM ==========================================================================

REM -- Headless environment (set before any daemon launch) --------------------
REM These tell CLIs that honour them (Go CLIs such as agy) to skip colour,
REM TTY/console and clipboard code paths.
set CI=1
set NO_COLOR=1
set TERM=dumb
set NONINTERACTIVE=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8
set GOLANG_HEADLESS=1

REM -- Paths ------------------------------------------------------------------
set PIPELINE_ROOT=%~dp0
set AGENTIC_SCRIPTS=D:\__agentic\scripts
set LOG_DIR=%~dp0logs

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM -- Pre-flight checks ------------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (echo [ERROR] interpreter not found on PATH & exit /b 1)

if not exist "%PIPELINE_ROOT%.scripts\task_work_loop.py" (echo [ERROR] missing task_work_loop.py & exit /b 1)
if not exist "%AGENTIC_SCRIPTS%\cochem_ha_daemon_watchdog.py" (echo [ERROR] missing cochem_ha_daemon_watchdog.py & exit /b 1)
if not exist "%AGENTIC_SCRIPTS%\cochem_idle_sidecar.py" (echo [ERROR] missing cochem_idle_sidecar.py & exit /b 1)

REM -- Daemon launch (windowless: start "" /B, output redirected to logs) -----
start "" /B python -u "%PIPELINE_ROOT%.scripts\task_work_loop.py" > "%LOG_DIR%\task_work_loop.log" 2>&1
start "" /B python -u "%AGENTIC_SCRIPTS%\cochem_ha_daemon_watchdog.py" > "%LOG_DIR%\cochem_ha_daemon_watchdog.log" 2>&1
start "" /B python -u "%AGENTIC_SCRIPTS%\cochem_idle_sidecar.py" > "%LOG_DIR%\cochem_idle_sidecar.log" 2>&1

echo [OK] Pipeline 3.0.0 daemons launched. Logs: %LOG_DIR%
endlocal
exit /b 0
