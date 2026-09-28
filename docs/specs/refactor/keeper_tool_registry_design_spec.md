# Keeper tools as a registry

[繁體中文](keeper_tool_registry_design_spec_zh.md)

Status: **partial** (reviewed; registry and derived sets implemented, handler families pending). Base: `main_v2` at `1a31645`.

> Refreshed against current `main_v2`: `main_v2` since reverted PR #99/#121's movement pipeline (`docs/specs/bug/movement_authorization_diagnosability_design_spec.md`). `app/services/movement.py`, `movement.TOOL`/`commit_movement`, and `ORIGIN_TOOLS` no longer exist, so they're dropped below along with the migration step that named them. Everything else here still matches the current code, with refreshed line numbers.

## Problem

A Keeper tool's definition is split across three places that must stay in step by hand.

1. **Schema.** `keeper.TOOLS` (`app/keeper.py:97`) holds 35 JSON schemas.
2. **Behaviour.** `_execute_tool` (`app/keeper.py:1849-…`) runs shared gates first: mutation admission and the KP-assistant `roll_dice` context and allow-list. Then it runs a 34-branch `if name == "…"` cascade. Each branch defines its own nested mutator closure over `state`, `tool_input`, `private_messages`, `image_requests` and `speaker_role`.
3. **Properties.** What a tool *is* (read-only, KP-assistant-allowed, creates a check, invalidates combat status, …) lives in about a dozen name sets across six modules:

| Set | Where |
| --- | --- |
| `READ_ONLY_TOOL_NAMES`, `RESOLVED_CHECK_FOLLOWUP_TOOL_NAMES` | `app/keeper.py:805`, `:819` |
| `_KP_ASSISTANT_ALLOWED_TOOL_NAMES`, `_KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES` | `app/keeper.py:887`, `:911` |
| `_COMBAT_STATUS_INVALIDATING_TOOLS` | `app/keeper.py:3589` |
| `BOUNDED_QUERY_TOOLS`, `_CHECK_REGISTRATION_TOOLS` | `app/agents/tool_gateway.py:22`, `:218` |
| `_OPENING_TOOL_NAMES` | `app/agents/narrator.py:18` |
| `_CHECK_CREATION_TOOLS` | `app/services/turn_context.py:99` |
| `INFORMATION_QUERY_TOOLS` | `app/services/turn_resolution.py:18` |

Adding or changing one tool therefore means editing the schema, a cascade branch, and every set it should belong to. Missing a set fails silently. For example, a new check-creating tool left out of one of the two check-creation sets (`tool_gateway` and `turn_context`) is treated as not creating a check in that layer. The review behind `CODING_STANDARDS.md` flagged the cascade as the repo's largest function and `keeper.py` as its most-changed file.

## Goal

Each tool is declared **once**: its schema, its handler, and its properties in one place. The gates, the name sets and the dispatcher are all derived from those declarations.

```python
@dataclass(frozen=True)
class ToolSpec:
    schema: dict
    handler: Callable[[ToolCall], dict]
    read_only: bool = False
    kp_assistant: bool = False             # allowed for speaker_role == "kp_assistant"
    creates_check: bool = False
    invalidates_combat_status: bool = False
    # … one flag per set above, named for the property rather than the consumer

@dataclass
class ToolCall:                            # the Data Clump the handlers share today
    state: GroupState
    input: dict
    private_messages: list[tuple[str, str]]
    image_requests: list[tuple[str | None, int]]
    speaker_role: SpeakerRole              # Literal["player", "kp_assistant"]
```

`_execute_tool` becomes: shared gates → `REGISTRY[name].handler(call)`. Each existing set becomes `frozenset(n for n, t in REGISTRY.items() if t.<flag>)`, and consumers keep their current names during migration.

## Plan: strangler migration, one family per PR

1. **Registry and derived sets, no handlers moved.** Add `ToolSpec` and `REGISTRY` with every tool's schema and flags. `handler` falls back to the existing cascade. Replace each name set with its derived equivalent. A test asserts that each derived set equals the old literal set; that equivalence test is the safety net for everything after.
2. **Move handlers family by family:** dice, checks, character attributes, inventory and status, combat, scenario and search, messaging. Each family is one PR. Its branches become module-level functions (e.g. in `app/keeper_tools/combat.py`), and the cascade branch is deleted. Behaviour tests for the family must pass unchanged.
3. **Delete the cascade** once it's empty, along with the equivalence test's old literal sets.

## Testing

- Step 1: derived-set equivalence for every set in the table, plus `schema.name` matching the registry key for every tool.
- Each step-2 PR: the family's existing tests pass without edits. Add a registry test that every schema has a handler and every handler has a schema.
- Throughout: the full suite and `tests/test_tool_gateway_speaker_role.py` (KP-assistant gating) stay green.

## Decisions from review

- **Location:** handlers live in `app/keeper_tools/`, one module per family (`dice.py`, `checks.py`, `combat.py`, …). They're the Keeper's capabilities, a different kind of thing from the flow services in `app/services/`.
- **Schema order:** provider prompt caching includes the tool list in the cached prefix, so the registry preserves declaration order and a test pins the order sent to providers.
- **Sequencing:** the combat-family PR comes after `bug/major-wound-con-check-gate` and `refactor/combat-start-in-combat-module` land, because they edit the same branches.

Marco reconfirmed all three decisions before implementation. The first migration step also registers `report_summary`, whose schema is used only for log summarization and is not in the 35 player-turn tools. It retains the old unknown-tool result if dispatched through `_execute_tool`; the registry includes it so the existing read-only and opening sets remain exactly equivalent. The combat-start and major-wound prerequisites are present in `main_v2`; their behavior is retained by the combat handler migration.

The checks family (skill, SAN, NPC checks, defense choices, and pending-check clearing) is migrated to `app/keeper_tools/checks.py`. Check cache, ownership, metadata, and state mutation remain in Keeper behind a temporary public service seam. This branch needs re-alignment with the separate check-lifecycle refactor before integration.

The dice family (`roll_dice`, `roll_impaling_damage`, `roll_weapon_damage`) is migrated to `app/keeper_tools/dice.py`.

The character family (`adjust_character`, `set_skill`, `get_character_sheet`) is migrated to `app/keeper_tools/character.py`. Major-wound CON registration and state refresh still use Keeper's existing authoritative helpers via a public migration seam. This branch will need re-alignment when the separate check-lifecycle refactor lands.

The inventory/status family (`adjust_ammo`, carried-item add/remove, status-tag add/remove) is migrated to `app/keeper_tools/inventory.py`. These handlers still use Keeper's single state mutation boundary through a public migration seam.

The scenario/search family (`record_established_fact`, `record_clue`, image search/display, chapter advance, scenario search, and memory search) now lives in `app/keeper_tools/scenario.py`. The chapter-advance and fact-record handlers still use Keeper's single state mutation boundary through a public migration seam.

The messaging family (`send_private_info`) is migrated to `app/keeper_tools/messaging.py`. The combat family is migrated separately.

The combat family (`start_combat`, NPC admission, status, turn progression, damage, enemy plans, effects, and end combat) is migrated to `app/keeper_tools/combat.py`. Combat rules stay in `app/combat.py`; state writes, blocked-hit no-save behavior, and public damage filtering still use Keeper's authoritative helpers through a public migration seam. All families are now combined on the integration branch; the final step removes the empty cascade.
