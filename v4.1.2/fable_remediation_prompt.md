# Fable 5.1 & Opus 5.5 Engineering Task: Ephemeral VM Lifecycle & Background Startup

## Architectural Deficiencies in the Current Implementation
The current codebase in `D:\__CoChem\__agentic\v4.1.2\` contains the core logic to spawn and manage the ephemeral Quarantine VM (`src/cochem/warden/vm_control.py` and `src/cochem/warden/mcp_server.py`), but it lacks the wiring to make the pipeline genuinely autonomous and secure.

Specifically, the following critical flaws must be fixed:

1. **Missing FastMCP Exposure:** The `mcp_server.py` defines the tools but completely forgot to instantiate `fastmcp` and expose the functions using `@mcp.tool()` decorators.
2. **Transport Mismatch (Background Execution):** You cannot use `stdio` transport if the MCP server is intended to run as a background daemon, because there is no client attached to `stdin/stdout` on boot. The server must use HTTP/SSE bound to `127.0.0.1`.
3. **Command Injection Vulnerability:** `Restart-VM -Name '{vm_name}'` accepts raw input from the caller. A malicious or hallucinating LLM could inject arbitrary PowerShell commands. You must strictly sanitize the `vm_name` variable or hardcode it to the quarantine VM name to prevent SYSTEM-level command injection.
4. **Provisioning Execution Flaws:** The `provisioning/Create-WardenTask.ps1` script is just an XML stub. It needs to be a real PowerShell script that registers the MCP server in the Windows Task Scheduler with `<RunLevel>HighestAvailable</RunLevel>` so the Python daemon can execute the `Start-VM` cmdlets securely as an administrator. Furthermore, executing the Python file directly in the scheduled task breaks relative imports; it must be launched as a module (`-m cochem.warden.mcp_server`) with the `PYTHONPATH` explicitly set to the `src` directory.

## Your Mission
Act as the Fable 5.1 Director with Opus 5.5 sub-agents. You have full `--dangerously-skip-permissions` to write files and execute commands. 

1. Refactor `mcp_server.py` to properly use FastMCP with the correct transport and security constraints.
2. Fix the command injection vulnerability in `vm_control.py`.
3. Overhaul the `Create-WardenTask.ps1` and `Install-WardenDaemon.ps1` provisioning scripts so they actually execute the Windows `Register-ScheduledTask` cmdlets to automatically spawn the pipeline on boot.
4. Execute `pytest` to ensure your new implementations didn't break the existing ecosystem.
5. Create a `FABLE_REMEDIATION_REPORT.md` documenting your exact fixes.
