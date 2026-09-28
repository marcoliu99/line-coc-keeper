# A Discord role named `keeper` is a hidden superuser

[繁體中文](keeper_role_superuser_design_spec_zh.md)

Status: **backlog** (awaiting spec review). Base: `refactor/discord-buttons-through-router` at `5106d47` (on top of `main_v2` at `83e0c54`). Replaces step 4 of `docs/specs/refactor/discord_events_through_router_design_spec.md`.

## Problem

`app/discord_bot.py:402` `_is_keeper_member` returns true for any member with a role whose **name** is `keeper`, case-insensitively, in any server. That flag, `is_keeper`, grants the same authority as the group's registered KP Assistant, in **every** group on that server, with no registration.

On our deployment the `keeper` role belongs to **the bot itself** (confirmed by Marco). The bot's messages are ignored (`on_message` returns on `message.author.bot`) and bots can't click buttons, so the flag is never true for a legitimate user. The branch is therefore:

- **dead for its intended purpose:** no human is meant to hold it;
- **a live superuser path:** anyone who can manage roles can grant any member full KP authority everywhere by naming a role `keeper`, by mistake or on purpose;
- **misleading in the UI:** 24 occurrences in `app/` (messages, help text and comments) say 「只有目前的 KP Assistant 或 Discord Keeper 可以……」, implying a second kind of person who doesn't exist.

## What the flag grants today

Where it comes from (the transport):

| Source | Where |
| --- | --- |
| Ordinary messages | `discord_bot.py:1937` → `router.handle_text_message(is_keeper=…)` |
| Help-execute button | `discord_bot.py:1332` → `handle_text_message` |
| PDF choice button | `discord_bot.py:1145` (check), `:1156` → `resolve_pdf_upload_choice(is_keeper=…)` |
| Source-ready button | `discord_bot.py:1257` |

What it unlocks. **Always**, regardless of settings:

| Action | Where |
| --- | --- |
| `/coc sudo`: act as another player | `router.py:221` |
| Move **another player's** investigator (through sudo turns) | `router.py:190/257/326` → `supervisor.py:192` → `executor.py:86` → `movement.py:385` |
| Create, list or roll back checkpoints | `handlers/system.py:157` |
| Read scene digests | `handlers/system.py:213` |
| Manage English sources, templates and manual character cards; choose a scenario | `handlers/system.py:260`, `:313`, `:366`, `:506` |
| See full `/coc index` values | `handlers/system.py:795` |
| Approve narrative corrections | `handlers/correct.py:79` |
| Source-ready button | `discord_bot.py:1257` |

**Only when `SCENARIO_LIFECYCLE_KP_ONLY=true`** (`legacy_commands._is_kp_or_keeper`); otherwise these are open to everyone:

| Action | Where |
| --- | --- |
| Reparse, cancel or clean the scenario library; `/coc pdf` | `handlers/system.py:435/448`, `:493`, `:605`, `:647` |
| PDF choice button | `legacy_commands.py:631`, `discord_bot.py:1145` |

It does **not** change the AI turn: a role holder's ordinary chat runs as a player, without KP priority. `speaker_role="keeper"` appears only as a queue-log label (`router.py`, 10 sites).

## Decision

KP authority comes from **one** place: being the group's registered KP Assistant (`state.kp_assistant_user_id`). No Discord role grants it.

- Add a public module `app/commands/permissions.py` with:
  - `is_kp(state, user_id) -> bool`: the registered KP Assistant;
  - `may_manage_scenario_lifecycle(state, user_id) -> bool`: `not SCENARIO_LIFECYCLE_KP_ONLY or is_kp(...)`.
- Every site in the tables above uses one of the two. The split between always-checked and lifecycle-gated actions **is kept exactly**; this spec changes who counts as KP, not which actions need KP.
- Remove `_is_keeper_member`, the `is_keeper` / `actor_is_keeper` parameters end to end, and `legacy_commands._is_kp_or_keeper`. Movement authorization becomes "actor is the subject or the KP Assistant".
- The queue-log `speaker_role` becomes `kp_assistant` / `player` only.
- Messages say 「只有目前的 KP 助手可以……」. This also fixes the checkpoint and digest messages, which already said only KP Assistant.
- `/coc help` notes, `docs/references/player_command_reference*.md`, and the `.env.example`, `config.py` and `models.py` comments drop "Discord Keeper". Historical specs and the changelog stay as written.
- `CONTEXT.md`: remove the **Host** entry, which described a person who doesn't exist, and record that the server's `keeper` role is the bot's own and grants nothing.

## Replacement: handing over and taking over the KP Assistant

Today `/coc kp` registers the first caller when the seat is empty, only the KP can `/coc kp quit`, and there is no handover. If the KP Assistant disappears, the group is stuck: nobody can register, roll back, sudo or approve corrections. The `keeper` role was, in effect, the escape hatch for that. It goes, so this PR adds visible, deliberate ones:

