# Keeper deterministic check resolution design spec

## Changeset tracking

- Integration branch: `main_v2`
- Base changeset: `b87625094a20fc7954ad1d05680c7987d0904812`
- Working branch base before this correction: `d841c7a7f4a6f20199add6bd392ce66c4a325f9d`
- This document records the check-ownership correction to the existing state-isolation branch.

## Problem and goal

The current implementation asks a player to run `/coc check` for ordinary skill,
attack, and SAN checks. That is not the intended table flow. A player should
describe an action, Keeper should select the rule and deterministic system
should roll, then Keeper should narrate the authoritative result. A player may
still choose between mutually exclusive defensive options, but choosing an
option is not manually rolling the dice.

## Scope

- `skill_check` immediately resolves a character skill/attribute check and
  returns roll, tier, difficulty, and success to the Keeper.
- `sanity_check` immediately resolves SAN loss and any chained temporary
  madness INT check.
- Major-wound CON checks triggered by damage are immediately resolved.
- `offer_check_choice` and `offer_npc_attack_defense_choice` remain pending
  only until the player chooses an option; the system rolls the selected option
  after the choice.
- Existing Luck-spend decisions remain player choices after the Keeper roll.
- Existing persisted pending skill/SAN entries remain readable and are resolved
  by `/coc check` as a compatibility path; new ordinary checks never create
  those entries.
- `/coc check` no longer starts an unsolicited manual skill roll.
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
Keeper calls skill_check or sanity_check
        |
        +--> deterministic dice + authoritative result
        |        |
        |        +--> near miss: persist pending Luck decision
        |        |
        |        +--> final result: return result to Keeper
        v
Keeper narrates the result

NPC attacks a player:
NPC attack roll (system) -> pending Dodge/Fight Back choice
                              |
                              +--> player chooses an option
                                   -> system rolls defense
                                   -> opposed result -> Keeper narrates
```

## State and idempotency

- New ordinary checks do not use `pending_checks`.
- A deterministic check result is cached by current Keeper turn plus target and
  normalized tool input, so an LLM/tool retry in the same turn cannot consume a
  second random roll.
- `pending_luck_decisions` keeps the existing `decision_id`, `check_id`,
  timeline, original roll, and upgrade options.
- Choice pending entries continue to use `check_id` and timeline validation for
  Discord buttons and command compatibility.

## Compatibility and failure behavior

- A pre-deployment `pending_checks[type=skill|sanity]` is treated as a legacy
  pending request: `/coc check` resolves it with system dice and sends the
  result to Keeper.
- A new `/coc check <skill>` without a pending choice is rejected with guidance
  to describe the action to Keeper; it does not perform a hidden second roll.
- A stale choice button remains rejected by its existing check identity gate.
- A stale or duplicate Luck button remains rejected by its existing decision
  identity gate.

## Verification plan

- Unit-test ordinary skill/attack checks for immediate roll, result fields,
  absence of new `pending_checks`, and same-turn idempotency.
- Unit-test SAN and major-wound automatic resolution.
- Unit-test defense choice selection rolls by the system and opposed result
  behavior.
- Regression-test old pending skill/SAN snapshots and stale buttons.
- Run the full pytest suite, coverage, ruff, mypy, and compileall.
