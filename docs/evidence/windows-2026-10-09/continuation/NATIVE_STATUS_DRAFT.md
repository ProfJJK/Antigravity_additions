# Ordinary worker native status check — prepared, not executed

This separate package leaves the frozen pipeline source and installation
manifest unchanged. Its native execution path still requires coordinator review
and real Windows acceptance after stopped provisioning and human sign-in.
No login, provider status command, model job, protected copy, or SYSTEM task has
been run while preparing it.

## Files and intended sequence

1. Review `worker-native-status.py` and `check-worker-native-status.ps1`.
2. Finish stopped provisioning and the selected worker's native human login.
   A successful login command alone is not a status receipt. The ordinary
   owner account's existing login does not authenticate a worker profile.
3. From 64-bit Windows PowerShell 5.1, inspect the plan:

   ```powershell
   & 'C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next\check-worker-native-status.ps1' -Slot slot1 -Provider codex
   ```

4. After coordinator review and a clear plan, the owner can run that exact
   command in Administrator Windows PowerShell with `-Apply`. This creates a
   fresh protected one-shot helper and no-trigger SYSTEM task. It runs exactly
   one native status command. Repeat manually for another slot/provider only
   after reviewing the preceding receipt. Supported slots are `slot1`–`slot6`;
   providers are `codex` and `claude`. Agy and the independent repair identity
   are outside this package.

The default plan performs file, hash, ACL, and scheduler inspection only. It
does not create the root/task or execute Python/native status. Existing result
roots and tasks are preserved and refused. There is no automatic retry or
overwrite. The protected pipeline/supervisor daemons must remain stopped and
disabled. The existing legacy Hyper-V task and R: startup task are untouched.
The guarded current names are `CoChem-4.2.7-Warden` and
`CoChem-4.2.7-Supervisor`; the former `4.2.2` Warden and `4.2.3` Supervisor names
are checked as well. An enabled, queued, or running guarded task blocks native
launch. No statement here attests their current host state.

## Executed commands and proof boundaries

- Codex: protected `codex.exe login status`. Success requires exit zero and the
  single exact status line `Logged in using ChatGPT`, allowing either output
  stream. Extra or conflicting output remains unverified.
- Claude: protected `claude.exe --setting-sources "" auth status --json`.
  Success requires valid nonduplicated JSON, exit zero, `loggedIn: true`,
  `authMethod: claude.ai`, `apiProvider: firstParty`, and a reported subscription
  type of `pro`, `max`, `team`, or `enterprise`. A missing plan remains
  unverified rather than inferred. Error output and inconsistent fields fail
  closed. The [official Claude CLI reference](https://code.claude.com/docs/en/cli-reference)
  documents its native authentication-status command; the prepared invocation
  and parser also follow the installed pipeline's reviewed status contract.

The helper pins the observed native executables (Codex 0.160.0 and Claude
2.1.280) and the frozen Windows launcher implementation. It checks executable
hashes before and after status. Version text is labeled as observed at the pin;
no new version command is implied. It validates protected layout/config mapping
and actual local account SIDs, then uses the installed `launch_worker` batch
token, profile, sanitized environment, exclusive SID reservation, resource
limits and Job Object. A separate global reservation allows one manual status
helper at a time. It checks the actual child token SID, process image, Job
membership and profile-directory hash through the owned process/token handles.

Status is limited to 30 seconds and 64 KiB combined output, followed by the
runtime's physical descendant/profile cleanup. Raw status is read only from
temporary handles in the SYSTEM-private directory and is not included in the
receipt, console output, or hashes. The helper never opens native credential
stores or copies owner credentials. The native CLI may refresh its own cache.
The launcher uses the provisioned worker's SYSTEM Credential Manager entry
internally, as it does for an ordinary isolated process.

The redacted receipt records allowlisted authentication fields and actual
process evidence. It does not establish the provider account's email/identity,
paid entitlement, model availability, serving model, serving effort, inference
isolation, or inference readiness. Model and login command counts remain zero.
A failed launch uses an unknown native status count if execution cannot be
established. A completed native process plus verified cleanup and unchanged
binary are required before positive authentication evidence is published.

## Receipt and failure handling

The protected result is:

`C:\Program Files\CoChem\NativeStatus4.2.7-windows-20261006-<slot>-<provider>\worker-native-status.json`

The preserved task is `CoChem-4.2.7-NativeStatus-<slot>-<provider>`. The wrapper
binds the receipt to its nonce, helper hash, SYSTEM SID, slot and provider, and
requires both the success classification and a zero task result. Task Scheduler
`0x8004130B` ends polling only; the receipt still must pass. Unknown scheduler
errors do not establish absence or success.

On timeout, missing receipt, or unverified cleanup, keep the new daemons
disabled. Preserve the task/root for diagnosis and do not retry or reuse the
worker identity until cleanup is independently established. This package does
not alter production quarantine/budget records or authorize enabling a daemon.

## Windows tests, separate from native acceptance

`worker-native-status-daemon-guard-followup-tests.xml`: **54 passed in 2.49 seconds** on
Windows Python 3.12.13. Tests cover strict redaction, positive/negative status,
API/OAuth alternatives, malformed/duplicate JSON, inconsistent status, UTF-8,
combined output bounds, deadline behavior, real temporary-file byte limits,
source pinning, PowerShell syntax/default parameters, and inert scheduler
completion/error fixtures. The actual guarded task names are tested against
enabled/running/queued scheduler fixtures, proving the launch boundary is never
reached, and both wrapper guards are exercised independently. They do not
attest SYSTEM/worker launch or the real scheduler's current state.

Earlier evidence is retained. The first draft run had 43 passes and four
fixture setup/teardown errors: pytest placed a 65 KiB parameter in Windows'
`PYTEST_CURRENT_TEST` environment variable. Bounded parameter IDs fixed that
fixture failure; the next run passed 45 tests, then the scheduler fixture
brought the count to 46. Coordinator review then caught the missing current
`4.2.7` task names, which were corrected before any native execution. The first
new guard test run had six fixture failures because its inert `New-Object`
replacement used the wrong parameter name; that fixture was corrected and all
54 tests pass. No production guard was bypassed.

Reviewed draft source hashes (SHA-256):

- `worker-native-status.py`:
  `a5a54c00a0973ec71f9b8d6b7b1af35fa05c576217d5ff56197474129c00ab4d`
- `check-worker-native-status.ps1`:
  `ce8eff2a93ac85ced6e673d921f93d464a1061f879c734aa95f28a43bc9a6ea5`
