# Every Discord event enters through the command router

[繁體中文](discord_events_through_router_design_spec_zh.md)

Status: **backlog** (awaiting spec review; no implementation yet). Base: `main_v2` at `a68df95`.

## Problem

README `## Architecture` describes one pipeline: `discord_bot.py` translates Discord events and forwards them to the command router (`app/commands/router.py`), which dispatches to `app/commands/handlers/*`. In practice only **text messages** follow it (`command_router.handle_text_message`, `app/discord_bot.py:1593`, `:2284`). Every other event calls `legacy_commands` directly (imports at `app/discord_bot.py:53-65`):

| Event | Direct call | Where |
| --- | --- | --- |
| Scenario PDF upload | `handle_pdf_upload` | `app/discord_bot.py:2188` |
| Multi-part PDF staging | loads, mutates `staged_pdf_parts` and saves state **inside `discord_bot.py`** under `locks.get_conversation_lock` | `app/discord_bot.py:2169-2175` |
| Map image upload | `handle_map_upload` | `:2202` |
| Role-sheet upload | `handle_role_sheet_upload` | `:2232` |
| Scenario compare upload | `handle_scenario_compare_upload` | `:2241` |
| Check button | `handle_check_command(…, acquire_legacy_for_keeper=False)` | `:754` |
| Luck button | `handle_luck_decision` | `:1134` |
| KP permission check | `_is_kp_or_keeper` (private) | `:1409` |

The router is where the text pipeline gets its sudo handling, Keeper-priority gate, conversation lock with queue notices, and route observability. The direct paths each assemble their own subset of these protections. Some of that is deliberate: the buttons pass `acquire_legacy_for_keeper=False` and claim pending buttons under their own lock. But whether any given path is **equivalent** to the text pipeline can only be answered by reading each one. `discord_bot.py` (2,375 lines) also holds state-mutation logic (PDF staging) and a private permission helper, which belong to layers below it.

## Goal

`discord_bot.py` only translates: a Discord event becomes a router call with a typed payload. The router owns ordering, locking, permission and observability for **every** event kind, and handlers own the behaviour.

```python
# app/commands/router.py
async def handle_upload(conversation_id, user_id, upload: Upload, reply, send_image) -> None
async def handle_check_button(conversation_id, owner_id, choice: CheckChoice, io: ButtonIO) -> None
async def handle_luck_button(conversation_id, owner_id, decision: LuckChoice, io: ButtonIO) -> None
```

## Plan

1. **Map first, no code change.** For each row above, record what it gets today: conversation lock, Keeper-priority gate, mutation admission, sudo, and whether it can interleave with a Keeper turn. Add the result to this spec. It decides whether each migration is a pure move or also a fix, and it goes through review again before step 2.
2. **Uploads** (lowest risk): add `router.handle_upload` dispatching to `handlers/uploads.py`, and move the PDF-part staging mutation out of `discord_bot.py`. One PR.
3. **Buttons:** add the two button entry points and move the claim and ordering logic from `discord_bot.py` into the router, keeping `acquire_legacy_for_keeper` semantics exactly. One PR, with the existing button/Luck race tests as the gate.
4. **Permission:** replace `_is_kp_or_keeper` with a public helper in the router or `commands/sudo.py`, named for the glossary (`CONTEXT.md`): the human is the **KP** and the server administrator is the **Host**, and "Keeper" means only the AI. Rename `_is_keeper_member` / `is_keeper` to `_is_host_member` / `is_host` and the helper to `can_administer_group` (or `_is_kp_or_host`), and change the user-facing text "KP Assistant 或 Discord Keeper" to "KP 或主辦人". The Discord role **name** stays `keeper` so deployed servers need no change; the role check carries a one-line comment saying so.
5. After step 4, `discord_bot.py` imports only `Reply`/`SendImage` types from `legacy_commands`. A test asserts that.

## Testing

- Each step's existing tests pass unchanged (`tests/test_luck_buyup_gate.py`, the check-button and upload tests, `tests/test_kp_sudo.py`).
- New: for each event kind, a Keeper turn in flight on the same conversation is ordered the same way the text pipeline orders it, based on the step-1 map.
- New: `app/discord_bot.py` has no `from app.legacy_commands import` of a callable.

## Open questions for review

- Whether uploads should pass the Keeper-priority gate, which could delay an upload behind a long turn, or only the conversation lock. Step 1's map should show what they effectively get today.
- Discord interaction deadlines: buttons must acknowledge within 3 s. Any router-level queueing for buttons has to ack first, as the direct path does today.
