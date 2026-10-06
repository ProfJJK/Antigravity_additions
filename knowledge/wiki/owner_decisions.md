# Current owner decisions

## Complexity routing

The owner supplied this exact routing order:

- 1–3 Flash → Haiku → Luna.
- 4–6 Sonnet → Sol → Gemini Pro.
- 7–9 Opus → Astra low → Gemini Pro.
- 10 Fable → Astra ultra.

The tenth tier contains two models. The host controller uses Windows Python;
provider execution uses native subscription CLI authentication.

## Failure isolation

On 2026-10-06 the owner chose: “Keep isolated task failures; amend FR009.”
The [amended watchdog specification](../.sources/ch08_watchdog_sre.md) records
the resulting distinction between structural corruption and terminal task
failures. Historical failed/cancelled workflows do not freeze unrelated work.

## Source precedence

The [primary planning architecture](../.sources/Pipeline_4_2_0_Architecture.md)
specifies the four-worker ceiling and Gemini 3.1 Pro final synthesis. The
[complete telemetry dossier](../.sources/full_dossier_untruncated.md) supplies
the missing section in the shorter dossier. Referenced canonical seven-stage
planning and Method Matrix definitions are still absent; this wiki does not
invent them or certify complete implementation.
