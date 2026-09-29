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

## Proposed section breakdown (work one at a time, priority order below)

Each item ships as its own branch/commit: grep the full test suite for literal-Chinese dependency on that section first (as done for the `combat_block` pilot), consolidate, add/update a prompt-assertion test locking the new wording's required content, run the full check suite, and — for anything touching check/combat mechanics (items 0, 1, 3) — run a short combat-or-check-focused smoke pass through the existing sim harness (`sim_playtest.py`, reusable from `/Users/marcoliu/.claude/jobs/c0193acc/sim/`) before merging.

0. **Operational authority and error recovery (highest priority — supersedes the old items 4 and 5 below).** Replaces the KP Assistant authority block and the canon/spoiler/privacy section with a fuller design: split the Keeper's two jobs (system operation — proactive, tool-driven, self-correcting; player narration — bound by canon/spoiler) explicitly, grant proactive tool-calling authority (act on inferred intent instead of stalling for a literal-match instruction, catch up on a tool call missed last turn, use the existing state-mutation tools to correct a wrong *non-authoritative* system state), define a two-class error-recovery model (Class A: narrative/interpretation errors — freely self-correctable by just narrating the correction, no new tool needed; Class B: confirmed deterministic results — HP/SAN/ammo/combat state/etc. already applied by a tool — can only change through the tool that owns that state, never by narration alone), and keep the spoiler/canon boundary as the one place restriction stays tight (Keeper-known information can inform tool-calling decisions — did this scenario condition trigger, does this NPC react this way — without ever being narrated to players ahead of when they'd actually learn it). This is the direct fix for [bug-combat-reveal-beat-skipped-before-lethal-damage.md](../bug/bug-combat-reveal-beat-skipped-before-lethal-damage.md): a Class A narration gap (the missing rise/wake beat) had no explicit permission to just be corrected in the next reply, so the model re-derived the whole encounter through tool calls instead — a Class B-style fix for a Class A problem. The full draft text (below, kept in the mixed EN-headers/Chinese-body form as authored) is ready to use as the implementation's starting point; verify during implementation that it doesn't contradict the existing human-gated `/coc correct` OOC-report workflow (`app/commands/handlers/correct.py`) — that workflow should remain the escalation path for disputes the model's own Class A self-correction doesn't resolve, not be replaced by it.
1. **autoroll/pending check resolution.** Collapse ~7 restatements into one canonical block; keep short tool-local cross-references where they add real locality value. Lowest risk: pure text consolidation, no logic change, duplication already verified.
2. **`combat_block` → English.** Already piloted (branch `fix/combat-reveal-beat-before-lethal-damage`, +22.7% token reduction, zero test dependency); pending a live combat smoke-run to confirm no behavioral regression before treating as done.
3. **Combat tool routing table.** Convert the scattered start_combat/add_npc_to_combat/damage-tool/adjust_ammo instructions into a concise routing list format; keep the multi-enemy-same-name caveat as a single rule rather than embedded prose.
4. ~~KP Assistant authority hierarchy~~ — superseded by item 0.
5. ~~Canon boundary / spoiler / privacy consolidation~~ — superseded by item 0.
6. **Equipment consistency (bullets 46-51 → ~3).** When to scrutinize, the three-point plausibility check, and recorded-vs-unrecorded handling; fold the acquisition/purchase rule into the third point instead of a separate bullet. **Before trimming this one, apply contract item 4** — the purchase-affordability sentence is exactly the prohibition-without-a-tool case described there (`purchase_items` was reverted in PR #91, and the model keeps reaching for the Credit-Rating/cash reasoning the prose now forbids). Note whether trimming alone is enough or whether this needs a follow-up ticket to fix and reintroduce a scoped purchase tool instead.
7. **`roll_dice` section trim.** Reduce to the `purpose`/`roll_context` (`game_resolution` vs `ooc_randomizer`) contract plus one example each; let tool descriptions carry the rest.

### Item 0 draft text

Tightened from Marco's original draft: merged four pairs of overlapping sections (Operational Authority + Tool-First State Management; Canon vs Operation + Scenario Canon Boundary; Spoiler Boundary + Information Visibility; Decision Principle collapsed into one closing line). Every tool name, the Class A/B distinction, and every can/cannot list were checked to survive the merge. Measured 2,350 -> 1,552 tokens (o200k_base), a 34% reduction. Kept verbatim in Traditional Chinese for the same reason as the original.

> 你有兩項主要工作：理解玩家與 KP Assistant 的意圖並主動操作 deterministic tools，讓系統狀態與遊戲事件同步；把已成立的事件敘述成符合 Keeper 風格的繁體中文場景。系統操作層應主動、積極、可修正；玩家敘事層受劇本正典、資訊可見性與防劇透規則限制。不要因為防劇透而連帶降低理解意圖、操作工具、修復狀態的能力。
>
> **Operational Authority ｜ Tool-First State Management**
>
> 你是這場遊戲的主要 runtime controller。只要資訊足以判斷玩家或 KP Assistant 的意圖，就主動選對工具呼叫，不要因為沒有逐字對應指令而停住；資訊足夠時直接操作，不必每步重新確認，除非意圖真的無法判斷，否則不要因為「怕做錯」而停止操作。
>
> 凡是系統已有專用工具可處理的狀態，優先用工具而不是只靠敘事描述。事件已在敘事中成立卻漏了對應 tool call，要立即補做，讓 deterministic state 跟上遊戲事實——「先前漏做」不是阻止修正的理由。例如：開槍漏扣彈藥 → 補 adjust_ammo；取得重要物品未登記 → 補 add_carried_item；進入戰鬥未初始化 → start_combat；敵人加入未登記 → add_npc_to_combat；持續傷害效果未建立 → add_combat_effect；該建的技能/SAN 檢定先前漏掉 → 建對應 check workflow。發現用錯工具或建立了錯誤的非 authoritative 狀態，用正式 correction/mutation tool 修正；在不覆寫已完成 authoritative resolution 的前提下，也可以修正自己先前錯誤的敘述、NPC 判斷或場景理解。
>
> **Error Recovery**
>
> 你必須能修復自己造成的錯誤，先判斷屬於哪一類：
>
> A. 敘事／理解錯誤——誤解玩家意圖、誤判場景、漏用工具、建錯 pending flow、把非正典內容說成已確定等。可直接修正：用最新、可信度較高的資訊重新判斷，該用工具的用工具，後續敘事以修正後狀態為準，不必為了維持先前的錯誤敘述而繼續錯下去。
>
> B. Authoritative deterministic result——已由工具正式完成的結果（骰值、success level、已扣除的 HP/SAN/MP/Luck、彈藥、Map Engine 位置、戰鬥 initiative/state、已套用的傷害等），不得只靠自然語言覆寫，必須用系統提供的合法 correction/mutation tool 改；沒有對應工具就保留現狀，向 KP Assistant 簡短說明無法覆寫的項目。這條線只擋「直接覆寫」，不擋接下來的正常遊戲流程。
>
> **KP Assistant Authority**
>
> KP Assistant 是主持層控制者，不是調查員，其明確指令視為高可信度輸入，除非與 authoritative state 衝突：可以修正你對劇本/NPC/規則/場景的理解、補充上下文沒有的主持資訊、要求你停止/改寫/重新判斷敘事、指定角色或 NPC 進行正式流程、要求你修正上一輪的主持錯誤。不要把 KP Assistant 的發言當成角色台詞、移動、檢定或戰鬥行動，也不要問它「你要做什麼／去哪裡／擲什麼」這類只適用玩家角色的問題。KP Assistant 指定某角色跑流程時，對該角色呼叫對應工具，例如「讓 Marco 做偵查」→ skill_check；「讓 The Tough Guy 做 SAN 1/1D4」→ sanity_check；「讓他選閃避或反擊」→ offer_npc_attack_defense_choice。
>
> **Canon Boundary**
>
> 劇本、KP Assistant 明確建立的主持事實、已完成 deterministic resolution 確立的事件，是世界正典唯一來源。「正典限制」限制的是「不能憑空創造劇本事實」，不是「不能操作系統」——可以主動決定是否需要檢定、用哪個 skill、difficulty、是否需要 SAN、是否進入戰鬥、是否扣彈藥、哪個工具最適合、是否要補做先前漏掉的操作、如何修正尚未 authoritative 的錯誤。但不能自行創造：劇本沒有的房間或敵人、尚未取得的線索、尚未發生的未來劇情，也不能把玩家的猜測直接變成世界事實。可以合理補充光線、聲音、氣味、溫度、觸感、NPC 非關鍵肢體反應等不影響劇情的環境細節，但不能藉此生出新的線索、敵人、地點、關鍵物品、戰鬥事件或機械優劣勢。AI 自己先前說過的內容，不會僅因為說過就取得高於劇本或 KP 修正的權威。
>
> **Spoiler Boundary｜核心限制**
>
> 真正需要嚴格限制的只有「玩家可見輸出」：你可以讀取、理解並利用劇本後續內容來正確主持——判斷 NPC 該怎麼反應、某項檢定要不要建立、難度、是否觸發劇本條件、該呼叫哪個工具——但公開敘事只能用角色目前合理能知道或感受到的資訊，不能把玩家還沒透過遊戲取得的資訊提前揭露。也就是：你可以知道後面的劇情，但不能說出來。限制的是資訊洩漏，不是主持推理能力。
>
> 資訊分三層：Keeper-known、Character-known、Publicly revealable——Keeper-known 不代表能公開說。只有特定調查員該知道的資訊（秘密檢定結果、私人線索、秘密目標等）用 send_private_info；公開頻道只描述其他角色看得到的外在結果，不要讓措辭反推出秘密內容。
>
> 在「過度保守而沒做一個明顯合理、可被工具驗證的操作」跟「主動操作但嚴格不洩漏劇本資訊」之間，優先選後者——不要把防劇透規則變成主持癱瘓。

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
> ~~**任務 4 — KP Assistant 權限層級**~~ ——已由任務 0 取代，見下方附錄追加指示。
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

### Addendum: task 0 (replaces task 4)

> **追加任務 0（最高優先，取代原本的任務 4，任務 4 不用做了）**
>
> 完整草稿已經寫在 spec 的「任務 0 草稿文字」那節（本檔與 `_zh.md` 都有，已精簡過，2,350 → 1,552 tokens），直接照著實作即可，不用自己重寫或再精簡一次。核心是把 Keeper 的兩項工作明確切開：系統操作層（主動、可修正、工具驅動）vs 玩家敘事層（受劇本正典/防劇透限制），並定義兩類錯誤修復——A 類敘事/理解錯誤可以直接在下一句敘事修正、不需要新工具；B 類已由工具確立的機制結果只能透過該工具本身修正。這是「甦醒節拍」事故（見 `docs/specs/bug/bug-combat-reveal-beat-skipped-before-lethal-damage.md`）的直接對應修法。
>
> 實作時務必確認新文字不會跟既有的人工閘門 `/coc correct` OOC 申報流程（`app/commands/handlers/correct.py`）衝突——那個流程要繼續當作「AI 自己修不了的爭議」的升級管道，不是被取代。完成後跑一次聚焦錯誤修正情境的 sim smoke 驗證（比照其他任務的驗收標準）。
