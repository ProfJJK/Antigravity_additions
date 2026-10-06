# Antigravity additions 4.2.1

The 4.2.1 MCP bridges connect Antigravity's Gemini interface agents to real,
headless **Codex CLI** and **Claude CLI** processes using their native subscription
logins. Each provider has its own MCP server and job receipts. A submitted job is
not a completed job, and an unavailable provider never falls back to another.

The primary setup uses **Windows 11, Windows Python 3.12+, and native Windows
CLIs**, with Antigravity 2.0 as the GUI. A separate WSL2 setup is documented for
native Linux Codex. Gemini and Agy remain the interface/orchestration layer;
these bridges do not launch a Gemini runner.

## Set up

1. Install Python 3.12+ and the official Codex and Claude CLIs on Windows.
2. Sign in as the Windows user who runs Antigravity: `codex login` and
   `claude auth login`. Select subscription authentication.
3. From a PowerShell terminal in this checkout, run:

   ```powershell
   .\scripts\install_mcp_windows.ps1
   ```

4. Edit the generated `config/bridge.local.json`. Confirm workspace directories
   and replace example model IDs with IDs actually available to each CLI account.
5. Run the [health checks and live CLI verification](docs/MCP_4.2.1.md#3-check-each-provider-then-connect-antigravity)
   while the MCP servers are stopped. The live checks consume subscription quota.
6. In Antigravity's MCP configuration editor, merge the two `mcpServers` entries
   from the generated `config/antigravity.local.json`, preserving unrelated
   entries, then reload the MCP servers.
7. Perform the [Antigravity handoff acceptance test](docs/MCP_4.2.1.md#4-verify-one-real-codex-job-then-one-real-claude-job).

The installer preserves existing local configuration and never edits Antigravity's
configuration automatically. The requested Astra/Sol/Luna and
Fable/Opus/Sonnet/Haiku names are configurable aliases; the example IDs are not
proof that those models are available.

After health succeeds, the live checker starts an actual MCP server and CLI,
requests a unique file in a temporary workspace, and independently verifies its
contents. Run Codex first, then Claude, with configured model aliases:

```powershell
.\.venv-mcp\Scripts\python.exe .\scripts\verify_cli_mcp.py --provider codex --config .\config\bridge.local.json --model sol
.\.venv-mcp\Scripts\python.exe .\scripts\verify_cli_mcp.py --provider claude --config .\config\bridge.local.json --model sonnet
```

Git must be on PATH. Keep the corresponding Antigravity MCP server stopped
during this check because only one instance can own its provider state directory.

## Development and migration

```powershell
.\.venv-mcp\Scripts\python.exe -m pip install pytest
.\.venv-mcp\Scripts\python.exe -m pytest mcp_tests -q
```

See [the bridge source](src/cochem_mcp/), [MCP tests](mcp_tests/), and
[Windows/WSL2 configuration and troubleshooting](docs/MCP_4.2.1.md).
Tests with fixture CLIs establish process and protocol behavior; a real
Antigravity/Windows subscription handoff still needs the acceptance test on the
target machine. The Linux development attempt initialized MCP and launched an
actual Codex process, which exited with code 1 before inference because its
cloud runtime/CODEX_HOME was read-only. No successful backend inference or
Windows execution is claimed. See the [4.2.1 release notes](docs/RELEASE_4.2.1.md).

The legacy CoChem scientific package and archived workflows remain in this
repository. The older `cochem_kanban_mcp.py` now starts without the optional
`v3.submit_v3` module, but v3 submission reports an explicit error when that
module is absent. Use the independent 4.2.1 bridge entries for Codex and Claude
delegation.
