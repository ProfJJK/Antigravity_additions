# Native effort contracts for pipeline 4.2.7

Routing specifies both a model and a reasoning profile. A profile label alone
does not establish that a native CLI can select it or report its actual use.
The [native effort adapter](../src/cochem_pipeline/native_effort.py) requires
protected, exact model/profile bindings for Claude Extended and Agy
Extended/High. The supplied configuration contains empty `effort_contracts`;
these are deliberate compatibility holds, not verified working bindings.

The [routing research](ROUTING_RESEARCH_2026-10-07.md) records the public model
and effort evidence. It does not replace Windows executable or account evidence.
No host Agy capability proof was obtained during this change.

## Protected configuration placement

Place reviewed bindings under `providers.PROVIDER.effort_contracts` in the
administrator-protected pipeline configuration. The exact key is
`MODEL_ID:PROFILE`, for example `claude-opus-5-5:extended` or
`gemini-3.1-pro-preview:high`. Each key authorizes only that model/profile pair.
The separately installed repair supervisor needs the same applicable bindings
in its own protected provider configuration; changing the pipeline file does not
silently update the supervisor's file.

An administrator reviews and installs a binding once for a verified executable
version. It is reused for jobs; workers do not create or modify contracts. A CLI
upgrade changes the pinned executable and requires a new review instead of
silently retaining the old attestation. Provider login remains under the
appropriate restricted worker account. Contracts contain capability evidence,
not subscription credentials, tokens, API keys or raw authentication output.

Every effort-contract object has exactly these fields; omitted or additional
fields are rejected:

| Field | Required value or bound |
| --- | --- |
| `arguments` | An explicit argument array using one of the eligible selector forms below. No shell command string or interpolation. |
| `version_arguments` | Exactly `["--version"]`; the probe is offline. |
| `executable_sha256` | Exactly 64 lowercase hexadecimal characters identifying the reviewed executable. |
| `version` | Exact nonempty output expected from the version probe, after surrounding whitespace is stripped; at most 2,048 characters. |
| `capability_reference` | Nonempty provenance for the reviewed host capability evidence; at most 2,048 characters. |
| `thinking_enabled` | `true` for Claude Extended; `null` for Agy. |
| `native_metadata` | `null` when the reviewed CLI does not report effort, or the exact native-field binding described below. |

Argument arrays are bounded to 32 nonempty strings, each at most 2,048
characters and without control characters. The current eligible effort selectors
are stricter than that general bound: exactly two arguments are accepted.
Effort arguments cannot override model selection, tools, MCP, settings, permission
policy, prompts, extensions or other protected inference controls.

The provider's `executable` must be an absolute path to a regular file, at most
1 GiB, without symlink or Windows reparse-point redirection along the path. The
runner checks the executable digest and exact offline version output against the
contract. An executable that changes during validation is rejected.

## Claude Extended

The only eligible argument form is:

```text
["--effort", ONE_OF_LOW_MEDIUM_HIGH_XHIGH_MAX]
```

The second token must be one actual lowercase native value: `low`, `medium`,
`high`, `xhigh` or `max`. The administrator selects and verifies it; `extended`
is not a permitted literal `--effort` value. `thinking_enabled` must be `true`.
The command builder merges `alwaysThinkingEnabled: true` into its protected
settings while retaining `disableAllHooks: true` and
`disableSkillShellExecution: true`. It does not replace those settings with an
unrestricted user settings file.

The existing inference-only command also disables tools, additional MCP servers,
setting sources and slash commands. The effort binding cannot relax those
controls. Extended is rejected outside this inference-only execution path.

Offline Linux inspection of Claude Code 2.1.291 `--help` confirmed the native
effort vocabulary and `--settings` support. This was not model inference and
does not certify the user's Windows binary, subscription or actual output
metadata.

This is a **nonusable, parameterized shape example**, not an activation file.
The placeholder digest and version must not be copied as evidence:

```json
{
  "effort_contracts": {
    "claude-opus-5-5:extended": {
      "arguments": ["--effort", "REPLACE_WITH_REVIEWED_NATIVE_EFFORT"],
      "version_arguments": ["--version"],
      "executable_sha256": "REPLACE_WITH_ACTUAL_LOWERCASE_SHA256",
      "version": "REPLACE_WITH_EXACT_OFFLINE_VERSION_OUTPUT",
      "capability_reference": "REPLACE_WITH_REVIEWED_HOST_EVIDENCE_REFERENCE",
      "thinking_enabled": true,
      "native_metadata": null
    }
  }
}
```

The Fable binding is a separate `claude-fable-5-1:extended` entry. An Opus
binding cannot authorize Fable merely because both use the Claude executable.

## Agy Extended and High

The adapter currently accepts only these *conditional parser forms*:

| Form | Permitted value |
| --- | --- |
| `--thinking-level`, followed by one token | `minimal`, `low`, `medium`, `high`, or the corresponding uppercase spelling |
| `--thinking-budget`, followed by one token | Extended only: decimal digits representing a value from 1 through 131072, at most six digits |

