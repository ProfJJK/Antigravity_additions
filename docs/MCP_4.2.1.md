# MCP 4.2.1: Windows subscription CLI handoffs

Antigravity's Gemini agent calls one of two stdio MCP servers. Windows Python
starts the corresponding native CLI, sends the prompt over stdin, and records
its process exit and structured result. No provider substitution occurs.

```text
Antigravity 2.0 / Gemini interface agent
  ├─ cochem-codex  → Windows Python → Codex CLI → subscription account
  └─ cochem-claude → Windows Python → claude.exe → subscription account
```

Gemini 3.1 Pro / Gemini 3.8 Flash selection belongs to Antigravity/Agy. These
servers expose Codex and Claude workers only; they do not impersonate Gemini
or implement a separate Agy runner.

## 1. Prepare Windows

Use the same Windows account for installation, CLI login, and Antigravity.
Install Python 3.12+ and current official native CLI distributions:

- [Codex CLI installation and login](https://developers.openai.com/codex/cli/).
- [Claude Code installation](https://code.claude.com/docs/en/setup).

In a regular PowerShell terminal:

```powershell
py -3.12 --version
codex --version
claude --version
codex login
claude auth login
```

Select the subscription login for each provider. Confirm it using
`codex login status` and `claude auth status`. Do not put access tokens in the
bridge JSON or Antigravity MCP entries. The bridges reuse the CLI's own login
and remove API/backend environment overrides from child processes.

The Codex CLI must support `codex exec --ignore-user-config`; check
`codex exec --help` and update the official CLI if this option is absent.
The bridge uses it to avoid inherited provider settings and nested MCP workers.
Claude must support `--setting-sources` and `--strict-mcp-config`; it runs with
empty settings sources and an empty strict MCP configuration to avoid API-key
helpers and recursive worker delegation. Native subscription authentication is
still reused. Existing CLI preference files are not modified.

Codex can be `codex.exe` or the official npm `codex.cmd` installation. For the
latter the bridge invokes its JavaScript entry point through `node.exe`, without
passing prompts through `cmd.exe`. Claude uses its native Windows executable.
Restart Antigravity after installing CLIs so it inherits the updated PATH.

## 2. Install the bridge

From the repository root:

```powershell
.\scripts\install_mcp_windows.ps1
```

The script resolves the checkout from its own location, creates `.venv-mcp`,
runs `python -m pip install -e '.[mcp]'`, and writes local configuration only
when its destination does not exist. It discovers CLI executable paths from
the current Windows PATH. It does not install CLIs or perform interactive login.
Existing local JSON files are preserved on reruns. With another Python version
or installation, supply the executable explicitly:

```powershell
.\scripts\install_mcp_windows.ps1 -Python 'C:\Python313\python.exe' -PythonArgs @()
```

The generated files are:

| File | Purpose |
| --- | --- |
| `config/bridge.local.json` | Allowed workspace roots, model aliases, CLI paths and job limits. |
| `config/antigravity.local.json` | Two MCP entries containing this checkout's absolute Windows paths. |

Both are local, ignored files. Edit `workspace_roots` to include each directory
in which workers may start. The installer initially allows this checkout.
The worker's requested `workspace` must exist inside one of those roots.
This directory check is not a substitute for the CLI's own permissions.

Check both providers' `models` and `default_model` fields. The examples reflect
the requested names:

| Provider | Alias | Example CLI ID — verify before use |
| --- | --- | --- |
| Codex | `astra` | `gpt-6-astra` |
| Codex | `sol` | `gpt-6-sol` |
| Codex | `luna` | `gpt-6-luna` |
| Claude | `fable` | `claude-fable-5-1` |
| Claude | `opus` | `claude-opus-5-5` |
| Claude | `sonnet` | `claude-sonnet-5-5` |
| Claude | `haiku` | `claude-haiku-4-5` |

These IDs have not been verified against your accounts. Use each installed
CLI's model selection/help and current provider documentation to confirm exact
IDs and access. Replace the values while retaining useful aliases. An alias
does not make a model available. Health checks do not send inference requests
and cannot validate model access; a real job must do that.

Defaults allow one worker per provider, a queue of 16 pending jobs, and a
1,800-second process timeout. Optional provider fields are `executable`,
`timeout_seconds`, `max_workers` (1–4), and `max_pending` (at least `max_workers`,
up to 100). Codex uses `workspace-write` with approval requests disabled;
Claude uses `acceptEdits`, so permission-dependent commands can be denied in
headless operation. Configure the Claude provider's optional `allowed_tools`
list when it must run your test commands unattended, for example:

```json
"allowed_tools": ["Bash(python -m pytest:*)", "Bash(git diff:*)"]
```

These are native Claude permission patterns passed using `--allowedTools`.
Choose commands appropriate to the workspace; no shell allowlist is granted by
default. Permission-denied results remain failures. Review worker changes and
run tests after a coding job.

## 3. Check each provider, then connect Antigravity

Before starting these MCP servers in Antigravity:

```powershell
.\.venv-mcp\Scripts\python.exe -m cochem_mcp --provider codex --config .\config\bridge.local.json --health
.\.venv-mcp\Scripts\python.exe -m cochem_mcp --provider claude --config .\config\bridge.local.json --health
```

Require `ready: true` and exit code 0 for each. `model_availability_verified`
remains false because these checks only validate CLI discovery and subscription
login. While a server is running, use its MCP health tool instead: a second
process using the same provider state directory is intentionally rejected.

Next run the [live checker](../scripts/verify_cli_mcp.py), Codex first and then
Claude. **These checks consume the selected subscription's quota.** Keep each
corresponding Antigravity MCP server stopped while the checker runs. Git must
be available on PATH, and the model aliases below must already map to IDs
available to your accounts:

```powershell
.\.venv-mcp\Scripts\python.exe .\scripts\verify_cli_mcp.py --provider codex --config .\config\bridge.local.json --model sol
.\.venv-mcp\Scripts\python.exe .\scripts\verify_cli_mcp.py --provider claude --config .\config\bridge.local.json --model sonnet
```

The checker creates a temporary Git repository under the first configured
workspace root, initializes MCP, submits an actual CLI job, and verifies that
the CLI created a file containing a unique nonce. Require exit code 0,
`status: "completed"`, and `file_verified: true`. It reports the process and
session receipt, and removes the temporary workspace afterward. A failure
remains a failure even if the worker's generated text claims success. Receipts
remain in the normal provider state directory for diagnosis.

Open Antigravity's MCP server configuration editor through its GUI. Back up the
current configuration. Merge the **two entries inside** `mcpServers` from
`config/antigravity.local.json` into the existing `mcpServers` object. Preserve
other servers and settings; do not replace the whole configuration with an
example. If either entry already exists, update that specific entry after
review. Reload the servers and confirm both providers expose five tools:

| Codex | Claude | Meaning |
| --- | --- | --- |
| `codex_health` | `claude_health` | CLI discovery and subscription login check. |
| `codex_submit` | `claude_submit` | Accept a job and return its receipt and `job_id`. |
| `codex_status` | `claude_status` | Read actual recorded execution state. |
| `codex_result` | `claude_result` | Retrieve completed output and execution receipt. |
| `codex_cancel` | `claude_cancel` | Cancel a queued job or terminate the running process tree. |

Use `python.exe`, not `pythonw.exe`, for stdio MCP. The server reserves stdout
for MCP protocol traffic; logs go to stderr and per-job files.

## 4. Verify one real Codex job, then one real Claude job

Use a harmless unique marker and an existing workspace. Ask the Antigravity
interface agent to perform this sequence, substituting a configured model alias:

```text
Call codex_health and require ready=true.
Call codex_submit with:
  prompt: "Do not edit files or run commands. Reply exactly MCP_CODEX_421_CHECK."
  model: "sol"
Save the returned job_id. Poll codex_status with that job_id until terminal.
Only if status=completed, call codex_result with the same job_id.
Show the returned content, job_id, provider, exit_code, session_id,
requested_model, reported_model and receipt_path verbatim.
If any step fails, report that failure and stop. Do not generate a substitute reply.
```

Then repeat with `claude_health`, `claude_submit`, `claude_status`, and
`claude_result`, alias `sonnet`, and marker `MCP_CLAUDE_421_CHECK`. These jobs
consume the respective subscription's usage. Verify every model alias you
intend to use with a small job after confirming its ID.

`queued`, `authenticating`, and `running` are not success. Only `completed`
means the process exited successfully **and** produced a valid CLI terminal
success record and session ID. `failed`, `timed_out`, `cancelled`, and
`interrupted` are terminal failures. Never submit a duplicate just because a
job is taking time. Poll or cancel the existing `job_id`.

For long results, call the result tool with its returned `next_offset` until
that field is null. Noncompleted jobs return `content: null`. A requested
model is configuration; `reported_model` can be null when the CLI does not
disclose a model in structured metadata. Do not infer a model from its generated
text. A completed CLI turn also does not establish that a coding task passed
tests.

Receipts and CLI outputs are stored outside the checkout by default:
`%LOCALAPPDATA%\CoChem\mcp\<provider>\<job_id>\` on Windows. Each job includes
`receipt.json`; started jobs also have `stdout.jsonl` and `stderr.log`, and
successful jobs have `result.txt`. An optional top-level `state_dir` changes
the parent state directory. Keep these files local; CLI output can include
project content. On restart, unfinished recorded jobs become `interrupted`
without an automatic retry. Receipts are local execution evidence, not
independent cryptographic proof of a remote model's identity.

## WSL2 alternative for Codex

Keep the Claude entry on Windows Python. To run Codex in WSL, install native
Linux Python 3.12+ and native Linux Codex in that distribution, and complete
`codex login` **inside WSL**. Windows and WSL have separate CLI installations,
paths, environments, and login state. Do not call a Windows `.exe` or `.cmd`
from the Linux bridge.

For an existing checkout at `/mnt/d/__CoChem/__agentic`, inside WSL:

```bash
cd /mnt/d/__CoChem/__agentic
python3.12 -m venv "$HOME/.venvs/cochem-mcp"
"$HOME/.venvs/cochem-mcp/bin/python" -m pip install -e '.[mcp]'
codex login
codex login status
```

Create `config/bridge.wsl.local.json` from the Windows bridge example without
overwriting an existing file. Change `workspace_roots` to existing Linux paths
such as `/mnt/d/__CoChem/__agentic`. Remove any Windows `executable` value;
set it to the native Linux Codex path if PATH discovery is insufficient.
Confirm the Codex model mapping. Check it inside WSL:

```bash
"$HOME/.venvs/cochem-mcp/bin/python" -m cochem_mcp \
  --provider codex --config config/bridge.wsl.local.json --health
```

Adapt [the WSL MCP entry](../config/antigravity.mcp.wsl-codex.example.json):
choose the exact distribution from `wsl.exe --list --quiet`, replace
`YOUR_USER` and all checkout paths, and use the absolute Linux venv Python
path. Merge only this `cochem-codex` entry into Antigravity's configuration;
retain `cochem-claude` from the Windows setup. The GUI starts `wsl.exe`, which
starts Linux Python directly. Submit Linux workspace paths to that Codex
server, and Windows workspace paths to the Claude server.

## Troubleshooting and validation boundaries

| Observation | Action |
| --- | --- |
| Native CLI was not found | Install it in the same OS as the bridge. Set `providers.<name>.executable` to its actual path, or restart Antigravity to refresh PATH. |
| Subscription login not confirmed | Run the native CLI's login/status commands as the same user. API-key login is not accepted. |
| Workspace does not exist / is outside roots | Correct the path for the server's OS and add the intended existing root in local bridge configuration. |
| Unknown model or CLI exits nonzero | Confirm the model ID/account access and inspect that job's `stderr.log` and structured stdout. |
| Codex rejects `--ignore-user-config` | Update Codex to a version advertising that option in `codex exec --help`; do not remove isolation flags to suppress the error. |
| Invalid JSON / missing terminal success | Update the provider CLI and inspect its actual output; a plain prose response is insufficient execution evidence. |
| Another server owns the state directory | Close the duplicate instance. Query health through the active MCP server. |
| Legacy v3 submission reports that its queue is unavailable | The legacy MCP server now starts without `v3.submit_v3`, but submission requires the optional v3 queue. Use these independent bridges for direct delegation. The missing v3 daemon is not restored by this installer. |

Run the [MCP regression tests](../mcp_tests/) with:

```powershell
.\.venv-mcp\Scripts\python.exe -m pip install pytest
.\.venv-mcp\Scripts\python.exe -m pytest mcp_tests -q
```

Fixture-process tests and Linux protocol tests do not validate the Windows GUI,
Windows process cancellation, your subscription entitlement, or the requested
model IDs. A live attempt in the Linux cloud environment initialized MCP,
submitted a job, and launched an actual Codex process. That process exited
with code 1 before inference because its cloud runtime/CODEX_HOME was read-only;
the bridge reported failure. This did not establish successful backend inference.
Neither Windows nor actual Windows Claude execution has been validated here.
The live checker and GUI handoff checks above are required on the target Windows
machine before treating its setup as verified.

If the MCP server is forcibly killed or the host crashes, a running CLI can outlive
its server. Such jobs become `interrupted` on restart, and are never retried
automatically. Inspect the receipt PID and command in Task Manager (or the WSL
process list), stop any surviving worker you identify, and inspect its changes
before resubmitting. A PID may have been reused; do not kill it without checking.
