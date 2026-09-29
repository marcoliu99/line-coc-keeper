# Static prompt consolidation: dedupe, then split narrative/policy language

[繁體中文](enhancement-static-prompt-consolidation_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **partial**. Measured against `main_v2` at `07d55a7` (2026-09-29).

Branch `enhancement/static-prompt-autoroll` completes item 1 only: one canonical player-check policy, short local reminders, and prompt assertions. Items 3, 4, 6, and 7 are independent branches; item 5 remains deferred.

Verification for item 1: the core autoroll block decreased from 531 to 293 o200k_base tokens. A 16-turn real-provider smoke with autoroll off completed 16/16 turns, resolved three player-owned pending follow-ups, and ended with no pending check or Luck decision. The smoke used an isolated scenario copy and database.

This edition describes the current contract. Proposed work is explicitly identified.

## Background

`app/keeper.py`'s `_build_static_prompt` is resent in full on every turn: 9,463 chars / **7,108 tokens** (o200k_base) across 51 top-level bullets, plus dynamic blocks (`combat_block`, `kp_assistant_block`, ...) added on top when active. A real production session's structured log recorded `context_tokens_estimate: 29706` against `context_ceiling: 32000` with `reserve_tokens: 6144`, leaving `budget_tokens: 0` — the static prompt had already crowded out that turn's RAG scenario-retrieval budget entirely. See [bug-combat-reveal-beat-skipped-before-lethal-damage.md](../bug/bug-combat-reveal-beat-skipped-before-lethal-damage.md) for the incident that prompted this investigation.

Two independent lines of reasoning converged on the same fix direction:

1. A verified pilot translated `combat_block` (the active-combat mechanics block, pure tool-calling instruction, no player-facing narration) from Chinese to English: 1,275 → 986 tokens, a 22.7% reduction, with zero existing test dependency on its literal wording (confirmed by grep across the full test suite before translating).
2. An independently-authored consolidation proposal reached the same architectural split by a different route: audit the prompt for literal repetition first, then separate "narrative voice" (must stay Chinese — it directly shapes generated Chinese prose) from "tool-calling / deterministic policy" (model-only, no narration quality reason to be Chinese).

## Current contract

1. Content that shapes generated narration quality (Keeper tone, sensory-detail instructions, pacing, focus-one-character-at-a-time, no-checklist-endings) stays in Traditional Chinese. This is not a token-optimization target — Chinese instruction phrasing measurably steers Chinese output style more directly than an English instruction asking for Chinese output would.

2. Content that is pure tool-calling / deterministic-state policy (which tool to call when, parameter semantics, state-authority rules) is a token-optimization target. English is preferred there once (a) the section has zero existing test dependency on its literal Chinese wording, or that dependency has been migrated alongside it, and (b) a live combat/mechanic smoke run (reusing the existing sim harness) shows no behavioral regression. Literal UI/button strings the system actually renders or matches against (e.g. `「閃避」`, `「反擊」`, `「撲向掩體」`, `「（暫離）」`) stay as literal Chinese inside an otherwise-English instruction, since they are not prose to translate — they are exact strings the system produces or compares against.

3. Duplication is fixed by deleting the redundant restatement, not by deciding rules are unnecessary — the count of *distinct* rules does not go down, only the number of *times* each one is said.

## Verified findings (2026-09-29 investigation)

- **autoroll/pending duplication: confirmed.** The token "autoroll" appears 11 times across at least 7 distinct bullets restating "autoroll off → pending only, autoroll on → engine resolves immediately." At least one restatement is fully redundant with no tool-specific nuance added: "`skill_check`／`sanity_check` 在 autoroll 關閉（預設）時建立玩家擲骰 pending；只有 `autoroll on` 才會立即完成。" Several other restatements are *not* pure duplication — they attach the same rule to a specific tool's own bullet (sanity_check, major-wound CON check, pushed rolls) for locality, which has real value and should be preserved as a short reminder, not necessarily deleted outright. Consolidation here means picking one canonical statement of the rule and trimming the rest to a short cross-reference, not blanket deletion.

- **spoiler/privacy "duplication": not confirmed as originally proposed.** The candidate duplicate bullets (scenario secrecy, private info/secret goals, NPC-ally secrecy, no-metanarration) are themselves the return values of `_spoiler_protection_prompt_rules()` and `_privacy_isolation_prompt_rules()` (`app/keeper.py` ~3191-3260), interpolated into the prompt at single points (~3371, 3405-3406, 3418). No second, independent restatement of the same content was found elsewhere in the static prompt during this investigation. Before touching this section, re-audit with the actual full bullet text (not a truncated preview list) to locate the specific duplicate the original proposal had in mind, or confirm there isn't one and drop this item.

## Proposed section breakdown (work one at a time, lowest risk first)

Each item ships as its own branch/commit: grep the full test suite for literal-Chinese dependency on that section first (as done for the `combat_block` pilot), consolidate, add/update a prompt-assertion test locking the new wording's required content, run the full check suite, and — for anything touching check/combat mechanics (items 1, 3, 4) — run a short combat-or-check-focused smoke pass through the existing sim harness (`sim_playtest.py`, reusable from `/Users/marcoliu/.claude/jobs/c0193acc/sim/`) before merging.

1. **autoroll/pending check resolution.** Collapse ~7 restatements into one canonical block; keep short tool-local cross-references where they add real locality value. Lowest risk: pure text consolidation, no logic change, duplication already verified.
2. **`combat_block` → English.** Already piloted (branch `fix/combat-reveal-beat-before-lethal-damage`, +22.7% token reduction, zero test dependency); pending a live combat smoke-run to confirm no behavioral regression before treating as done.
3. **Combat tool routing table.** Convert the scattered start_combat/add_npc_to_combat/damage-tool/adjust_ammo instructions into a concise routing list format; keep the multi-enemy-same-name caveat as a single rule rather than embedded prose.
4. **KP Assistant authority hierarchy.** Restructure the KP Assistant block into an explicit priority list (engine state > KP corrections > scenario canon > Keeper narrative judgment) with terse examples instead of prose explanation.
5. **Canon boundary / spoiler / privacy consolidation.** Blocked on the re-audit above — locate the actual duplicate (if any) before editing; this section gates spoiler/secret-goal correctness, so treat as higher risk than 1-4.
6. **Equipment consistency (bullets 46-51 → ~3).** When to scrutinize, the three-point plausibility check, and recorded-vs-unrecorded handling; fold the acquisition/purchase rule into the third point instead of a separate bullet.
7. **`roll_dice` section trim.** Reduce to the `purpose`/`roll_context` (`game_resolution` vs `ooc_randomizer`) contract plus one example each; let tool descriptions carry the rest.

## Flow and interfaces

```text
per section: grep test suite for literal-Chinese dependency -> consolidate/translate -> prompt-assertion test -> full checks -> (mechanics-touching sections) sim smoke run -> commit
```

## Implementation and verification

- [app/keeper.py](../../../app/keeper.py)
- Prompt-assertion tests per section (new, one per item above)
- Sim harness for mechanics-touching sections (external to this repo, reusable script)

Related: [bug-combat-reveal-beat-skipped-before-lethal-damage.md](../bug/bug-combat-reveal-beat-skipped-before-lethal-damage.md) (the incident and the RAG-budget-starvation evidence that motivated this investigation); [bug-combat-trigger-prompt-and-damage-tool-ambiguity.md](../bug/bug-combat-trigger-prompt-and-damage-tool-ambiguity.md) (an example of a rule that must stay Chinese — its whole point is Chinese contrastive examples for a Chinese-language judgment call, item 2's exception case).
