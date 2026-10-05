# Handing an item to another investigator is one committed step, and an item has one holder

[繁體中文](inventory_transfer_design_spec_zh.md)

Status: **backlog** (design only; no code in this change). Base: `main_v2` at `0de529b`.

## Problem

Two real five-player runs on `1fd3ccb` show items disappearing in a hand-off, and the player is told something generic.

Dead Boarder, 200 turns, `NARRATION_OUTSIDE_MUTATION_LOCK=true` (`turns.jsonl`, `turn.fallback` events and `api-events.jsonl`):

| Turn | What was committed | How the turn ended |
|---:|---|---|
| 47 | `remove_carried_item` only (D's old book removed; the receiver B never got it) | `executor_no_action`, generic reply |
| 50 | `search_scenario`, `remove_carried_item` | `executor_no_action` |
| 56 | `add_carried_item`, `search_scenario` | `llm.turn.failed`, `TimeoutError` after 120 s with 2 tool calls |
| 94 | `search_scenario` ×2, `remove_carried_item`, `add_carried_item` (four tools) | `executor_no_action` |
| 140 | `remove_carried_item`, `add_carried_item`, `search_scenario` ×2 (four tools) | `llm.turn.failed`, `TimeoutError` after 120 s with 4 tool calls |
| 199 | `remove_carried_item` only (A's forged receipt removed, D never got it) | `unsupported_action` |

The run report counts this as DB200-F3 (a likely issue, "root cause unconfirmed"). Reading the code confirms a cause that does not depend on the model being careful.

## Why it happens (read from the code)

- **A hand-off is two independent commits.** `remove_carried_item` and `add_carried_item` (`app/keeper_tools/inventory.py`) each run their own `mutate_tool_state` and persist at once. Nothing makes the second follow the first. If the turn stops in between (a model that does not finish, a Codex timeout, `CodexError`, the tool budget), the item has left one character and arrived at none.
- **A committed mutation outlives its turn on purpose.** `codex_provider` does not cancel a worker-thread mutation on timeout ("Do not cancel a committed/worker-thread mutation on LLM timeout"), and the player is told "已提交的變更會保留". That is right, but it makes a half-done hand-off permanent.
- **The turn deadline covers the whole Executor run.** `CODEX_TIMEOUT` (120 s) is set once per `run_conversation` (`request_owner.deadline()`), not per model decision. Turns 56, 133 and 140 timed out at 120.017 s after two to four tool calls, so a compound action with several tool rounds can spend the whole budget before it finishes the second half of a hand-off.
- **A hand-off costs two of the four tools a turn may use** (`MAX_TOOLS_PER_TURN=4`). Turns 81, 94, 140, 145 and 148 used exactly four tools and ended incomplete or failed.
- **A hand-off is recorded as a consumption.** `remove_carried_item` appends to `consumed_or_removed_items`, which the Narrator receives as `historical_removals`. An item that was given to a teammate is therefore described to the model as used up or lost.
- **The check that the hand-off was whole happens afterwards.** `turn_resolution._mutation_evidence` recognises a matched remove-then-add pair after both are committed and otherwise returns "incomplete". It cannot undo the first half.
- **Nothing limits whose inventory a tool changes.** Both handlers take any `investigator` and resolve it with `find_character`, which falls back to a substring match. A tool call from player A's turn can edit player B's character, and a near-match name can pick the wrong one.
- **Nothing says an item exists once.** Two characters can hold "古書", and a hand-off can leave it with both or neither.

## Rules

1. **One step.** A hand-off between investigators is a single tool call, `transfer_item(from, to, item, source_event_id?)`, that validates everything first and then changes both inventories in one `mutate_tool_state`. It either happens entirely or not at all.
2. **Validate before mutate.** The transfer is refused, with a reason the Keeper can use, and nothing is written, when: the giver is not the acting player's character (unless the speaker is the KP Assistant); the receiver is unknown, not active, or the same character; the item is not in the giver's list by exact normalised match (no substring fallback); or, for a unique item (rule 5), the receiver already holds it.
3. **Only your own character, except by transfer.** `add_carried_item` and `remove_carried_item` act only on the acting player's character (`ToolCall.actor_id` already carries it). Another investigator's inventory changes only through `transfer_item`, or when the speaker is the KP Assistant. The correction paths (`correction_adjudication`, `natural_corrections`) keep working because they act on the claimant's own character.
4. **A remove says why.** `remove_carried_item` takes a `reason` of `consumed`, `lost`, `dropped` or `destroyed`. `given`/`handed over` is refused with a pointer to `transfer_item`, so a bare removal can no longer stand in for a hand-off. `consumed_or_removed_items` keeps the reason; a transfer writes a separate `inventory_transfers` record (id, turn, from, to, item) and not a consumption.
5. **An item acquired in play has one holder.** An item added after the game started is unique by normalised name while someone holds it: a second `add_carried_item` of it to another investigator is refused with "already held by X; use transfer_item". Starting equipment is not unique (each investigator may carry a flashlight). Where the item sits when nobody holds it (dropped in a room, held by an NPC) is not modelled yet; see "Not done".
6. **The reply matches the state.** When a turn ends in a fallback, the guidance names what was committed, taken from the tool events and not from model text ("已提交：D 把〈古書〉交給 B"), so a half-finished turn no longer reads as nothing happened. This extends `turn_fallback.guidance`.

## Changes (for the implementation)

- New handler `inventory.transfer_item` and tool schema in `keeper_tools/registry.py`; descriptions of `add_carried_item`/`remove_carried_item` say when not to use them for a hand-off.
- `inventory.py`: own-character check from `ToolCall.actor_id`; exact item match; `reason` on removal; unique-item check; the transfer record on state (`inventory_transfers`, persisted like `consumed_or_removed_items`).
- `turn_resolution._mutation_evidence`: a successful `transfer_item` event is one verified transfer; the remove-then-add pair matching stays during migration and is removed once the prompt no longer produces pairs.
- Prompt text that tells the Executor to call add/remove for hand-offs (`turn_context.py`, `prompt_builder.py` "Equipment Consistency") says to call `transfer_item` for a hand-off.
- Observability: an `inventory.transfer` event (committed) and an `inventory.transfer.refused` event with the reason; `scripts/summarize_turn_log.py` counts refusals.
- Tool budget: a hand-off uses one of the four tools instead of two.

## Verification the implementation must include

- Each observed case is a regression fixture: turn 47 and 199 (a removal with no receiver must be impossible), 56 (an add with no matching remove), 140 (a four-tool turn that times out after the hand-off: the item is with the receiver and the reply says so).
- Atomicity: a failure injected between the two inventory writes leaves both inventories unchanged.
- Refusals: wrong giver, unknown receiver, receiver equals giver, item not held, substring-only match, unique item already held by the receiver; each writes nothing.
- A removal with `reason=given` is refused; `consumed` is accepted and recorded.
- The correction paths still add to the claimant's character.
- Fallback guidance names a committed transfer and nothing else.

## Not done

- Where a unique item is when nobody carries it (left in a room, held by an NPC such as the landlady in turn 199). Rule 5 only covers items held by investigators. Modelling scene and NPC holders needs a location record per item and a scenario-side list of which items are props; it is a larger change and should wait for evidence of how often it happens.
- Undoing a half-finished hand-off already in a saved game, and finding items lost by earlier runs.
- Party-wide grants ("everyone receives a copy"): each is an ordinary `add_carried_item` on each character.
- Retrying a turn after an Executor timeout; that is a separate decision (a turn that has committed anything is not safe to replay).
