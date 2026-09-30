# Fable 5.1 & Opus 5.5 Audit Prompt
## Task Context
The orchestration architecture has transitioned to V4.1.2. The Host Warden daemon was redesigned from a raw infinite loop into an asynchronous FastMCP Server that manages the ephemeral Hyper-V Quarantine VM via PowerShell.

I just implemented two key changes:
1. In `src/cochem/warden/mcp_server.py`, I explicitly injected the `fastmcp` module, instantiated `mcp = FastMCP("cochem_warden")`, and added `@mcp.tool()` decorators to expose `get_vm_health_status`, `trigger_vm_resuscitation`, and `get_crash_envelopes`. I also added the `if __name__ == '__main__': mcp.run()` block.
2. In `provisioning/Install-WardenDaemon.ps1`, I wrote the PowerShell script to register the MCP Server in the Windows Task Scheduler configured with `<RunLevel>HighestAvailable</RunLevel>`. This natively executes `C:\Python314\python.exe D:\__CoChem\__agentic\v4.1.2\src\cochem\warden\mcp_server.py` as `SYSTEM`, ensuring the FastMCP transport has the necessary elevated context to execute `Start-VM` cmdlets securely when the agents request it.

## Directives
Please execute a rigorous architectural audit (acting as the Fable 5.1 Director) using Opus 5.5 sub-agents to verify the implementation. 
Review the codebase in `D:\__CoChem\__agentic\v4.1.2\`. 
Determine if any improvements are necessary to guarantee robust stdio/SSE transport and error handling for the HighestAvailable daemon. Output your findings to `D:\__CoChem\__agentic\v4.1.2\FABLE_AUDIT_REPORT.md` and complete the pending task `MC-AUDIT-HW-01` in `job_board.db`.
