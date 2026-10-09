# Separate native payload copy stage

Status: **COPIED_AND_INDEPENDENTLY_VERIFIED** at 2026-10-07 04:54 UTC. The owner
ran the exact copy helper in Administrator PowerShell and returned its five-file
copy receipt. An independent inspection verified all five installed hashes,
lengths, held-handle identities, absence of hardlinks/reparse paths and protected
owner/write ACL ancestry through Program Files, with zero failures. See
[installed verification](evidence/windows-2026-10-06/protected-native-installed-verification.json).
This stage leaves the already-issued toolchain/CPU helper and inventory unchanged.
No native CLI, login, model, installer or task was executed by this copy stage.

The new `scripts/stage_aetherdesk_427_native.ps1` defaults to a read-only preflight.
Its explicit `-Apply` copies five pinned files into the fresh directory
`C:\Program Files\CoChem\Native4.2.7-windows-20261006`. The proposed pipeline
and supervisor executable paths both match this target. The full inventory is
`config/windows/aetherdesk-427.native-payloads.json`.

| File | Bytes | SHA256 |
| --- | ---: | --- |
| codex.exe | 326872368 | `fdda5fa3cf3fb3d000b876720742857676293e4315e4b045fae6f8bd7e866d1d` |
| codex-code-mode-host.exe | 74697520 | `1d448bfde19e7a280d600d8d0bcddf77afbe9feaec1e804905becc5f39bc9db6` |
| claude.exe | 237100192 | `0e4195524b73eb77efbdf3e2b36de5322a29f0ca575dfd2d9b4f946b1d425469` |
| agy.exe (1.3.1) | 190286488 | `38f30c7dd1ed808f5cf98fe2014de3d30903035a4f0df02d3eb72a9ff8993741` |
| native-contract-agy-1.3.1-followup.json | 6736 | `82db0ae4d4f42c40d4265a2116a68b7f27947f5718bd35ca375e2ad87e89d3f5` |

Total: **828,963,304 bytes**. The Codex source is the concrete directory
`C:\Users\ansac\.codex\packages\standalone\releases\0.160.0-x86_64-pc-windows-msvc\bin`.
Inspection found exactly the two listed ordinary files and no subdirectories.
The public launcher `AppData\Local\Programs\OpenAI\Codex\bin` and intermediate
`standalone\current` are junctions; the first preview correctly refused them.
Its initial inventory is retained in
`docs/evidence/windows-2026-10-06/native-payload-inventory-alias-refused.json`.
No reparse guard was weakened. Claude's source is
`C:\Users\ansac\.local\bin\claude.exe`; Agy's is
`C:\Users\ansac\AppData\Local\agy\bin\agy.exe`. Agy's old auto-update backup
stays in place and is excluded. The report source is the exact new repository
evidence file under `docs/evidence/windows-2026-10-06`.

Offline PE import and delay-import inspection found Windows/API-set DLL names
only for these four executable files. This is static evidence, not proof of every
dynamically loaded resource or isolated runtime operation. Installed native
acceptance remains necessary. No profile, token, credential, configuration or
subscription information is copied.

The helper uses the reviewed source-handle/hash, destination `CreateNew`, protected
ACL and ancestry checks from the separate payload helper. All sources are verified
before mutations and rehashed on a held read handle during each copy. It rejects
hardlinks, reparse ancestors, destination reuse, unexpected inventory names and
digest drift; failed partial destinations are preserved. It never executes any
payload or alters existing roots. SYSTEM/Administrators receive full control and
ordinary Users receive read/execute. The capability report's protected copy does
not clear the scoped Agy integration hold. The new 1.3.1 report preserves all three
pending findings: isolated authentication, inference isolation and serving-model
identity. Earlier 1.3.0 evidence remains unchanged. Any installed configuration
must later use this protected evidence path with the same report hash; this copy
helper does not install or edit configuration.

## Administrator command executed by the owner

Use the owner's elevated **Windows PowerShell 5.1** session. The command verifies
the reviewed helper before invocation. Omit `-Apply` for another read-only preview;
with it, only the fresh native payload copy and its ACLs are created.

```powershell
$repo = 'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions'
$helper = Join-Path $repo 'scripts\stage_aetherdesk_427_native.ps1'
if ((Get-FileHash -LiteralPath $helper -Algorithm SHA256).Hash -ne 'b84eab8869ba43cba88a32c753808a92849eb873cede82ec372c50ba62e5ec4b') { throw 'Reviewed native staging helper changed' }
& $helper `
  -Manifest "$repo\config\windows\aetherdesk-427.native-payloads.json" `
  -ManifestSha256 '585ba9a855565aa4e66df8177f706a6f880ffc8d7ec4ce167715066b7e37716b' `
  -Apply
```

Capture the returned JSON. The expected copy receipt reports five files copied
and zero payloads executed, tasks/accounts changed or driver installation.
`activation_ready` remains false and supervisor history remains unresolved and
untouched. A changed source or existing destination stops the helper; do not
delete partial targets or silently replace a captured digest to retry.

## Actual Windows evidence

The real read-only preview verified all five files and copied/executed zero:
`docs/evidence/windows-2026-10-06/native-payload-stage-preview.json`.
The focused Windows run passed **9 tests in 4.53 seconds**:
`docs/evidence/windows-2026-10-06/native-payload-staging-tests.xml`.
Tests use inert file bytes and verify default read-only behavior, incomplete or
obsolete native inventories, profile/escape/duplicate destinations, source drift,
hardlink rejection and non-elevated Apply refusal. Privileged copying and native
runtime acceptance were not performed by these tests. The subsequent actual
protected copy is separately documented above; isolated native runtime acceptance
remains unrun. Earlier Linux or broad
Windows results do not certify this new helper's unrun protected installation.