- **`/coc kp transfer @member`**: only the current KP Assistant. The new KP must meet the `/coc kp` rules: not a bot, no investigator, no creation session. `kp_ooc_log` is cleared, as on registration.
- **`/coc kp takeover [@member]`**: for a member with Discord's **Manage Server** permission in this server (`guild_permissions.manage_guild`; Administrator implies it). It replaces any current KP Assistant with the caller, or, with `@member`, with that member. Decided with Marco: Manage Server rather than the server owner only, so a takeover still works when the owner is away.
  - The new KP must meet the same exclusivity rules as `/coc kp`. A manager who is playing an investigator can't take the seat themselves. They're refused with a hint to appoint someone instead, `/coc kp takeover @member`, naming a member who isn't playing. Decided with Marco: KP and investigator stay mutually exclusive, because the KP sees private scenario data and can roll back and act for others. No exception for takeover, and nobody's investigator is changed on their behalf.
  - If every member of the group is playing, someone has to `/coc retire` first; takeover doesn't bypass that.
- Both post a **public** message in the channel naming the old and new KP Assistant, and log `kp.transfer` / `kp.takeover` with hashed ids.
- The permission is read from Discord when the command is sent. `discord_bot.py` passes it to the router as one boolean, `can_manage_server`, so the core stays Discord-agnostic. DMs have no server, so there it is false. **It is used for `takeover` and nothing else.** Every other action still needs `permissions.is_kp`.
- They ship **in the same PR** as the removal, so there's no window without an escape hatch.

## Safety: this removes a superuser path

1. **Observable transition.** For one release, when a **non-bot** member with a role named `keeper` attempts an action that the role used to allow, log `authz.keeper_role_ignored` (action, hashed user id; no role list, no message text), then deny as for anyone else. The detection helper lives only in the transport and is deleted in a follow-up once the logs are quiet.
2. **Pre-deploy check (manual).** On each server, confirm that no human holds a role named `keeper`. If one does, register them as KP Assistant in the groups they run before deploying; otherwise they lose sudo, checkpoints and rollback there.
3. **Rollback.** Revert the PR. No state or schema changes, so nothing to migrate either way.
4. **Reviewed as a security change.** The PR lists every site from the tables and the test that pins it.

## Testing

For **every** action in both tables, three callers:

| Caller | Expected |
| --- | --- |
| The registered KP Assistant | allowed (unchanged) |
| A member who is not KP (formerly `is_keeper=True`) | **denied** with the KP-only message |
| An ordinary player | denied as today; for lifecycle-gated actions with `SCENARIO_LIFECYCLE_KP_ONLY=false`, allowed as today |

Plus:

- sudo: a non-KP member can't act as another player, and a KP-driven sudo turn can still move the subject's investigator (`movement.py`).
- Movement: an actor who is neither the subject nor the KP is rejected with `movement_actor_not_authorized`.
- The transition log fires for a role holder and never for the bot or for members without the role.
- Handover and takeover:
  - `transfer` by anyone but the KP is refused, and to a bot, a member with an investigator, or a member in a creation session is refused;
  - `takeover` without Manage Server is refused, including in a DM, and with it replaces the KP and posts the public notice;
  - a manager with an investigator is refused for themselves, with the appoint hint, and can appoint an eligible member; appointing a bot, a member with an investigator, or a member in a creation session is refused;
  - the public notice names who appointed whom;
  - `can_manage_server` unlocks no other action;
  - **manual acceptance on a real server:** one account with Manage Server and one without each try `takeover`.
- An AST check that no `app/` module references `is_keeper`, `actor_is_keeper` or `_is_keeper_member`, and no Discord role name appears in an authorization decision.
- Existing tests that authorised a caller with `is_keeper=True` (`test_scenario_authoring.py`, `test_scenario_source_authoring.py`, `test_state_persistence.py`) switch to registering that caller as KP Assistant. Their assertions about the actions stay the same; `test_state_persistence.py:246` (`_is_kp_or_keeper(state, "player", True)` is true) inverts, because that grant is exactly what this spec removes.

## Limits

- Takeover covers a missing KP Assistant in one group at a time; there is still no standing "super KP" across every group. If that need appears, it should be an explicit, opt-in grant by **user or role ID** in configuration, off by default, and never a role name. That's a separate spec.
- The Keeper holds the Keeper's adjudication authority in CoC terms (for example, ruling on a player's request to roll back), but has no tool to exercise it today. **Correction approval in groups without a KP Assistant** is covered by `docs/specs/feature/keeper_adjudicates_corrections_design_spec.md`, which must ship **before or with** this change so a KP-less group keeps a working path to a ruling. Rollback, checkpoints and sudo by the Keeper remain a separate future spec.
