# Static prompt consolidation: dedupe, then split narrative/policy language

[繁體中文](enhancement-static-prompt-consolidation_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **backlog**. Measured against `main_v2` at `07d55a7` (2026-09-29).

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

4. Before shrinking or rewording a rule, ask whether it is actually a *prohibition standing in for a missing tool*. A prose "don't do X" rule only reliably suppresses a behavior the model has no better-shaped alternative for; where a properly-scoped tool would channel the same intent correctly, prefer building or fixing that tool over tightening the prose. Concrete evidence: `app/keeper.py`'s equipment-purchase rule was a working `purchase_items` tool as of PR #91 (2026-09-27, `bug/purchase-turn-provenance`), reverted the same day; the prose was then tightened to explicitly forbid the Credit-Rating/cash reasoning the tool used to structure ("不以信用評級、生活水準、價格或現金裁定是否可得"), and the model has continued reaching for that reasoning anyway. Sections in the breakdown below that are pure prohibition with no backing tool (equipment consistency, item 6, is the clearest candidate) should be re-examined for this before being merely trimmed — trimming a rule that isn't working does not fix it, it just makes the failure cheaper.

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
6. **Equipment consistency (bullets 46-51 → ~3).** When to scrutinize, the three-point plausibility check, and recorded-vs-unrecorded handling; fold the acquisition/purchase rule into the third point instead of a separate bullet. **Before trimming this one, apply contract item 4** — the purchase-affordability sentence is exactly the prohibition-without-a-tool case described there (`purchase_items` was reverted in PR #91, and the model keeps reaching for the Credit-Rating/cash reasoning the prose now forbids). Note whether trimming alone is enough or whether this needs a follow-up ticket to fix and reintroduce a scoped purchase tool instead.
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

## Appendix: execution handoff

Item 2 (`combat_block` → English) is done — branch `fix/combat-reveal-beat-before-lethal-damage`, pushed, validated with a live 16-turn combat smoke run (`start_combat`/`add_npc_to_combat`/`advance_combat_turn` all fired correctly). Item 5 (canon/spoiler/privacy) is blocked on the re-audit noted above — do not start it on the assumption the original proposal's duplicate claim is correct.

The handoff text below is kept verbatim in Traditional Chinese, as given to the executor (Codex) for items 1, 3, 4, 6, 7 — it is the literal instruction text used, not prose translated for this document:

> **給 Codex 的任務（依序做，每項獨立分支/commit，只 commit+push 到自己的分支，不要開 PR，做完再一起 review）**
>
> Spec：`docs/specs/enhancement/enhancement-static-prompt-consolidation.md`（繁中版同目錄 `_zh.md`）。背景：`app/keeper.py` 的 `_build_static_prompt` 每回合整包重送，7,108 tokens／51 條規則，真實 log 量到 `budget_tokens:0`（RAG 預算被擠光）。已驗證方向：**敘事語氣規則維持繁中不動**（這塊完全不在任務範圍內），**純工具呼叫／機制政策規則**才是整併/翻英文的目標。`combat_block` 已經是驗證過的範例（分支 `fix/combat-reveal-beat-before-lethal-damage`，已 push），可以參考它的做法：翻譯前先 grep 全部測試套件確認零字面依賴、翻完寫 prompt 斷言測試鎖住必要內容、跑滿測試、有動到機制的話額外跑一次 sim smoke 驗證。
>
> 每項都用 `git worktree add -b <branch> <path> origin/main_v2` 開新分支，不要疊分支。
>
> **任務 1 — autoroll／pending 檢定流程整合（優先做，風險最低）**
> 已證實：「autoroll」在 prompt 裡出現 11 次，至少 7 個不同規則重講「off→pending、on→系統立即代擲」，其中這句完全多餘可直接刪：「`skill_check`／`sanity_check` 在 autoroll 關閉（預設）時建立玩家擲骰 pending；只有 `autoroll on` 才會立即完成。」但不要無差別刪——有幾處是把同一條規則掛在特定工具（sanity_check、重傷 CON 檢定、孤注一擲）旁邊做就近提醒，這個要留，只是壓短成交叉引用，不要整條刪掉。收斂成一個標準版本 + 短提醒。純文字整併，邏輯不變。完成後跑一次聚焦 pending check 的短模擬（sim harness 路徑見下）驗證行為沒退化。
>
> **任務 3 — 戰鬥工具路由表**
> 把散落的 start_combat／add_npc_to_combat／offer_npc_attack_defense_choice／roll_weapon_damage／roll_impaling_damage／apply_combat_damage／apply_final_combat_damage／add_combat_effect／adjust_ammo 指示，改成 concise routing list 格式（可參考 spec 裡英文提案的範例格式）。同種怪物多隻要不同名稱這條保留成單一規則。動到戰鬥機制，完成後要跑戰鬥 smoke 驗證。
>
> **任務 4 — KP Assistant 權限層級**
> 把 KP Assistant 區塊改成明確優先順序清單（engine state > KP 明確更正 > 劇本正典 > Keeper 敘事判斷），範例從長篇中文說明壓成 `"讓 Marco 做偵查" → skill_check(...)` 這種對照格式就好。
>
> **任務 6 — 裝備真實性審查（46-51 → 約 3 條）**
> 何時審查／三項合理性檢查／已登記-未登記物品處理，三條就好，購買規則併進第三條。
>
> **任務 7 — `roll_dice` 段落精簡**
> 收斂成 `purpose`／`roll_context`（`game_resolution` vs `ooc_randomizer`）核心契約 + 各一個範例，其餘交給 tool description。
>
> **任務 5（canon boundary／spoiler／privacy）先不要做**——spec 裡已經記錄查證結果：原提案講的「重複」查無實據（那些規則就是 `_spoiler_protection_prompt_rules()`／`_privacy_isolation_prompt_rules()` 的 dict 值本身，不是額外重複的一份）。要做這項前，先用完整規則原文重新查一次是否真的有別處重複，寫清楚查證結果再決定要不要動，不要照原提案假設直接刪。
>
> **每項驗收標準：** ruff 0.16.8 / mypy / compileall / 全套 pytest 全綠 + 新的 prompt 斷言測試 + （任務 1、3 屬機制類）sim smoke 驗證無退化。Sim harness：`/Users/marcoliu/.claude/jobs/c0193acc/sim/sim_playtest.py <worktree路徑>`，`SIM_TURNS=16` 只跑聚焦編排的戰鬥序列，`.env` 用同目錄下已配置好的（記得複製一份改 `DATA_DIR`/`DB_PATH`/`LOG_FILE` 到獨立子目錄，不要共用原本的資料庫）。
