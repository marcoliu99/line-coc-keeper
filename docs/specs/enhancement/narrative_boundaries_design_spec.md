# Scenario canon boundaries and durable corrections

[繁體中文](narrative_boundaries_design_spec_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `0ae449a` (2026-10-09).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Scenario materials and explicitly established canonical events govern world facts. A player assumption or earlier unsupported AI narration cannot create a room, enemy, clue or significant item.

2. Reasonable mundane possessions are allowed. New content with plot or mechanical impact needs evidence; a retrieval miss means unconfirmed, not nonexistent and not permission to invent.

3. Dedicated /coc correct reports are out of game. Validate their target message against a current-channel/timeline Keeper receipt; player allegations do not automatically become canon or mechanical holds.

4. Keep raw report text out of system prompts. The bounded user-message projection separates pending allegations from approved resolutions and KP-defined hold_scope.

5. Limit pending reports to 3 per reporter and 12 per group. Project at most 6000 JSON characters, prune/archive closed records, and do not silently drop effective approved decisions to fit a budget.

6. Approve/reject/hold/supersede are authorized adjudication operations; reporters may withdraw their own reports. A scoped hold blocks relevant actions, while unrelated actions continue.

7. If authoritative decisions exceed projection capacity, require explicit consolidation rather than silently forgetting canon. Rollback/scenario changes isolate correction timelines.

8. No fixed per-turn LLM reviewer is added. Prompt compliance and semantic completeness are not guaranteed by deterministic state/receipt validation alone.

9. Player-facing narration speaks in the story's voice. Every narrator prompt (`prompt_config.PLAYER_VOICE_RULES`, in `NARRATOR_INSTRUCTION` and both tool-enabled narrator instructions) forbids system words such as 紀錄, 收據, 權威, 狀態, 驗證, 核實 and explanations of why something cannot be written yet: what has not happened is left out, and suspense hands the action back to the player (「Corbitt 的爪子已逼到你面前——你要閃避，還是反擊？」). The pending-check fact block asks for the next roll in that voice instead of 「檢定已建立」. The Haunting runs on 2026-10-09 had told players 「現有紀錄沒有驗證他已起身」 and 「沒有可核實的戰鬥收據」, the guard rules' own wording leaking into the story. The roll instruction the engine appends after narration, and the public line for a triggered check, name the roll instead of saying it was 「已建立」 (「請按檢定按鈕或輸入 /coc check，擲 Evelyn 的偵查。」). Other fixed fallback lines are not narration and are not covered here.

## Flow and interfaces

### Ordinary procurement exception (2026-09-27)

Historical design: the player previously approved ordinary legal purchases based on an established commercial environment without requiring a named shop or itemized scenario catalog. The `purchase_items` settlement and actionable blocker handoff were later reverted with PR #91. Current play uses the existing Keeper affordability and possession guidance; this section no longer specifies an active transaction workflow. See the [rollback design](../bug/revert_pr91_purchase_turn_design_spec.md).

```text
Player action/hypothesis -> scenario evidence -> mechanics -> narration
/coc correct -> verified message receipt -> pending allegation -> KP adjudication -> durable projection
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/keeper.py](../../../app/keeper.py)
- [app/services/narrative_corrections.py](../../../app/services/narrative_corrections.py)
- [app/commands/handlers/correct.py](../../../app/commands/handlers/correct.py)
- [app/agents/tool_gateway.py](../../../app/agents/tool_gateway.py)
- [tests/test_narrative_boundary_prompts.py](../../../tests/test_narrative_boundary_prompts.py)
- [tests/test_narrative_correction_lifecycle.py](../../../tests/test_narrative_correction_lifecycle.py)
- [tests/test_narrative_correction_command.py](../../../tests/test_narrative_correction_command.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/narrative_boundaries_design_spec.md)
