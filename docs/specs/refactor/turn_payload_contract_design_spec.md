# Turn payload contract

[繁體中文](turn_payload_contract_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **implemented**. Based on `main_v2` at `b0e875c`; the second item of the 2026-10-05 architecture review ("Make Keeper turn handoff explicit").

A player turn passes through the Supervisor, Executor, Narrator and delivery, and what they hand each other lived in two untyped dictionaries: `AgentMessage.payload` (`dict[str, Any]`, about 25 keys read and written in six modules) and `MechanicResult.check_status` (`dict[str, Any]`, ten keys written by four modules). The order of the stages and the evidence each requires were an implicit interface: a typo in a key silently read a default, and nothing said which stage may add which key.

## Contract

`app/domain/models.py` now declares both as `TypedDict`s with the stage that owns each key.

| Type | Keys | Written by |
| --- | --- | --- |
| `TurnPayload` | `conversation_id`, `user_id`, `display_name`, `speaker_role`, `text`, `resolved_location`, `state`, `character`, `combat_provisional`, `resolved_check_events`, `rag_context`, `memory_context`, `rag_status`, `memory_status`, `correction_context` | `context_builder`, once, when the payload is built |
| | `turn_kind`, `intent`, `resolved_check_context`, `mechanic_result` | `supervisor` |
| | `private_messages`, `image_requests`, `observed_outcomes` | `executor` (and `narrator` appends to `observed_outcomes`) |
| | `narration_requirements`, `narration_failed` | `narrator` |
| | `delivery_envelope` | `turn_delivery.finalize` |
| `CheckStatus` | `tool_called`, `pending`, `pending_luck`, `resolved`, `scenario_evidence_blocked`, `cleared` | the Executor's tools through the tool gateway |
| | `tool_event_count`, `state_changed`, `dice_rolled` | `executor`, once the tools are done |
| | `pending`, `pending_luck`, `resolved`, `waiting_for_name`, `current_turn_state` | `turn_handoff.prepare_narrator_handoff`, from the latest state |
| | `pending`, `pending_luck` (cleared) | `turn_delivery.public_mechanic`, on a copy |

`PlayerTurnKind` and `SpeakerRole` (`"player"` or `"kp_assistant"`, previously a `Literal` inside the tool registry) live next to the payload so the types can name them; `supervisor.run_turn`, `context_builder.build_context` and the router's local `speaker_role` are typed with `SpeakerRole`, so a misspelt role no longer passes mypy at those entry points. Code further down (`tool_gateway`, `canonical_facts`, `keeper._execute_tool`) still takes `speaker_role: str`; narrowing it there is a separate change. `mypy app` now rejects an unknown key, a wrongly typed value and a key that is not a string literal. Two writes that used a computed key in `tool_gateway` and `turn_delivery.public_mechanic` were rewritten with literal keys; their effect is the same.

## Contract kept

No behaviour is meant to change. Most of the diff is annotations; the code that was restructured is the two computed-key writes above, the `pending`/`resolved` assignments in `_record_check_status`, the loop in `turn_delivery.public_mechanic` (now `_is_private_wait`, same condition), two `status.get(...)` lookups in `prompt_config`, and an `isinstance(owner, str)` guard on the executor's inventory lookup. Each is an equivalent rewrite and the full suite passes unchanged. The authority of each role is as before: the Executor adjudicates and the handoff reads the latest state; the Narrator never writes check facts; delivery has the last word on what may be shown.

## Enforcement

`tests/test_turn_payload_contract.py` fails if a declared key has no supplier (only `observed_outcomes` has two: the Executor creates it and the Narrator appends), if `context_builder` builds a different set of input keys, or if any module other than the owning stage writes a payload key (`message.payload[...] = ...`, `setdefault`, `update`, `pop`), including a write with a computed key. The same checks apply to `CheckStatus`: every key needs a writing stage, and a write from any other module (a subscript, `update`, or the dict literal that builds it) fails.

## Limits

The write gate sees subscripts, `setdefault`/`update`/`pop`, replacing or merging into the whole object, and the dict literal that builds a `CheckStatus`; it does not follow aliases (`p = message.payload; p["k"] = x`). `CheckStatus` is gated per module, not per key: `pending`, `pending_luck` and `resolved` are legitimately written first by the tool gateway and then recomputed by `turn_handoff`. The values of `pending`, `pending_luck` and `resolved` are still `dict[str, Any]`, so a misspelt inner key (`investgator`) is not caught; nested TypedDicts are a possible follow-up. `app/domain/models.py` imports `GroupState`, `Character` and `DeliveryEnvelope` under `TYPE_CHECKING` only, so there is no runtime cycle, but `typing.get_type_hints(TurnPayload)` cannot resolve them.

## Not changed

Narrator facts are still prepared by `turn_handoff`, and `AgentMessage.payload` is still a mapping; replacing it with a dataclass would touch about fifty test fixtures and is a separate change. Each role's authority (ADR 0002) is untouched.
