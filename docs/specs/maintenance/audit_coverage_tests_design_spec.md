# Tests for what the 2026-10-14 audit found uncovered

[繁體中文](audit_coverage_tests_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c6512c4`. Tests only; no runtime change.

The audit found behaviour that is right but pinned by no test:

- `Hatchet`, `Sickle`, `Hatchet/Sickle`, `手斧` and `鐮刀` are one weapon (`i.weapon.hatchet-sickle`) fought with `fighting-axe`; the literal `Club, Large` resolves to the large club, fought with `fighting-brawl` (`tests/test_combat_rules.py`).
- The resolved-check follow-up whitelist offers `sanity_check`, `apply_resolved_check_damage` and `create_triggered_check`, and not `adjust_character`, `skill_check`, `npc_skill_check` or `offer_check_choice` (`tests/test_resolved_check_consequences.py`). Chained checks and non-combat damage after a roll go through the authorised consequence tools, not free-form tools.

(The fallback-opening timeline window has its test in the change that closes it.)