The High profile requires exactly `--thinking-level high` or
`--thinking-level HIGH`; it cannot map to a lower level or a numeric budget.
When its native metadata binding is present, it must identify an effort/level
field reporting `high` or `HIGH`. A missing native field may still be declared
`null`, which leaves the actual effort unverified. Extended rejects a zero
thinking budget; positive numeric budgets remain reviewed deployment choices,
without an inferred equivalence to High.

**These forms are not a claim that Agy supports either flag.** A binding may be
installed only if the actual protected executable, its offline help/version and
reviewed provider documentation establish the selector's meaning for that exact
model/profile. Numeric acceptance by the parser does not demonstrate that a
budget achieves High or Extended thinking. In particular, an unsupported or
inappropriate value must not be labeled High merely because it passes a bounds
check.

If the installed Agy exposes thinking only through a settings file or another
selector outside these forms, the current adapter does not support that route.
Keep the compatibility hold and implement a reviewed adapter change; do not
invent flags, weaken validation or report the route as working.

Agy effort contracts use `thinking_enabled: null`. Their executable digest,
version and `version_arguments` must exactly match the provider's separate
`inference_only` contract. That contract requires these exact fields:

```text
arguments, version_arguments, executable_sha256, version,
capability_reference, disables_tools, disables_mcp, disables_hooks,
disables_subagents, disables_model_fallback
```

All five `disables_*` attestations must be `true` and supported by actual native
capability evidence. Inference arguments must contain exactly one `{model}`
placeholder so the board's chosen model is passed explicitly. The booleans are
administrator attestations, not switches that independently disable native
behavior. They must never be set just to satisfy validation. Existing contracts
without `disables_subagents` and `disables_model_fallback` require review.

No populated Agy effort example is supplied because actual Agy flags and metadata
were not established on this host. Keep `effort_contracts: {}` until they are.

## Requested versus reported effort

`native_metadata: null` permits a reviewed invocation when the installed CLI has
no native effort field. It produces:

```json
{
  "verified_profile": null,
  "native_metadata_path": null,
  "observed_native_value": null,
  "reported_effort": null,
  "observation_status": "not_reported_by_reviewed_cli"
}
```

This is invocation evidence with an unverified actual profile. It must not be
displayed as proof that the model used the requested effort.

When the CLI genuinely reports native effort, `native_metadata` has exactly
`path` and `value` members. `path` is a list of one through four JSON object
keys, each an identifier of at most 64 characters. Its first key must be one of
`reasoning_effort`, `model_reasoning_effort`, `thinking_level`, `thinking_mode`,
`thinking_enabled`, `effort`, `thinking` or `metadata`. Keys that enter generated
answer content, such as `result`, `response`, `content`, `text`, `message`,
`messages` or `parts`, are forbidden. This list is a parser boundary, not evidence
that a particular provider emits any listed key.

The expected `value` must be a nonempty string of at most 128 characters, a
Boolean, or a signed 64-bit integer. The runner compares the native value and
its type exactly. Missing or contradictory declared metadata rejects the result;
it is not silently downgraded to “not reported.” Do not configure a metadata
path from model-authored prose or from a fabricated fixture field.

For Claude, and Agy using the `terminal-json` protocol, the enclosing native
event must be a terminal `type: "result"` event. Requested profile and actual
native value remain separate. For example, an Extended profile may be verified
through a reviewed native `max` value without claiming the CLI literally
reported `extended`. The attempt evidence retains the executable digest, version,
capability reference and contract digest as well as any actual native field.

## Codex and board-controlled delegation

The routed Codex effort values are `low`, `medium`, `high` and `ultra`, passed as
the native `model_reasoning_effort` configuration value. Sol 6.1 uses `medium`
for complexity 4–6 and `high` for 7–9; Astra uses `ultra` for complexity 10. No
Claude/Agy-style Extended contract is needed for those native Codex labels.

The protected inference policy sets `agents.enabled=false` and disables both
`features.multi_agent` and `features.multi_agent_v2`, alongside its host-tool
gates. Native children cannot bypass the pipeline's requirement that model jobs
be assigned and accounted for through the board. Offline feature checks and
host acceptance remain necessary; the `ultra` label itself is not delegation
authorization.

## Operator activation and holds

1. Under the intended Windows installation, identify the protected executable
   and capture its digest, offline version and relevant help. Review supported
   model/profile controls without exposing account secrets.
2. Review the profile's semantics and, for Agy, the separate inference-only
   isolation controls. If the installed selector is unsupported, retain the
   hold. Do not use force flags or suppress a failed capability check.
3. Install the exact binding in the administrator-protected pipeline and,
   where applicable, supervisor provider configuration. Worker accounts should
   be able to use their subscriptions without gaining permission to edit these
   controller contracts.
4. Run deployment preflight under the actual configured accounts. Missing or
   changed contracts are compatibility failures before paid repair/inference;
   normal board spillover or backoff applies. A contract alone does not prove
   subscription entitlement, quota availability or successful inference.
5. During controlled Windows acceptance, verify the recorded selected model,
   contract identity and returned native metadata where available. Preserve an
   explicit unreported state when metadata is unavailable. Do not turn an
   offline help check into a claim of completed model execution.

The shipped empty contracts and placeholders intentionally prevent unsupported
profiles from being treated as deployment-ready. Completing these host checks
is separate from passing Linux protocol-fixture tests.
