# A shot declared without an attack kind is a shot

[繁體中文](declare_action_kind_default_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `d16bb75`. Found in the 2026-10-12 Camp Sunny rerun8 (turns 77–83).

## Problem

Jonah shot the creature with his .38 Revolver. The Executor called `declare_combat_action` without `action_kind` (the field is optional in the tool schema), and the handler defaulted it to `melee`. A revolver is a `single_shot` weapon, so the engine answered 「Unsupported attack mode」 and paused the fight in NEEDS_RULING. The Executor's attempts to repair it failed in turn (`Unsupported changed attack mode`, then `already completed this turn`), and for seven turns the table read 「這次行動尚未完整處理…處理這個行動的工具失敗了」 and 「Jonah 尚未射出那一槍」.

## Change

When a declaration names no `action_kind`, the handler uses the attack mode of the weapon it resolves (`combat_rules.resolve_weapon`): a firearm is `single_shot`, a fist or knife is `melee`, and an unknown weapon stays `melee` and is still sent for a ruling. An explicit kind is used as given, so a wrong or unsupported one (`burst`) is still a ruling. The tool schema says what the default is.

## Verification

`tests/test_combat_state_machine_integration.py`: a declaration without a kind for a .45 Automatic is a `single_shot` and not NEEDS_RULING; for `unarmed` it is `melee`.
