# Routing research and selected policy — 2026-10-07

The selected policy retains the user's model-family order, includes complexity 3
in the lightweight tier, uses the confirmed Fable 5.1 target, and sets GPT-6.1 Sol
to `medium` for complexity 4–6 and `high` for complexity 7–9. The latter settings
are engineering choices intended to balance reasoning depth, latency and quota
consumption. They are not claims of measured superiority or vendor equivalence.

## Selected routing policy

Every model job, including planning, coding, review and repair, uses the job board
and the applicable complexity route. Arrows denote the existing ordered
availability/spillover policy; they do not denote simultaneous execution of every
candidate. After the tier's candidates are unavailable, the existing bounded
backoff returns to the preferred candidate. Complexity 10 has two candidates.

| Complexity | Preferred | Second | Third |
| --- | --- | --- | --- |
| 1–3 | Gemini 3.8 Flash, default | Claude Haiku 4.5, default | GPT-6 Luna, `low` |
| 4–6 | Claude Sonnet 5.5, default | GPT-6.1 Sol, `medium` | Gemini 3.8 Flash, Extended |
| 7–9 | GPT-6.1 Sol, `high` | Claude Opus 5.5, Extended | Gemini 3.1 Pro Preview, `high` |
| 10 | Claude Fable 5.1, Extended | GPT-6 Astra, `ultra` | — |

The model-family order is a provisional quality and availability policy, not a
published head-to-head ranking. In particular, the retrieved sources do **not**
establish that GPT-6.1 Sol and Claude Opus 5.5 have equivalent coding quality.

The policy is captured when a job is submitted. Older v1 captures remain
decodable and byte-identical, and completed attempts retain their original
receipts. They do not authorize a new attempt to execute a target retired by the
active v2 deployment policy. Before reserving capacity, the board checks the
captured candidate's exact model/profile identity against current availability;
an unavailable identity advances or backs off in the captured order using the
normal rules. This does not reset retry budgets, rewrite captures, update old
receipts or silently alias an old model to its replacement. New jobs capture the
explicit v2 policy.

## Verified identifiers and effort vocabulary

| Model | Identifier in retrieved official source | Selected intent |
| --- | --- | --- |
| Gemini 3.8 Flash | `gemini-3.8-flash` | Default for 1–3; Extended for 4–6 |
| Claude Haiku 4.5 | `claude-haiku-4-5` | Default |
| GPT-6 Luna | `gpt-6-luna` | Native `low` |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | Default |
| GPT-6.1 Sol | `gpt-6.1-sol` | Native `medium` or `high`, according to tier |
| Claude Opus 5.5 | `claude-opus-5-5` | Extended |
| Gemini 3.1 Pro Preview | `gemini-3.1-pro-preview` | High thinking |
| Claude Fable 5.1 | `claude-fable-5-1` | Extended |
| GPT-6 Astra | `gpt-6-astra` | Native `ultra` |

The [official Codex catalogue][codex-models] advertises `low`, `medium`, `high`,
`xhigh`, `max` and `ultra` for Sol 6.1 and Astra. Luna advertises those levels
through `max`. The user's “Light” label corresponds to native `low`; it is not an
additional native effort value.

The [official Anthropic model type][claude-models] and [Claude Code changelog][claude-changelog]
identify Sonnet 5.5, Opus 5.5, Fable 5.1 and Haiku 4.5. Fable 5.5 was not verified
in these sources. The final selected policy uses Fable 5.1 after clarification;
the original research record preserves the earlier uncertainty. Absence from a
catalogue is not proof that a model cannot exist or become available later.

The [Anthropic effort type][claude-effort] permits `low`, `medium`, `high`,
`xhigh` and `max`. Offline inspection of the existing Claude Code 2.1.291 binary
with `--version` and `--help` independently confirmed that `--effort` vocabulary.
No inference or account access was exercised. Thinking and effort are separate:
the changelog documents `alwaysThinkingEnabled` and cases where `xhigh` or `max`
requires thinking to be enabled.

The [Gemini CLI model constants][gemini-models] name `gemini-3.8-flash` and
`gemini-3.1-pro-preview`. They describe the latest Flash model as experiment-gated;
the catalogue alone does not establish access for a particular subscription.
The stable identifier `gemini-3.1-pro` without `-preview` was not verified. The
[Google SDK thinking enum][gemini-thinking] provides `MINIMAL`, `LOW`, `MEDIUM`
and `HIGH`, and [Gemini CLI configuration examples][gemini-config] use
`generateContentConfig.thinkingConfig.thinkingLevel`.

**Extended is an explicit pipeline intent profile, not a literal provider flag.**
The protected deployment contract must map that intent to the installed CLI's
verified thinking and effort controls. For example, a reviewed Claude profile
can combine supported effort with thinking enabled; a reviewed Gemini profile can
map Extended to supported high thinking. This document does not authorize passing
`--effort extended`, inventing an Agy `--thinking` flag, or treating a label as
proof that the provider applied it. Default means the reviewed CLI/model default,
not zero reasoning.

Record the requested model/profile, executable identity and contract revision in
each attempt. Record actual model and effort metadata when the provider exposes
it. Missing actual metadata must remain explicitly unavailable rather than being
filled with the requested value. A model or profile without an adequate protected
contract must be held or spill over through the established routing policy.

**Host Agy proof was not obtained.** Public Gemini CLI source does not establish
Antigravity Agy's flags, aliases, output schema, subscription entitlement or
Windows behavior. Those require the installed executable's capability evidence
and deployment acceptance.

## Why these effort choices

