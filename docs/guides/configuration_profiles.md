# Configuration: what each switch changes for players

[繁體中文](configuration_profiles_zh.md)

Settings are read from the environment (`.env`, see `.env.example`) when the bot starts. This page lists the ones that change what a player sees or how long they wait, with the default taken from `app/config.py`. **No setting here is recommended over its default yet**: a deviation needs a measurement, and the real-run measurements the latency work asks for have not been done (`docs/validation/camp_sunny_latency_before_after.md`).

An unrecognised value for a boolean setting (`ture`) keeps the default and is reported at startup; `1/true/yes/on` and `0/false/no/off` are accepted.

## Latency and ordering

| Setting | Default | What it changes for players | Status |
| --- | --- | --- | --- |
| `NARRATION_OUTSIDE_MUTATION_LOCK` | `false` | The next player's action can start while this turn is still being narrated, instead of waiting for the narration. Order is preserved by a narration lock. | Estimated to take the median lock hold from ~21.7 s to ~15.7 s (`docs/specs/enhancement/measured_turn_latency_priorities_design_spec.md`, WP3.5). Off because a mis-handled lock can hang a channel until restart; **not yet validated in a real five-player run**. Turns whose scenario evidence may state a mechanic keep the old behaviour. |
| `SCENARIO_RAG_ENABLED` | `false` | `false`: the whole scenario (up to `MAX_SCENARIO_CHARS`) is in the prompt. `true`: the Keeper searches the scenario with `search_scenario`, so long scenarios fit but each search is another model round trip. | The latency analysis (`search_scenario` was 79% of tool calls) concerns `true`. |
| `RETRIEVAL_REUSE_FOR_FOLLOWUPS` | `true` | The continuation after a dice roll reuses the scenario evidence of the action that caused it instead of searching again, while nothing it depended on changed. | Offline-tested; effect on answer quality not measured against a real scenario. |
| `SCENARIO_SEARCH_MAX_PER_TURN` | `5` | Upper bound on `search_scenario` calls in one turn. | |
| `TURN_FALLBACK_RECOVERY_ENABLED` | `true` | A turn that would have ended in a generic "cannot continue" reply gets one more search and one more decision first. | At most one extra search and one extra model call, only on such turns. |
| `LLM_TURN_DEADLINE_SECONDS` | `180` | Whole-turn limit for model work; past it the turn ends with a clear message and committed changes are kept. | |
| `MAX_TOOL_ITERATIONS` / `MAX_TOOLS_PER_TURN` | `5` / `4` | Bounds on the Executor's tool loop. | |

## Safety and what players are told

| Setting | Default | What it changes for players |
| --- | --- | --- |
| `GUARD_ENABLED` | `true` | A reply that fails validation is repaired by a model call (up to two attempts) instead of being sent as is. The validation itself always runs. |
| `SPOILER_PROTECTION_ENABLED` | `true` | Keeper-only scenario material is withheld from public replies. Turning it off lets the narration reveal it. |
| `PRIVACY_ISOLATION_ENABLED` | `true` | Private information and secret goals reach only their owner. The bot warns loudly at startup when this is off. |
| `DEBUG_SHOW_INTERNAL_IDS` | `false` | Shows internal ids (such as `check_id`) in replies, for debugging. Raw result-tier names are never shown. |
| `SCENARIO_LIFECYCLE_KP_ONLY` | `false` | Only the current KP Assistant may upload, reparse or cancel a scenario. Keep it off while roles are still being arranged. |

## Seeing how long players wait

| Setting | Default | What it changes |
| --- | --- | --- |
| `LOG_TEXT_ENABLED` | `true` | Plain text logs, including one `turn.summary` line per player turn (timings and ids only, no player text). |
| `LOG_ENABLED` | `false` | Structured events with timers, counters and JSON payloads (`turn.phases`, `llm.turn`, …). Adds per-call overhead; turn it on to investigate, not by default. |

## What is tested

Both values of the boolean settings above are exercised by tests that set them explicitly; the full suite runs with the defaults. There is no CI job that runs the whole suite with a non-default flag, so a non-default combination is covered only as far as its own tests go.
