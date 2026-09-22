# Player-owned check resolution design spec

## Changeset tracking

- Integration branch: `main_v2`
- Base changeset: `b87625094a20fc7954ad1d05680c7987d0904812`
- Working branch base before this correction: `d841c7a7f4a6f20199add6bd392ce66c4a325f9d`
- Superseded implementation: `7f9a042` incorrectly made Keeper/system own the rolls.
- Correction implementation changeset: pending
- This document records the corrected ownership model for the existing state-isolation branch.

## Problem and goal

The check belongs to the player. The player describes an action, Keeper selects
the applicable rule and registers the pending check, then the player triggers
the roll with `/coc check` or the corresponding Discord button. The application
rolls the dice on that explicit player action, returns the authoritative result
to Keeper for narration, and never lets Keeper silently replace the player's
roll. A player may still choose between mutually exclusive defensive options;
after choosing, the player also triggers the selected check.

## Scope

- `skill_check` registers a character skill/attribute check in
  `pending_checks`; `/coc check` performs the player's roll and returns roll,
  tier, difficulty, and success to the Keeper.
- `sanity_check` registers a SAN check; `/coc check` performs the player's SAN
  roll, applies SAN loss, and registers any chained temporary-madness INT check
  for the player.
- Major-wound CON checks triggered by damage are registered as pending player
  checks and are not silently resolved by combat or `adjust_character`.
- `offer_check_choice` and `offer_npc_attack_defense_choice` remain pending
  until the player chooses an option; the player then triggers the selected
  check and the application resolves the opposed result.
- Existing Luck-spend decisions remain player choices after the Keeper roll.
- Existing persisted pending skill/SAN entries remain readable and are resolved
  by `/coc check`; new ordinary checks continue to create those entries.
- `/coc check <skill>` without a pending Keeper request may remain available as
  an explicit player-initiated check according to the existing command policy,
  but Keeper tools must not silently roll in place of the player.
- `/coc autoroll` is a group-level opt-in. It is `off` by default; only the
  current KP Assistant or Discord Keeper role may use `/coc autoroll on|off`.
  With `off`, newly requested investigator skill, attack, SAN, and major-wound
  CON checks wait for the player. With `on`, those newly requested checks may
  be resolved by the Keeper/system immediately. Existing pending checks are
  never silently consumed when the setting changes.
- Prompts, help text, Discord button labels, tests, and rule references must
  describe this ownership consistently.

## Explicit non-goals

- Pre-generated-character LUCK creation remains a player-owned `/coc luck roll`.
- The player still chooses Dodge/Fight Back (or another mutually exclusive
  choice) when the rules require a choice.
- Weapon damage calculation and combat turn ordering are not redesigned here.
- No provider, Discord API, or state timeline architecture change is required.

## Flow

```text
Player: "我攻擊怪物／我調查血跡"
        |
        v
Keeper calls skill_check or sanity_check to register a pending check
        |
        +--> autoroll=off (default): Player clicks the check button or sends `/coc check`
        |                         -> player-triggered dice + authoritative result
        |
        +--> autoroll=on: Keeper/system rolls immediately
        |
                 +--> near miss: persist pending Luck decision
                 |
                 +--> final result: return result to Keeper
        v
Keeper narrates the result

NPC attacks a player:
NPC attack roll (system) -> pending Dodge/Fight Back choice
                              |
                              +--> player chooses an option
                                   -> player triggers the defense roll
                                   -> opposed result -> Keeper narrates
```

## State and idempotency

- Ordinary checks use `pending_checks`, keyed by owner, until the player
  explicitly resolves them.
- The authoritative roll is generated once by the `/coc check` resolution path;
  the pending entry is removed atomically before the result is sent to Keeper so
  duplicate button/command submissions cannot consume a second roll.
- `autoroll_checks` is persisted on `GroupState` and defaults to `False` when
  loading old state. Toggling it affects only checks requested afterward.
- `pending_luck_decisions` keeps the existing `decision_id`, `check_id`,
  timeline, original roll, and upgrade options.
- Choice pending entries continue to use `check_id` and timeline validation for
  Discord buttons and command compatibility.

## Compatibility and failure behavior

- A persisted `pending_checks[type=skill|sanity]` is the normal player-owned
  request: `/coc check` resolves it with one application roll and sends the
  result to Keeper.
- A new `/coc check <skill>` without a pending request follows the existing
  explicit player command policy; it is never created implicitly by Keeper.
- `/coc autoroll` without an argument reports the current state; `on` and `off`
  are explicit and restricted to KP operations. The default must remain off.
- A stale choice button remains rejected by its existing check identity gate.
- A stale or duplicate Luck button remains rejected by its existing decision
  identity gate.

## Verification plan

- Unit-test ordinary skill/attack checks for pending registration, one player-
  triggered roll, result fields, and duplicate-submission protection.
- Unit-test SAN and major-wound player-triggered resolution.
- Unit-test defense choice selection followed by a player-triggered roll and
  opposed result behavior.
- Regression-test persisted pending skill/SAN snapshots and stale buttons.
- Test `/coc autoroll` permissions, default-off migration, status, and toggles;
  test both off (pending/player roll) and on (immediate system roll) branches.
- Run the full pytest suite, coverage, ruff, mypy, and compileall.
