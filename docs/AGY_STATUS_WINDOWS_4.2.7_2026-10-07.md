# Agy owner status: actual Windows evidence, October 7, 2026

The owner confirms that Agy is signed in and functioning. The independent
Windows capture now also establishes that the installed CLI produces useful
standalone selected-model and quota reports. It does not establish the new
isolated worker accounts' subscriptions or inference-only capability.

## Stable observation

At `2026-10-07T04:38:28.814214Z`, the owner-account capture completed these four
commands with exit status zero and unchanged executable SHA-256 before and
after each command:

```text
agy.exe --version
agy.exe --help
agy.exe -p /model
agy.exe -p /usage
```

The installed version was **1.3.1**, executable SHA-256
`38f30c7dd1ed808f5cf98fe2014de3d30903035a4f0df02d3eb72a9ff8993741`.
The [sanitized diagnostic](evidence/windows-2026-10-06/agy-status-diagnostic-1.3.1.json)
binds the private capture and all stream hashes. No natural-language model
prompt was submitted. API override environment variables were removed only
from the child environment; existing owner credentials were not copied or
replaced. Native cache refresh was possible.

| Native report | Observed value |
| --- | --- |
| Selected configuration | `gemini-3.8-flash-high`, Gemini 3.8 Flash (High) |
| Agy Gemini Models, weekly | 99% remaining; reset `2026-10-14T02:23:46Z` |
| Agy Gemini Models, five-hour | 99% remaining; reset `2026-10-07T08:40:28Z` |
| Agy Claude and GPT models, weekly | 0% remaining; reset `2026-10-10T12:31:21Z` |
| Agy Claude and GPT models, five-hour | Disabled; no reset reported |

These are capture-time observations, not current availability guarantees.
The Agy group named “Claude and GPT models” is not evidence about the separate
Claude Code or Codex subscription CLIs. The selected slug is not silently
substituted for a Chapter 06 identifier or used as proof of a serving model.
Neither report contains a native account/authentication-mode field, so the
diagnostic explicitly retains `subscription_authentication: unverified`.

## Updater observation and historical evidence

The preceding capture at `2026-10-07T04:35:38.027092+00:00` began with the old
1.3.0 executable digest and the file changed during that interval. The new
executable was then independently measured as 1.3.1. That first capture lacks
per-command before/after bindings and is retained privately as mixed-source
evidence; it must not certify either binary version's native status contract.
Its reported Gemini percentages were 98%; the stable recapture reported 99%.
No inference-use explanation is invented for that difference.

The original [native capability report](evidence/windows-2026-10-06/native-contract-followup.json)
and its 1.3.0 digest remain immutable historical evidence. A separate new
[1.3.1 capability report](evidence/windows-2026-10-06/native-contract-agy-1.3.1-followup.json)
now binds the stable capture and retains all three pending findings. The new
installation proposals and custody plan reference this report and the reviewed
1.3.1 executable; the hold remains `verification_pending`. No installed
configuration or native account was changed. The protected native copy must
preserve these exact bytes and report hash. Native self-update is not a
reproducible deployment pin.

## Implemented diagnostics

[agy_status.py](../src/cochem_pipeline/agy_status.py) parses the observed bounded
tab-separated reports without launching a process. It distinguishes zero from
disabled quota, validates UTC resets, preserves native group names and selected
model identity, and rejects changed versions/binaries, malformed records,
duplicates, command errors, stderr diagnostics and unsupported fields. Past
reset times remain visible; the parser never replenishes quota or admits jobs.

[parse_agy_status_capture.py](../scripts/parse_agy_status_capture.py) validates a
saved four-probe capture, verifies exact sibling-file byte counts and hashes,
and creates a new diagnostic JSON file without overwriting existing evidence.
It rejects a missing per-command binary binding, an arbitrary model prompt,
redirected evidence files and attempts to relabel owner evidence as an isolated
worker result. It launches no CLI and does not read a credential store.

From a source environment with this package available, run:

```powershell
python scripts/parse_agy_status_capture.py `
  --capture 'C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\agy-status-20261007T043821Z\capture.json' `
  --output 'new-agy-status-diagnostic.json'
```

Actual Windows Python 3.12.13 validation: **57 passed** in the dedicated
[test capture](evidence/windows-2026-10-06/agy-status-diagnostic-tests.xml).
These tests cover diagnostic parsing and saved-evidence integrity, not SYSTEM
launch, interactive sign-in or live model execution. They are separate from
the existing Windows consolidated results and historical Linux results.

The subsequent [proposal validation](evidence/windows-2026-10-06/agy-1.3.1-proposal-validation.json)
confirms matching 1.3.1 holds in both proposals, four shared slots, six planned
identities, unchanged repair limits and explicit `adopt_existing` for the 8 GiB
R: drive. Native command construction refuses to create that adopted drive.
Focused config, integration-hold and adoption tests passed **145 tests**.
The broader RAM diagnostic retained **195 passed, five skipped and four
WinError 1314 failures** while constructing privileged symlink fixtures.
Those physical checks remain pending. Full installed-supervisor configuration
validation also remains pending because its protected `pipeline.reviewed.json`
has not been installed; no path substitution was used to bypass that check.

## Remaining native integration work

The new 1.3.1 help bytes match the earlier help capture: no new all-tools/MCP/
hooks/subagents/fallback disable contract or standalone auth subcommand was
established. The native status output adds an operator diagnostic, not an
authentication predicate. Interactive isolated login still needs an actual
terminal transport and browser/code handoff; accepting an empty argv alone
does not repair the current redirected-stdin login path.

The official [headless guide](https://www.antigravity.google/docs/cli/headless/)
describes `/model` and `/usage` as CLI-handled reports outside the streaming
inference protocol. The [/usage guide](https://www.antigravity.google/docs/cli/commands/usage)
describes backend configuration and quota refresh. The
[authentication guide](https://www.antigravity.google/docs/cli/install/)
describes native-keyring sign-in and interactive browser or SSH authorization.
None of these status observations releases the independent inference-only or
actual-serving-model requirements. Keep canonical spillover and the four-slot
shared capacity unchanged while completing protected host acceptance.
