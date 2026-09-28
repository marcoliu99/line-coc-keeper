# Every Discord event enters through the command router

[繁體中文](discord_events_through_router_design_spec_zh.md)

Status: **partial**: steps 1–3 done, steps 4–5 not started. Base: `main_v2` at `a68df95`.

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
2. **Uploads** (lowest risk): add `router.handle_upload` dispatching to `handlers/uploads.py`, and move the PDF-part staging mutation out of `discord_bot.py`. One PR. *Done:* `router.handle_uploads` / `handle_unsupported_attachment`, `handlers/uploads.py` (`Upload(filename, read)`), and audit finding 2 fixed: staging checks the hold before writing anything, and a hold that starts mid-staging discards only newly staged, unreferenced files and replies with the hold notice.
3. **Buttons:** add the two button entry points and move the claim and ordering logic from `discord_bot.py` into the router, keeping `acquire_legacy_for_keeper` semantics exactly. One PR, with the existing button/Luck race tests as the gate. *Done:* `router.handle_check_button` / `handle_luck_button` run `handlers/buttons.py`, which keeps the original order (owner, in-flight guard, acknowledge, locked validation and command, claim before unlock, restore after). Claiming and the identity checks moved to `app/services/pending_buttons.py`, which the text path uses too; `discord_bot.py` keeps only Discord I/O, passed in as `ButtonIO`. The race tests needed their **patch targets** moved from `discord_bot` to `buttons` / `pending_buttons`, with assertions unchanged. The move also exposed a silent skip: `test_state_loss_amnesia` caught `ImportError` as "discord.py is not installed", so a missing name would have skipped its identity test; it now imports the discord-free service directly and always runs.
4. **Permission:** *Superseded by `docs/specs/bug/keeper_role_superuser_design_spec.md`.* The earlier plan here, renaming the `keeper`-role helpers to `host`, rested on a wrong reading. The Discord role named `keeper` is the bot's own, so the check is a hidden superuser path to remove, not a person to rename.
5. After step 4, `discord_bot.py` imports only `Reply`/`SendImage` types from `legacy_commands`. A test asserts that.

## Step 1 audit (main_v2 at `2a61269`)

What each entry path gets today. "Guard" is `@mutation_admission.guard_async_entry` on the handler; the router additionally pre-checks `is_held` for text.

| Path | Admission | Conversation lock | Priority gate | Duplicate-submit guard | Permission | Queue notice / router observability |
| --- | --- | --- | --- | --- | --- | --- |
| Ordinary text (baseline) | router pre-check | yes | **yes** | — | sudo rules | yes |
| `/coc check`, `/coc luck` text | router + guard | yes, with notice | no | `try_acquire_check` | owner | yes |
| Check button (`discord_bot.py:687-710`) | guard | yes, plain | no | `try_acquire_check` | clicker must be owner | no (`check.button.*` events) |
| Luck button (`:1086-1105`) | guard | yes, plain | no | `try_acquire_check` | clicker must be owner | no |
| PDF upload (`:2188`, `legacy_commands.py:338`) | guard | taken twice inside the handler, extraction between | no | pending-upload check | **none, intentional** | no |
| Multi-part PDF staging (`discord_bot.py:2168-2176`) | **only at repository save** | yes, in `discord_bot.py` | no | — | none | no |
| Map upload (`:2202`) | guard | yes, in handler | no | — | none | no |
| Role-sheet upload (`:2232`) | guard | yes, in handler | no | — | uploader's own | no |
| Scenario-compare upload (`:2241`) | guard | none (read-only) | no | — | none | no |
| PDF choice button (`:1409`) | guard | yes, in handler | no | — | `_is_kp_or_keeper` | no |
| Unsupported attachment (`:2249`) | none (read-only reply) | yes | no | — | — | no |

Findings:

1. **Only ordinary text passes the Keeper-priority gate.** Every other path is serialised with Keeper turns by the conversation lock alone. This answers the open question: uploads effectively get the conversation lock only today.
2. **Staging bypasses the admission notice.** It stages files first, then saves; while the group is held, the repository's `assert_admitted` (`repositories/group_state.py:101`) raises `MutationHeld`, which falls through to the generic handler: the user sees 「發生內部錯誤」 instead of the hold notice, and the staged files are orphaned. Step 2 should check admission before staging and reply with the notice.
3. **PDF upload has no permission check, deliberately** (confirmed by Marco): even with `SCENARIO_LIFECYCLE_KP_ONLY=true`, any player may upload, and the first upload applies at once. Step 4 must keep that; only the choice button and `/coc scenario` are KP/Host-only.
4. Buttons match the text routes on locking and duplicate guarding; they differ only in the queue notice and router observability, which the 3-second interaction ack makes deliberate.

Steps 2–4 are pure moves except finding 2, which is a fix.

## Testing

- Each step's existing tests pass unchanged (`tests/test_luck_buyup_gate.py`, the check-button and upload tests, `tests/test_kp_sudo.py`).
- New: for each event kind, a Keeper turn in flight on the same conversation is ordered the same way the text pipeline orders it, based on the step-1 map.
- New: `app/discord_bot.py` has no `from app.legacy_commands import` of a callable.

## Open questions for review

- Whether uploads should pass the Keeper-priority gate, which could delay an upload behind a long turn, or only the conversation lock. Step 1's map should show what they effectively get today.
- Discord interaction deadlines: buttons must acknowledge within 3 s. Any router-level queueing for buttons has to ack first, as the direct path does today.
