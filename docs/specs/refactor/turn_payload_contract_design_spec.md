# Turn payload contract

[繁體中文](turn_payload_contract_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `refactor`. Status: **implemented**. Based on `main_v2` at `b0e875c` (2026-10-05); the second item of the 2026-10-05 architecture review ("Make Keeper turn handoff explicit").

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

`PlayerTurnKind` moved next to the payload so the type can name it; `supervisor` still exports it. `mypy app` now rejects an unknown key, a wrongly typed value and a key that is not a string literal. Two writes that used a computed key in `tool_gateway` and `turn_delivery.public_mechanic` were rewritten with literal keys; their effect is the same.

## Contract kept

No runtime behaviour changed: the annotations, the two rewritten writes and a `isinstance` guard on an inventory lookup are the only code changes, and the full suite passes unchanged. The authority of each role is as before: the Executor adjudicates and the handoff reads the latest state; the Narrator never writes check facts; delivery has the last word on what may be shown.

## Enforcement

`tests/test_turn_payload_contract.py` fails if a declared key has no supplier or two, if `context_builder` builds a different set of input keys, or if any module other than the owning stage writes a payload key (`message.payload[...] = ...`, `setdefault`, `update`, `pop`), including a write with a computed key.

## Not changed

Narrator facts are still prepared by `turn_handoff`, and `AgentMessage.payload` is still a mapping; replacing it with a dataclass would touch about fifty test fixtures and is a separate change. Each role's authority (ADR 0002) is untouched.
