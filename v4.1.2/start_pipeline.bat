@echo off
:: CoChem v4.1.2 Pipeline Windows Startup
cd /d D:\__CoChem\__agentic\v4.1.2
set PYTHONPATH=D:\__CoChem\__agentic\v4.1.2\src
start /b "CoChem Warden MCP" python -m cochem.warden.mcp_server --transport sse --port 47821
start /b "CoChem DSP MCP" python -m cochem.dsp.toolkit.mcp_server --transport sse --port 47822
start /b "CoChem Knowledge MCP" python -m cochem.knowledge.server --transport sse --port 47823
start /b "CoChem Worker Daemon" python -m cochem.dsp.worker_daemon
start /b "CoChem Watchdog SRE" python src\cochem\watchdog\watchdog_sre.py
