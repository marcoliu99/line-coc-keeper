# Splitting `app/discord_bot.py`

[繁體中文](discord_transport_split_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `27a53e2`.

## Problem

`app/discord_bot.py` was 1,862 lines mixing four responsibilities: the event handlers, the code that sends to Discord, the persistent Check/Luck/PDF buttons, and the Help/sudo/source-ready views, modals and selects. A change to a Help view meant opening the file that also holds `on_message`. See `docs/architecture/main_v2_architecture_review.md` (F11).

## Constraint: persistent buttons keep working

Check, Luck, PDF-choice, Help and Help-execute buttons are `discord.ui.DynamicItem` objects matched by the regular expression in their `custom_id`. A message posted before a deploy must still resolve after it, so the templates, and the ids the code writes, are unchanged. `tests/test_discord_custom_id_contract.py` pins them (added in the preceding change). No behaviour, text or timing changes; this is a move.

## Layout

| Module | Holds |
| --- | --- |
| `app/discord_bot.py` | the entry point: `on_ready`, the backup loop, typing, `on_message`, `_handle_message`, `main` |
| `app/discord_transport/gateway.py` | the one `discord.Client` and its intents |
| `app/discord_transport/delivery.py` | message chunking, direct messages, interaction replies, request metrics, `discord_operation` (the bounded Discord call) |
| `app/discord_transport/interactions.py` | the conversation id of a channel and the server facts for permissions (sends nothing, so every other module may import it) |
| `app/discord_transport/lifecycle.py` | `observed_interaction`, the request-lifecycle wrapper around a button/interaction callback |
| `app/discord_transport/controls.py` | the Check, Luck and PDF-choice buttons and the code that posts them |
| `app/discord_transport/help_ui.py` | the Help pages, the Help/sudo/source-ready views, modals and selects |

Names used across modules became public (`_make_reply` → `delivery.make_reply`); `_conversation_id` became `interactions.channel_conversation_id` because `conversation_id` is a local variable everywhere. Tests that patched `discord_bot.<name>` now patch the module the code lives in, and a test that patched `discord_bot.load_group_state` to steer a Help callback patches `help_ui.load_group_state`.

## Layering

`gateway` < `interactions` < `delivery` < `lifecycle` < `controls` < `help_ui`: a module imports only lower ones at module level. The one upward edge is real: `help_ui` dispatches commands through `delivery.make_reply`/`make_interaction_reply`, and those adapters attach `help_ui.SourceReadyView` when a reply is a source-ready message. `delivery` therefore imports `help_ui` inside those two functions, not at load. A first version of this split imported it at module level and made `import app.discord_transport.interactions` fail on its own, masked because the bot happens to import `controls` first; `tests/test_architecture_discord_transport.py` now checks the order and imports every module in a fresh interpreter.

## Verification

`ruff check .`, `mypy app` and the full `pytest` pass. `tests/test_button_routing.py` still asserts that the Discord layer imports only types from `app.commands.types`; it now scans `discord_bot.py` and every module in `discord_transport/`.
