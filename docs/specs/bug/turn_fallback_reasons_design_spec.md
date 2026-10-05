# Turn fallback reasons and bounded recovery

[繁體中文](turn_fallback_reasons_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `bug`. Status: **implemented**. Source: finding CS-008 of the 5-player / 500-turn Camp Sunny validation (Workstream B). Based on `main_v2` at `aa79f22`.

Seventeen reasonable player actions got the same "this action cannot continue" reply. Nothing recorded why: the reply is produced by one function from any of a dozen validator outcomes, so the 17 cases could share a root cause or have 17. The run's logs are not in the repository, so the historical cases cannot be replayed; this change makes every future one classifiable and gives a recoverable one a second chance.

## Contract

1. **A stable reason.** `FallbackReason` (`app/domain/models.py`, `FALLBACK_REASONS`) has twelve values: `no_scenario_evidence`, `executor_no_action`, `unresolved_pending_state`, `invalid_tool_plan`, `tool_failure`, `tool_result_rejected`, `narration_failure`, `state_conflict`, `unsupported_action`, `safety_block`, `internal_error`, `unknown`. `turn_fallback.classify` maps each validator outcome of the Executor (`validation_code`, execution health, failed tools, evidence) to exactly one; `unknown` is only the last resort, and a test fails if a known code falls into it.
2. **One log event.** Every place that sends the player a generic reply logs `turn.fallback` with the reason and the turn's evidence: campaign, timeline, player, turn, scene and chapter, own pending check/Luck and others', retrieval count and hit ids, the Executor's decision, the tools attempted and which failed, and whether a recovery ran and how it ended. Sites: the Executor's blocked/incomplete/deferred resolution, the unchanged-pending reply, a failed narration, a delivery replaced for safety, and a commit refused because the timeline moved. Identifiers go through the configured redaction; no credentials or provider payloads are logged.
3. **A specific reply.** The final generic sentence of an incomplete or blocked turn is the guidance for its reason (what to do next), not "check the state or correct your action". Wording that already named a pending check or Luck decision is unchanged.
4. **One bounded recovery** (`supervisor._recover_blocked_turn`, switch `TURN_FALLBACK_RECOVERY_ENABLED`). Only when the reason is `no_scenario_evidence`, `executor_no_action`, `invalid_tool_plan` or `unsupported_action`, **and** the first attempt changed no game state, rolled no dice, produced no event, outcome, private message or image, and left every pending check and Luck decision as it found it. Then, if grounding was missing, one targeted scenario search (current scene + chapter + the player's text; lexical results accepted for this one search) is stored as `recovery_context`, and the Executor decides once more. There is no second retry, and a tool failure, a state conflict or a pending wait never retries.

## Contract kept

Mechanics stay deterministic: nothing is applied by the retry that the first attempt did not leave untouched, and no fallback text becomes a success. Existing wording for pending checks, Luck, deferral and cancellation is unchanged. `TurnPayload` gains one declared key, `recovery_context`, written only by the supervisor and read by the Executor.

## Enforcement

`tests/test_turn_fallback.py`: every validator code has a reason; every reason has distinct guidance; a recovered turn, an unresolved turn and a capped retry (at most one search and one extra Executor call); no retry after a state change, dice, an event, a tool failure or a pending wait; the switch; a failing search; the event's fields; each non-Executor site logs its reason; an ordinary turn logs nothing; and a gate that fails if the supervisor stops recording one of the generic-reply sites.

## Not covered

The 17 historical turns are not replayed (their logs are not available here), so which reasons they would have received is unknown. Whether the retry finds the missing evidence for a real scenario needs the directed real-runtime run, which was not performed.
