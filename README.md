# Antigravity additions 4.2.5

4.2.5 adds coding execution, Docker test isolation, verified RAM workspaces and
measured hardware controls to the protected Windows pipeline. See the
[4.2.5 execution and installation guide](docs/EXECUTION_4.2.5.md),
[requirements ledger](docs/REQUIREMENTS_4.2.5.md) and
[release notes](docs/RELEASE_4.2.5.md) for implementation and validation status.
Native Windows and live-provider acceptance remain unverified.

The final 4.2.5 Linux suite completed with **1,826 passed, 29 skipped**, including
actual Docker and offline native CLI checks. Independent copied-source
acceptance completed with **1,102 passed, 45 skipped**; its Docker tests were
disabled to preserve repair-account isolation. The release wheel also passed
source verification. These are separate runs, not additive totals.

The user-corrected model matrix remains authoritative:

| Complexity | Route order |
| --- | --- |
| 1–3 | Gemini 3.8 Flash → Claude Haiku 4.5 → GPT-6 Luna |
| 4–6 | Claude Sonnet 5.5 → GPT-6 Sol → Gemini 3.1 Pro |
| 7–9 | Claude Opus 5.5 → GPT-6 Astra, low effort → Gemini 3.1 Pro |
| 10 | Claude Fable 5.1 → GPT-6 Astra, ultra effort; only two routes |

The pipeline's four-seat ceiling, controller-owned leases/fencing, chapter isolation,
Oracle safeguards and Gemini 3.1 Pro synthesis remain required. The
[4.2.4 routing guide](docs/ROUTING_4.2.4.md) records the inherited routing
contract. This upgrade requires a fresh protected 4.2.5 supervisor and
acceptance snapshot; the old frozen validator cannot approve changed execution
contracts. Preserve native credentials, task identities and repair budgets.

## Supervisor inherited from 4.2.3

4.2.3 adds a separate supervisor for the Windows planning pipeline. It observes
native heartbeat and database progress, attempts one restart for heartbeat
failures, and can request a repair through the configured native Codex or Claude subscription
CLI. Candidate changes must pass protected external acceptance checks before
the supervisor activates a versioned release; failed health checks trigger
rollback. Repair budgets and incident history are durable.

Operator update requests use the same repair and acceptance limits. The
supervisor cannot update itself or its dependencies through that path;
database migration is outside its code-release mechanism.

See the [4.2.3 supervisor guide](docs/SUPERVISOR_4.2.3.md) and
[release notes](docs/RELEASE_4.2.3.md). The supervisor has its own protected
installation, environment and ledger, keeping candidate pipeline changes out
of the observer runtime. **Windows/live-provider repair acceptance is
not yet verified.** The unresolved native Agy contract from 4.2.2 also remains
a prerequisite for real Gemini synthesis; a supervisor does not supply missing
CLI capabilities or authentication.

The 4.2.3 integrated Linux run completed with **866 passed, 7 skipped** and two
Authlib deprecation warnings; the skipped cases require native Windows. The
4.2.3 wheel build passed. Codex 0.142.0 and Claude 2.1.101 version/help contracts
were checked without inference; these results do not establish live repair
or Windows acceptance.

## Planning pipeline inherited from 4.2.2

4.2.2 adds the planning controller specified by the four supplied SRS/architecture
documents: one request creates a SQLite DAG, Codex generates the manifest,
Codex/Claude draft isolated chapters within a four-worker ceiling, and Gemini
3.1 Pro receives accepted chapter hashes for synthesis. The controller also
provides the debounced Oracle, fenced retries, immutable artifacts and recovery
telemetry.

**Windows deployment and live Gemini/Agy synthesis are not yet verified.** The
installed Agy CLI's actual headless arguments, authentication and structured
result contract must be confirmed before a full workflow can complete. Linux
component tests establish local behavior; they do not prove Windows isolation
or live provider execution.

The 4.2.2 release recorded **420 passed, 3 skipped** in its integrated Linux
suite; Windows security/process tests were the skipped cases. Its frozen
dependency synchronization and wheel build also passed. These are historical
4.2.2 results, not evidence of live 4.2.3 repairs.

- [4.2.2 Windows installation and workflow guide](docs/PIPELINE_4.2.2.md):
  protected SYSTEM controller, six dedicated identities by default, native
  account logins, and an unprivileged Antigravity MCP frontend.
- [Requirements and evidence map](docs/REQUIREMENTS_4.2.2.md): all four source
  specifications, test evidence and remaining host acceptance checks.
- [4.2.2 release notes](docs/RELEASE_4.2.2.md) and
  [audit of the earlier 4.2.0 components](docs/4.2.0_COMPONENT_AUDIT.md).

The standalone MCP bridges introduced in 4.2.1 remain available. They connect
Antigravity's Gemini interface agents to real,
headless **Codex CLI** and **Claude CLI** processes using their native subscription
logins. Each provider has its own MCP server and job receipts. A submitted job is
not a completed job, and an unavailable provider never falls back to another.

The primary setup uses **Windows 11, Windows Python 3.12+, and native Windows
CLIs**, with Antigravity 2.0 as the GUI. A separate WSL2 setup is documented for
native Linux Codex. For these standalone bridges, Gemini and Agy remain the
interface/orchestration layer. The full 4.2.2 pipeline instead uses the protected
Windows deployment described above, including a configured native Gemini runner.

## Standalone bridge setup

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
.\.venv-mcp\Scripts\python.exe -m pytest mcp_tests pipeline_tests supervisor_tests -q
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
module is absent. Use `cochem-pipeline` for the full 4.2.2 planning workflow or
the independent bridge entries for individual Codex/Claude jobs.

The proposed [cloud setup recipe](config/cloud-environment.proposed.json) is
saved in the repository. Applying it to the current environment draft failed
with `stale_base`; a new setup chat is needed only to save that environment
recipe against a fresh draft.
