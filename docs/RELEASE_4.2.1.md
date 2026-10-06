# 4.2.1: Observable Codex and Claude MCP handoffs

Antigravity can submit work directly to separate Codex and Claude stdio MCP
servers. Submission returns a job receipt; completion requires both process
success and the provider CLI's structured terminal result. Repeated status
checks read state without executing or advancing work. Failed providers are
not replaced with Gemini or another worker.

## Changes

- Added independent `cochem_mcp` servers with health, submit, status, result,
  and cancel tools, bounded queues, process timeouts, and durable receipts.
- Added native Windows CLI discovery, including safe invocation of the official
  npm Codex installation through Node, and a separate native WSL setup.
- Reused CLI subscription login while removing API/backend overrides and
  isolating worker settings and MCP configuration. Prompts are sent on stdin.
- Required structured CLI success and a session ID before publishing a result.
  Requested model IDs remain distinct from models reported by the CLI.
- Added a Windows installer, local configuration examples, and an acceptance
  script that verifies an actual worker-created file independently.
- Made the optional legacy v3 import lazy so its absence produces an explicit
  submission error instead of preventing the legacy MCP server from starting.

See [the setup guide](MCP_4.2.1.md) for migration, model configuration, and
the required Windows acceptance checks. Existing local configuration is
preserved by the installer. The legacy scientific package remains available.

## Validation scope

`python -m pytest mcp_tests -q`: **136 passed** on Linux. The wheel and source
distribution build successfully. Official Claude Code 2.1.290 help accepts the
configured command flags; that check makes no inference request.

The [regression suite](../mcp_tests/) covers executable selection, subscription
authentication, result parsing, job states, cancellation, timeouts, state
ownership, restart handling, and rejection of unsupported provider fallback.
Fixture subprocesses establish those behaviors without claiming real model
responses.

A live cloud attempt initialized MCP, submitted a job, and launched a real
Codex process. The CLI exited with code 1 before inference because the cloud
runtime/CODEX_HOME was read-only, and the bridge recorded failure. This is
evidence of process delegation and failure reporting, not successful backend
inference. Windows installation, Antigravity integration, actual Claude
execution, and the example model IDs require verification on the target
machine with its own subscription accounts.

The legacy archived MCP configuration contained a credential value. It has been
removed from the current files, but remains in existing Git history. Rotate that
credential in its owning service; this release does not rewrite history.