The Codex catalogue describes Luna as fast and affordable for easier tasks, Sol
6.1 as the latest coding and everyday-work model, and Astra as frontier
intelligence for demanding work. Gemini's [model selection guidance][gemini-selection]
distinguishes fast Flash work from complex reasoning on Pro. These descriptions
support broad roles, not exact numerical complexity boundaries.

- **1–3:** Keep the latency-oriented Flash, Haiku and Luna sequence. Use `low`
  for Luna rather than spending high reasoning effort on routine jobs.
- **4–6:** Keep Sonnet first. Select Sol `medium` because this band includes
  nontrivial implementation work; `low` is explicitly described as lighter
  reasoning. Flash Extended remains a capacity fallback subject to the same
  acceptance checks, not a claim that Flash equals Sonnet.
- **7–9:** Keep Sol 6.1 first, but use `high` as the initial balance between
  complex reasoning, elapsed time and subscription consumption. Follow with
  Opus Extended and Pro high. Requiring `ultra` for every job in this band would
  increase reasoning and delegation demand without workload evidence that the
  additional demand improves accepted outcomes.
- **10:** Retain the strongest requested tier: Fable 5.1 Extended followed by
  Astra `ultra`. Provider availability, verified profile support and the job
  board remain mandatory.

Measure first-pass acceptance, escaped defects, review disagreement, end-to-end
latency, quota consumption and spillover frequency on representative pipeline
jobs before changing this ranking again. Use the same task set and acceptance
criteria across candidates, and retain failed attempts in the measurements.
Do not infer model quality solely from a model name, response length or a vendor
catalogue description.

## Native delegation and fallback must remain controlled

The Codex catalogue describes `ultra` as “Maximum reasoning with automatic task
delegation.” Its [configuration schema][codex-config] also documents multiple
agent backends and notes that an enabled V2 feature can take precedence over
`agents.enabled`. Native children must be disabled or integrated into the job
board's assignment and capacity accounting; an effort selection does not exempt
them from routing requirements.

Gemini's [model selection documentation][gemini-selection] warns that `--model`
does not override models used by subagents. Its [routing documentation][gemini-routing]
describes enabled-by-default fallback, including some silent utility fallback.
Passing a model argument therefore does not prove that every resulting model
call obeyed the external route. The protected executable contract and physical
acceptance evidence must address these behaviors.

## Evidence and limits

Research was captured on **2026-10-07**, using public, first-party GitHub
repositories at fixed commits and offline CLI help. No paid model calls,
interactive authentication, subscription quota probes or comparative inference
benchmarks were run. The managed environment's network policy allowed the GitHub
sources; provider marketing/model-documentation domains were not accessed.

The [complete machine-readable research record](evidence/ROUTING_RESEARCH_2026-10-07.json)
contains source URLs, commit hashes, content hashes, bounded excerpts, collection
time and limitations. It preserves the preliminary proposals and the initial
Fable 5.5 verification question as research history. The selected-policy table
above and the canonical SRS govern the final decision.

Primary source permalinks:

- [OpenAI Codex model catalogue][codex-models] — commit `ed59a6c1cdf5e6fc96351fd46dfc1ef8a16db385`.
- [OpenAI Codex configuration schema][codex-config] — same commit.
- [Anthropic SDK model identifiers][claude-models] and [effort type][claude-effort] — commit `18f25547f20cf5f01da69ac611e700e3bc9ebf21`.
- [Claude Code changelog][claude-changelog] — commit `765f236fe1bfcc678e0ec59af9170fb7d6771a3a`.
- [Gemini CLI model constants][gemini-models], [configuration][gemini-config], [model selection][gemini-selection] and [fallback routing][gemini-routing] — commit `ef59c532f07fbb3a58dd68bac024ae217e9c73ce`.
- [Google Gen AI SDK thinking enum][gemini-thinking] — commit `84bf19c9394bb3cc41f56c11d125cf6e5de86e3a`.

[codex-models]: https://github.com/openai/codex/blob/ed59a6c1cdf5e6fc96351fd46dfc1ef8a16db385/codex-rs/models-manager/models.json
[codex-config]: https://github.com/openai/codex/blob/ed59a6c1cdf5e6fc96351fd46dfc1ef8a16db385/codex-rs/core/config.schema.json
[claude-models]: https://github.com/anthropics/anthropic-sdk-python/blob/18f25547f20cf5f01da69ac611e700e3bc9ebf21/src/anthropic/types/model.py
[claude-effort]: https://github.com/anthropics/anthropic-sdk-python/blob/18f25547f20cf5f01da69ac611e700e3bc9ebf21/src/anthropic/types/output_config_param.py
[claude-changelog]: https://github.com/anthropics/claude-code/blob/765f236fe1bfcc678e0ec59af9170fb7d6771a3a/CHANGELOG.md
[gemini-models]: https://github.com/google-gemini/gemini-cli/blob/ef59c532f07fbb3a58dd68bac024ae217e9c73ce/packages/core/src/config/models.ts
[gemini-config]: https://github.com/google-gemini/gemini-cli/blob/ef59c532f07fbb3a58dd68bac024ae217e9c73ce/docs/reference/configuration.md
[gemini-selection]: https://github.com/google-gemini/gemini-cli/blob/ef59c532f07fbb3a58dd68bac024ae217e9c73ce/docs/cli/model.md
[gemini-routing]: https://github.com/google-gemini/gemini-cli/blob/ef59c532f07fbb3a58dd68bac024ae217e9c73ce/docs/cli/model-routing.md
[gemini-thinking]: https://github.com/googleapis/python-genai/blob/84bf19c9394bb3cc41f56c11d125cf6e5de86e3a/google/genai/types.py#L375-L387
