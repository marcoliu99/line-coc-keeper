# One summary line per turn, whatever the logging settings

[繁體中文](turn_summary_log_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `27a53e2`.

## Problem

How long a player waits, and where the time goes, was only recorded as the structured `turn.phases` event, which `observability.event` drops when `LOG_ENABLED` is false. `LOG_ENABLED` defaults to false because it adds timers, counters and JSON payloads to every call. With the default `.env.example`, a deployment therefore cannot tell how long its players wait (architecture review F2). The generic "internal error" message also gave the KP nothing to search the log for (F7).

## Change

- `app/services/turn_phases.py` writes one plain line to the `app.turn` logger when a player-waited turn ends (kinds `turn` and `continuation`; background maintenance is not summarised):

  ```text
  turn.summary turn_id=… kind=turn route=gameplay_action campaign=… wall_ms=… queue_wait_ms=… retrieval_ms=… memory_ms=… executor_ms=… tool_ms=… continuation_ms=… narrator_ms=… other_ms=… fallback=…
  ```

  The `*_ms` values are the *exclusive* times already computed for `turn.phases`, every phase accounted for (`retrieval` and `memory` fold in the phases of that kind), so they add up to `wall_ms`. `route` comes from the supervisor's routing decision and `fallback` from `turn_fallback.record`, through `turn_phases.note`, which is free when there is no timeline. The line carries timings and ids only: no player id, no text. It is written at INFO to the text channel, so it appears under the default `LOG_TEXT_ENABLED=true` and needs no new setting. A failure to write it is swallowed like the rest of the timeline report.
- The generic internal-error reply now ends with `（代碼 xxxxxx）`, the last six characters of the request id that every log line of that request carries, so the KP can search the log for it. With logging fully off there is no id and the text is unchanged.

## Cost and player path

`_log_summary` costs about 4 µs per turn (measured); the summary it formats was already being computed for the event. It adds no `await`, lock, model request or player-visible text, apart from the six-character code on an internal-error reply.

## Verification

`tests/test_turn_summary_log.py` and one supervisor-level test in `tests/test_turn_phases.py`: the line appears with `LOG_ENABLED=false`, names the route and a fallback reason, carries no player id, skips maintenance, labels a continuation, and a failure to write it never fails the turn.
