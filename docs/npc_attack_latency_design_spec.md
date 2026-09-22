# 設計規格：NPC／怪物攻擊回合延遲調查與優化選項

> 適用範圍：`main_v2` 分支（多 agent 架構：`app/agents/supervisor.py` →
> `executor.py`／`narrator.py`，實際工具執行仍在 `app/keeper.py` 的
> `_execute_tool`）。

## 目前檢定所有權（後續修正）

本文件前半段保留當時針對 NPC 攻擊延遲的調查紀錄；目前實作已再收斂檢定流程：

- NPC 攻擊方由 `offer_npc_attack_defense_choice` 立即由系統擲骰，玩家只選閃避／反擊。
- 玩家選定選項後，`/coc check <選項名稱>` 或 Discord 按鈕只提交選擇，系統再替防守方擲骰並完成對抗判定。
- 一般技能、攻擊、SAN 與重傷 CON 檢定都由 Keeper 的 deterministic tool 立即擲骰；不要求玩家輸入 `/coc check` 來手動擲骰。
- `pending_checks` 只保留互斥選項與舊版本快照相容性；Luck 仍是看到系統結果後由玩家選擇是否花費。

詳細規格與狀態冪等策略見 `docs/keeper-deterministic-check-resolution_design_spec.md`。

## 目標

調查「每次到怪物（NPC）攻擊時，Keeper 回應總是特別久」這個現象的根因，並列出
可行的優化方向。這份文件目前只到「調查＋選項」，還沒有定案要做哪個，等 review
後再決定範圍、動手實作。

## 歷史現況（檢定所有權修正前的根因）

### 1. NPC 攻擊是強制的多步驟序列式工具呼叫鏈，每一步都要重新問一次 LLM

`app/keeper.py:1943-1948`（combat_block 的「敵人回合規則」）明確規定敵人攻擊
的標準流程：

```
輪到敵方戰鬥卡時，必須先呼叫 plan_enemy_turn。工具會檢查特殊能力、觸發條件、
每輪/每戰使用次數、冷卻與可用攻擊...照 plan 的 selected_action 處理，若是
attack，將正式命中結果與傷害值放入 outcome，再呼叫 resolve_enemy_action
統一套用護甲與 HP 變更。
```

而當攻擊目標是玩家角色、玩家需要在「閃避」跟「反擊」之間選一個時
（`app/keeper.py:1937-1941`），`offer_check_choice` 這個工具的 schema
（`app/keeper.py:192-200`）明講：

```
attacker_tier：這是防守方對抗攻擊的選擇（閃避／反擊）時才填：攻擊方這次攻擊的
成功等級（先呼叫 npc_skill_check 幫攻擊方擲出來，不要自己編）。
```

也就是說，`offer_check_choice` 的 `attacker_tier` 欄位**結構性地依賴**
`npc_skill_check` 先回傳的結果——AI 沒辦法把這兩個工具塞進同一次回應裡平行呼叫，
必須先看到 `npc_skill_check` 的回傳值，才能決定 `offer_check_choice` 要填什麼。

這代表「NPC 攻擊玩家、玩家要選閃避或反擊」這個最常見的戰鬥情境，最少需要：

1. `plan_enemy_turn`（決定敵人這輪要做什麼）→ 需要一次 LLM 呼叫才能決定要呼叫這個工具，工具本身是純 Python 計算（`app/combat.py:693`），瞬間執行完
2. `npc_skill_check`（擲攻擊方的檢定）→ 又要一次 LLM 呼叫才能看到上一步結果、決定呼叫這個
3. `offer_check_choice`（把選項＋`attacker_tier` 一起丟給玩家）→ 又要一次 LLM 呼叫
4. 最後才產生公開敘事文字（沒有工具呼叫的最後一輪）

**總共 4 次 LLM API 呼叫**（每次都是一次 `client.messages.create`／
`client.responses.create`），不是 1 次。`MAX_TOOL_ITERATIONS=8`
（`app/config.py:107`）給的餘裕綽綽有餘，不是卡在疊代上限，是每一步本身就要
真的付一次 API round-trip 的代價。

用先前已經在 review 過程中看到的真實 log 估算：一次 `responses.create`
呼叫平均落在 2-5 秒（視 prompt 大小、tool 數量而定），4 次疊起來輕鬆
**8-20 秒**——這就是「特別久」的直接原因。`plan_enemy_turn`／
`npc_skill_check`／`resolve_enemy_action` 這三個工具本身都是純 Python
（`app/combat.py`），沒有額外外部呼叫成本，延遲完全來自「AI 每決定下一步
都要重新問一次模型」這個 agentic tool-calling 架構的固有代價，不是工具太慢。

### 2. 疊加成本：不論是不是戰鬥中，每回合都會先跑一次主動 RAG

`app/agents/context_builder.py`（PR #39／#40 review 時已經確認過的行為）
不管這句話是不是跟戰鬥有關，只要玩家有綁定角色就會主動打 memory RAG
的 embedding API（`SCENARIO_RAG_ENABLED` 開啟時還會多打一次 scenario RAG），
這一步是在 `intent_router.classify_intent` **之前**就跑的（見
`docs/dialogue_batching_design_spec.md` 同一輪 review 的分析）。戰鬥回合
一樣要付這筆帳，即使戰鬥中的敘事通常高度依賴 `combat_block`
提供的機制狀態、對劇本 RAG／記憶 RAG 的需求本來就比一般探索回合低。

這一步（已經有 embedding cache，PR #40 merge 後）目前每次還是至少要打一次
embeddings API（cache 沒命中時），大約再疊加 2-5 秒。

### 3.（附帶發現，與延遲無關）`_execute_tool` 裡有一段死碼

審查這次的 `plan_enemy_turn`／`resolve_enemy_action` 呼叫路徑時，發現
`app/keeper.py` 裡 `if name == "plan_enemy_turn":`／
`if name == "resolve_enemy_action":`／`if name == "apply_combat_damage":`
這三個分支**各自出現兩次**（約 1481-1493 行是第一份，1524-1532 行是第二份）。
因為每個分支命中就直接 `return`，第二份永遠執行不到，是死碼。而且死碼那份
`resolve_enemy_action`（1529-1532 行）**沒有**傳 `outcome=tool_input.get
("outcome")`，跟第一份（1486-1493 行）不一致——證實是舊版沒清乾淨的殘留，不是
刻意的分支邏輯。這跟本次延遲調查無關，純屬程式碼品質問題，建議另外開一張票
清掉，不在這次範圍內處理。

## 優化選項（尚未決定，列出取捨讓你選）

### 選項 A：合併 `npc_skill_check` 與 `offer_check_choice`

新增一個專用工具（例如 `offer_defense_choice`），把「幫攻擊方擲檢定」跟
「把防守選項丟給玩家」合成一次呼叫——工具內部自己呼叫
`combat.npc_skill_check`（或等效邏輯）算出 `attacker_tier`，再直接組出
`offer_check_choice` 原本的回傳格式，AI 只需要呼叫這一個工具。

- **好處**：把「NPC 攻擊、玩家選防守方式」這個最常見情境從 4 次 LLM 呼叫
  砍到 3 次，少一輪 round-trip（省 2-5 秒）。
- **代價**：
  - 要新增工具 schema、改 `_execute_tool` 邏輯、改 prompt 指示（`combat_block`
    那段敘事規則要重寫，不能再要求 AI 分兩步做）。
  - `npc_skill_check` 目前的 description 寫著「也可以用在任何劇本需要 NPC
    自己做一次檢定的場合」（`app/keeper.py:210-211`）——不是只有防守選擇這一種
    用法，合併時要保留獨立呼叫 `npc_skill_check` 的路徑給那些其他場合，不能
    直接砍掉。
  - 這是這次三個選項裡改動範圍最大的一個，需要照「新功能」的完整流程走
    （spec 確認後才實作、要有測試）。

### 選項 B：戰鬥中跳過 context_builder 的主動 RAG

`state.combat.active` 為真時，讓 `context_builder.py` 的
`_run_scenario_rag`／`_run_memory_rag`（或至少後者，因為它不看
`SCENARIO_RAG_ENABLED`、幾乎每回合都跑）直接跳過，不主動打 embedding API。

- **好處**：獨立於選項 A，風險低、改動小（一行條件判斷），可以穩定省掉戰鬥
  回合裡 2-5 秒的 embedding 延遲，跟選項 A 疊加使用。
- **代價**：如果戰鬥中玩家真的問了劇本相關的問題（例如「這個房間有沒有可以
  丟的東西」），會少了 RAG 輔助──但 `combat_block` 已經把機制狀態、先攻順位、
  敵人資訊都放進 prompt 了，戰鬥敘事對 RAG 的依賴本來就比較低，這個取捨風險
  應該不高，但需要你確認可以接受。

### 選項 C：清掉附帶發現的死碼

跟延遲無關，見上方「現況」第 3 點。建議另開一張小票單獨處理，不跟這次的延遲
優化綁在一起。

## 建議

選項 A、B 彼此獨立，可以只做一個、也可以兩個一起做：

- 只想要最低風險、馬上見效：先做 **B**。
- 想要最大幅度改善「NPC 攻擊、玩家選防守方式」這個最常見的慢情境：**A**
  是真正砍掉一次 round-trip 的關鍵，但改動範圍較大，需要照新功能流程走
  （這份 spec 確認後、再寫測試、再實作）。
- **C** 建議另開票，不影響這次的範圍。

兩個一起做，粗估一次 NPC 攻擊判定可以從現在的 8-20 秒縮短到
**5-13 秒**左右（B 省 2-5 秒，A 省一次 round-trip 約 2-5 秒）。

你想先做哪個、還是兩個一起做？

## 定案設計（Marco 已選：A + B 一起做，C 另開規格書單獨處理）

### A 的具體設計：新增 `offer_npc_attack_defense_choice` 工具

不改動既有的 `offer_check_choice`／`npc_skill_check`（兩者在非「NPC 攻擊、玩家選防守
方式」的其他場合還是要獨立能用——`npc_skill_check` 的 description 本來就寫明「也可以
用在任何劇本需要 NPC 自己做一次檢定的場合」），而是新增一個專用工具，把這兩者在「防守
選擇」這個特定情境下的組合行為直接融合成一次呼叫：

```python
{
    "name": "offer_npc_attack_defense_choice",
    "description": (
        "『請求』一次「被 NPC 攻擊時的防守選擇」——COC7e 近戰對抗檢定的完整標準流程：閃避跟"
        "反擊只能選一個。這個工具會直接由程式碼擲出攻擊方（NPC/怪物）這次攻擊的成功等級，"
        "不用你自己先呼叫 npc_skill_check、也不用自己編。跟 offer_check_choice 一樣不會幫"
        "玩家骰防守方的骰子，只記錄下選項清單，讓玩家自己選一個、用 /coc check <選項名稱> "
        "擲骰。呼叫完之後只能敘述『被攻擊、需要在這幾個選項裡選一個』的當下場景，不能自己"
        "選、不能自己編結果、不能自己講攻擊有沒有命中——玩家真的擲完骰後系統會自動判定。"
        "如果只是一般多選一（不是被攻擊的防守情境），改用 offer_check_choice。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "investigator": {"type": "string", "description": "調查員角色名稱"},
            "options": {
                "type": "array", "minItems": 2, "description": "至少兩個互斥選項",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"}, "skill": {"type": "string"},
                        "bonus_dice": {"type": "integer"}, "penalty_dice": {"type": "integer"},
                    },
                    "required": ["label", "skill"],
                },
            },
            "attacker_skill_value": {"type": "integer", "description": "攻擊方（NPC）這次攻擊技能的百分比值"},
            "attacker_bonus_dice": {"type": "integer", "description": "攻擊方獎勵骰數量，預設 0"},
            "attacker_penalty_dice": {"type": "integer", "description": "攻擊方懲罰骰數量，預設 0"},
        },
        "required": ["investigator", "options", "attacker_skill_value"],
    },
}
```

`_execute_tool` 的 handler 直接組合既有兩個 handler 已經在用的既有函式（`dice.skill_check`、
`resolve_skill_value`、`find_character`／`require_character`、`_mutate_and_save_state`）——
不是重新發明邏輯，只是把「擲攻擊方檢定」跟「登記防守方 pending choice」這兩步在同一次
`_execute_tool` 呼叫裡做完：

```python
if name == "offer_npc_attack_defense_choice":
    char = find_character(state, tool_input.get("investigator", ""))
    if not char:
        return {"ok": False, "error": f"找不到角色「{tool_input.get('investigator')}」"}
    raw_options = tool_input.get("options") or []
    if len(raw_options) < 2:
        return {"ok": False, "error": "options 至少要給兩個選項，只有一個的話請直接用 skill_check"}
    attacker_skill_value = max(0, min(100, int(tool_input["attacker_skill_value"])))
    attacker_bonus = int(tool_input.get("attacker_bonus_dice") or 0)
    attacker_penalty = int(tool_input.get("attacker_penalty_dice") or 0)
    npc_roll = dice.skill_check(attacker_skill_value, bonus_dice=attacker_bonus, penalty_dice=attacker_penalty)

    def _register_pending_choice(target_state: GroupState) -> list[dict]:
        target_char = require_character(target_state, tool_input.get("investigator", ""))
        options = []
        for opt in raw_options:
            value = resolve_skill_value(target_char, opt["skill"])
            options.append({
                "label": opt["label"], "skill": opt["skill"], "skill_value": value,
                "bonus_dice": int(opt.get("bonus_dice") or 0), "penalty_dice": int(opt.get("penalty_dice") or 0),
            })
        pending_choice = {"type": "choice", "options": options, "attacker_tier": npc_roll.tier}
        target_state.pending_checks[target_char.owner_id] = pending_choice
        return options

    options = _mutate_and_save_state(state, _register_pending_choice)
    refreshed_char = require_character(state, tool_input.get("investigator", ""))
    return {
        "ok": True, "pending": True, "investigator": refreshed_char.name, "options": options,
        "attacker_roll": npc_roll.roll, "attacker_tier": npc_roll.tier,
        "note": "攻擊方檢定已經由系統擲好（tier 見上面），還沒有防守方的骰出結果——等玩家自己選"
                "一個選項、用 /coc check <選項名稱> 擲骰後才會有結果，不要自己選、不要自己編一個、"
                "也不要自己判定命中與否。",
    }
```

`combat_block` 的敘事規則同步改寫，指示 AI 改呼叫這個新工具、不用再分兩步：把原本
「先呼叫 npc_skill_check...填進 offer_check_choice 的 attacker_tier」那段改成
「呼叫 offer_npc_attack_defense_choice，工具會直接擲好攻擊方結果，不用你自己先呼叫
npc_skill_check」。

新工具要加進 `_KP_ASSISTANT_ALLOWED_TOOL_NAMES`（`offer_check_choice`／
`npc_skill_check` 都已經在裡面，比照辦理）。

**這個改動不影響「NPC 自己做檢定但不是防守情境」的其他場合**——那些場合繼續用既有的
`npc_skill_check`，這個工具完全沒被動到。

### B 的具體設計：戰鬥中跳過 context_builder 的主動 RAG

`app/agents/context_builder.py` 的 `_run_scenario_rag`／`_run_memory_rag` 觸發條件
各自加上 `not state.combat.active`：

```python
rag_task = None
if SCENARIO_RAG_ENABLED and state.scenario_text and state.scenario_title and not state.combat.active:
    ...

memory_task = None
if char and not state.combat.active:
    ...
```

`rag_context`／`memory_context` 本來就有 `""` 的預設初始值（沒有 task 時维持空字串），
下游 `narrator.py`／`executor.py` 不需要改動——這兩個 consumer 已經是「拿到什麼就用什麼，
空字串就是沒有 RAG context」的既有邏輯。

## 測試計畫

- `offer_npc_attack_defense_choice`：
  - 正常情境：mock `dice.skill_check` 回傳固定 `roll`/`tier`，驗證 `pending_checks`
    正確登記（含 `attacker_tier`），回傳值包含 `attacker_roll`/`attacker_tier`。
  - 角色不存在、`options` 少於兩個 → 回傳既有的錯誤訊息格式，跟 `offer_check_choice`
    的既有行為一致（regression 對照）。
  - `offer_check_choice`／`npc_skill_check` 原本的行為逐字元不變（regression）。
- `context_builder.build_context`：
  - `state.combat.active = True` 時，`scenario_rag.search`／`memory_rag.search_memory`
    都不會被呼叫（不論 `SCENARIO_RAG_ENABLED` 開關）。
  - `state.combat.active = False` 時，行為與現行完全一致（regression，沿用
    `tests/test_agentic_pipeline.py` 既有的 `ContextBuilderScenarioRagGatingTests`
    測試慣例）。

## 實作後自我 review 的追加發現：`plan_enemy_turn`／`resolve_enemy_action` 跟
防守選擇規則之間，prompt 原本沒有接起來

寫完 A 的實作後，通讀整條戰鬥流程的程式碼（`app/combat.py` 的
`plan_enemy_turn`／`resolve_enemy_action`）才發現：`combat_block` 裡「玩家角色
在近戰中被攻擊時」（用 `offer_npc_attack_defense_choice`）跟「敵人回合規則」
（`plan_enemy_turn` → `resolve_enemy_action`）這兩段規則，原本的 prompt**沒有
講清楚什麼情況該用哪一段**：

- `resolve_enemy_action` 的 `attack` 分支（`app/combat.py:924-951`）會直接呼叫
  `apply_combat_damage` 套用傷害，前提是**AI 自己**判定 `outcome` 裡的「正式命中
  結果」——但如果攻擊目標是玩家角色，COC7e 規則要求玩家自己選閃避或反擊，不能讓
  AI 自己判定命中，這正是 `offer_npc_attack_defense_choice`／舊版
  `offer_check_choice` 存在的理由。
- 換句話說，`plan_enemy_turn` 選出 `attack` 之後，實際上要看**目標是不是玩家角色**
  分岔：目標是玩家 → 改用防守選擇規則（`offer_npc_attack_defense_choice`），
  不能走 `resolve_enemy_action`；目標不是玩家（例如敵方陣營內鬥）→ 才由 AI 自己
  判定命中、走 `resolve_enemy_action`。這個分岔邏輯原本完全沒寫進 prompt——不是
  這次改動造成的（舊版 `npc_skill_check`／`offer_check_choice` 兩段規則一樣
  各自獨立寫，沒接起來），但既然這次正好在改同一段文字，一併補上。
- 順便發現 `plan_enemy_turn` 的 `attack` plan 已經在 `required_rolls[0]
  .skill_value` 裡回傳攻擊方的技能值（`app/combat.py:777-782`）——AI 呼叫
  `offer_npc_attack_defense_choice` 的 `attacker_skill_value` 時可以直接拿這個
  值用，不用另外想辦法取得，這點原本 prompt 也沒講。

已經在同一個 commit 裡把 `combat_block` 的「敵人回合規則」段落補上這段分岔說明
（見 `app/keeper.py` 的 combat_block），不算額外的程式碼改動、只是把既有兩段
規則之間缺的一句連接文字補齊，讓 AI 更可靠地在該用新工具的情境下真的用上，
不會因為看不懂該走哪條規則而退回舊的、多一輪的判斷方式。

## 追加 review：有沒有「重複呼叫」風險（不是延遲問題，是正確性問題）

Marco 要求再仔細看一次有沒有遞迴性/重複性的呼叫問題。系統性檢查了戰鬥／檢定
相關的每個工具，結論：沒有找到真正的 Python 遞迴（函式呼叫自己導致的無限迴圈
或 stack overflow），但找到兩個**程式碼層級沒有保護、完全依賴 prompt 紀律**
的重複呼叫風險——都是既有架構的既有模式，不是這次改動造成的，但這次新增的
`offer_npc_attack_defense_choice` 也繼承了同樣的模式，一併記錄。

### 風險 1：建立 pending check 的工具沒有一致的重複請求處理

`skill_check`（`app/keeper.py:1178`）、`sanity_check`（`:1292`）、
`offer_check_choice`（`:161`）、新的 `offer_npc_attack_defense_choice`
——這四個工具都必須在寫入前檢查這個 owner_id 是否已經有一筆待處理的檢定。
如果 AI 對同一個角色重複呼叫，不能靜默覆蓋第一次，避免玩家原本要處理的
`skill`／`difficulty`／`options` 等上下文消失。

對 `offer_npc_attack_defense_choice` 來說風險更具體：這個工具會**先擲一次
攻擊方的骰子**才登記 pending check，重複呼叫代表**白擲一次骰、結果被覆蓋丟棄
**——玩家永遠不會看到那個被丟掉的攻擊方骰出結果，如果 KP 或玩家事後回頭核對
擲骰紀錄會對不上。

**已修正並實作**：`skill_check` 與 `offer_check_choice` 會先建立 canonical
pending request；若現有 entry 完全相同，回傳既有結果並以 `should_save=False`
保持 idempotent；若不同則拒絕。`sanity_check` 與
`offer_npc_attack_defense_choice` 則在任何 state mutation／攻擊方擲骰前，對
fresh `target_state` 使用 `_reject_if_check_already_pending`，已有 pending 就拒絕。
這兩種策略都保證不覆蓋原 entry；其中 NPC 攻擊防守流程不能把第二次呼叫當作
相同請求重用，因為它涉及攻擊方骰子的生命週期，必須先明確清除舊 pending 才能
重新開始。

測試見 `tests/test_npc_attack_latency.py` 的 `AlreadyPendingCheckTests`：涵蓋
`skill_check`／`offer_check_choice` 的相同請求 idempotency、不同請求拒絕，以及
`sanity_check`／`offer_npc_attack_defense_choice` 的重複拒絕與「第二次呼叫不會
真的擲骰」驗證（用 mock 計算 `dice.skill_check` 實際被呼叫幾次、帶了什麼參數）。
另有「不同角色互不影響」的情境確認這個保護是以 `owner_id` 為單位、不是全域擋住。

### 風險 2：`plan_enemy_turn` 重複規劃——**深入查證後發現原本的假設是錯的，沒有修**

原本以為「同一敵人、同一輪、同一個 `current_index` 的 plan 一旦被
`resolve_enemy_action` 標記 `resolved=True`，再呼叫一次 `plan_enemy_turn`
就是不該發生的重複規劃」，打算比照風險 1 加一個「已解決就拒絕」的守衛。實作後
拿既有測試套件跑一次，`tests/test_combat_cards.py` 的
`test_on_damage_taken_trigger_fires_after_enemy_damage` 直接失敗，回頭看這個
既有測試才發現原本的假設是錯的：

```python
before_damage = combat.plan_enemy_turn(state)                       # → attack
combat.apply_combat_damage(state, "Spiteful Thing", 1)
after_damage = combat.plan_enemy_turn(state)                        # → special_ability（觸發 on_damage_taken）
combat.resolve_enemy_action(state, after_damage["plan_id"])
after_resolve = combat.plan_enemy_turn(state)                       # → attack（！）
```

這個既有測試證實：**同一個敵人在同一輪、同一個 `current_index` 裡，本來就可以
合法地被規劃並解決「多個」動作**——受傷觸發的特殊能力先解決一次，接著同一個
敵人還能再規劃一次「攻擊」動作，兩個都算在同一個 `advance_combat_turn` 之前。
`resolve_enemy_action` 的 `resolved=True` 代表的是「這一個 plan 處理完了」，
不是「這個敵人這一輪的行動額度用完了」——`advance_combat_turn` 才是真正決定
「輪到下一位」的唯一機制，這是刻意的設計（觸發性特殊能力可以跟主要行動疊加），
不是漏洞。

已經把原本加的守衛程式碼**完全撤掉**（`app/combat.py` 的 `plan_enemy_turn`
還原成跟這個 spec 一開始讀到的版本一樣），改記錄這個修正過的理解，避免以後
又基於同樣的錯誤假設重新加一次這個守衛。`already_resolved_plan` 這種「同一
`combatant_id`／`round_number`／`current_index` 是否已有 resolved plan」的
查詢，本身不是一個能區分「合法的第二個動作」跟「AI 誤觸發重複規劃」的可靠訊號，
沒有找到其他更精確的區分方式，這次先不處理——如果之後真的觀察到 AI 誤用
`plan_enemy_turn` 造成不合理的重複攻擊，需要重新想一個更精確的判斷條件（例如
看 `selected_action`／`selected_id` 是否重複、或限制「同一個 `plan_id` 觸發鏈」
之類），不是這次能直接套用的簡單版本。

### 已排除、不是問題的情況

- `combat.process_timing`（`:531`）本身有 `processed_timings` 集合去重
  （`key in state.combat.processed_timings: return []`），重複呼叫是安全的
  no-op，不會重複套用同一個 timing 效果。
- 整個 `app/keeper.py`／`app/combat.py` 的戰鬥與檢定相關程式碼裡，沒有找到
  函式呼叫自己（直接或間接）導致的真正遞迴。
- `MAX_TOOL_ITERATIONS = 8`（`app/config.py:107`）限制了單一 Keeper 回合內
  工具呼叫鏈的最大長度，就算 AI 真的陷入某種重複呼叫的困惑，最多執行 8 輪就會
  停止、回傳當下的敘事文字——這只是縮小炸裂半徑（AI 不會無限迴圈下去），不是
  解決上面兩個風險本身，兩個風險造成的資料覆蓋／重複規劃在 8 輪內一樣可能發生。
- 本次新增的 retry 機制（`app/providers/retry.py`，PR #38）不會造成工具被
  重複執行：重試只發生在「跟模型要下一步回應」這個 HTTP 呼叫本身失敗的時候，
  工具的實際執行（`execute_turn_tool`）在這之前就已經完成、結果已經寫進
  對話歷史，重試不會回頭重新觸發已經執行過的工具。

### 結論

風險 1 已修正並測試（`_reject_if_check_already_pending`，四個工具共用）。
風險 2 深入查證後發現原本的假設不成立，既有測試證實同一敵人同一輪合法地可以
被規劃／解決多個動作，已撤掉原本打算加的守衛，只留下修正後的理解記錄，沒有
程式碼改動。全套測試 249/251 過（2 個既有無關失敗）。

## 開 PR 後的審查與修正

開 PR #43 後，GitHub Codex review 跟另外派的一個獨立 code review agent 都審查
過這份改動，以下記錄發現跟處理結果：

### 已修正：`offer_npc_attack_defense_choice` 沒有依攻擊距離判斷能不能「反擊」

Codex（P2）指出：`plan_enemy_turn` 選到 `near`／`any`（遠程）攻擊時（`app/
combat.py:755-760` 本來就接受這三種 range_band），combat_block prompt 卻只
看 `target_ids` 的 `pc:` 前綴就無條件呼叫 `offer_npc_attack_defense_choice`
給「閃避」「反擊」兩個選項——COC7e 規則反擊只在近戰才合法，遠程攻擊被誤套用
近戰對抗語意。

根因是 `plan_enemy_turn` 的 attack plan 的 `required_rolls[0]` 本來就沒有帶
`range_band`，模型完全看不到這次攻擊是近戰還是遠程，加上工具 schema
`options` 原本 `minItems: 2` 強制至少兩個選項，結構上就不可能只給「閃避」。

修正：
- `app/combat.py` 的 attack plan `required_rolls[0]` 加上 `range_band`
  欄位（直接讀 `attack.range_band`，不是新邏輯）。
- `offer_npc_attack_defense_choice` 的 `options` schema `minItems` 從 2 改
  成 1，並在 description／combat_block prompt 明確說明：`engaged` 給
  「閃避」「反擊」兩個選項；`near`/`any` 只給「閃避」一個選項，不要湊。
- `_execute_tool` 的選項數檢查同步從 `< 2` 改成 `< 1`（只擋真的完全沒給
  選項的情況）。
- `_KP_ASSISTANT_PROMPT` 裡一段還在教模型用舊的 `npc_skill_check`+
  `offer_check_choice` 兩步流程的範例句子（Codex 另一個 P2 發現），也一併
  改成教模型用新的合併工具。
- 新增測試：`tests/test_npc_attack_latency.py::
  test_single_option_is_accepted_for_ranged_attacks`（單選項合法）、
  `tests/test_combat_cards.py::
  test_planned_attack_exposes_range_band_for_defense_gating`（確認
  `range_band` 真的被帶出來）。

#### `range_band` 到底怎麼判斷近戰還是遠程？

不是即時算出來的，是「攻擊這個動作本身的靜態屬性」，在敵人被建立的當下就決定
好了，跟戰鬥當時雙方實際距離無關（雙方實際距離另外用
`state.combat.range_bands` 追蹤，只用來決定這個攻擊選項「打不打得到這次的
目標」，不影響防守要不要給反擊——這是兩件事）：

1. **設定時機**：Keeper 呼叫 `add_npc_to_combat` 建立敵人戰鬥卡時，
   `attacks` 陣列每一筆可以帶一個 `range_band` 欄位（`app/models.py:428`
   的 `AttackRule.range_band`）。這個值從此就固定在這個攻擊定義上，直到
   戰鬥結束都不會變。
2. **沒填的話**：預設是 `"engaged"`（近戰）——`AttackRule` dataclass 的
   欄位預設值就是這樣。
3. **`plan_enemy_turn` 只是照抄**：輪到這個敵人時，`plan_enemy_turn`
   （`app/combat.py:755-782`）從敵人卡的 `attacks` 裡選一個「打得到目標」
   的攻擊（用 `_attack_can_reach_target` 比對這個攻擊的 `range_band` 跟
   `state.combat.range_bands` 記錄的實際距離），選到後把這個攻擊已經定死
   的 `range_band` 原封不動放進 `required_rolls[0].range_band`，回傳給
   模型。**沒有任何地方在這個時間點「判斷」這次攻擊是不是近戰**——判斷早在
   步驟 1 敵人被建立時就做完了，`plan_enemy_turn` 純粹是把那個決定傳下去。
4. **combat_block prompt 純粹讀值**：模型看到 `required_rolls[0]
   .range_band == "engaged"` 才給反擊選項，`near`/`any` 只給閃避——它不
   需要（也不應該）自己重新判斷攻擊距離，那個判斷已經在步驟 1 做完了。

**這個鏈條上原本有一個沒堵住的洞**：`add_npc_to_combat` 工具 schema 對
`attacks.range_band` 完全沒有任何說明（只列了欄位名字），模型在建立一個
拿槍的 NPC 時完全沒有被提醒要填 `range_band: "near"`——如果漏填，就會照
預設值變成 `"engaged"`，等於這次遠程攻擊的修正在源頭就被繞過了，即使
`plan_enemy_turn`／`offer_npc_attack_defense_choice` 的邏輯完全正確也沒用。
這個洞已經補上：`app/keeper.py` 的 `add_npc_to_combat` schema 裡
`attacks` 欄位的 description 現在明確列出三個值該怎麼用——`engaged`
（近戰，可以不寫，預設值）、`near`（有距離的攻擊：槍械／弓箭／投擲武器，
**務必明確填**，漏填會被當成近戰）、`any`（不受距離限制：法術／詛咒／
心靈攻擊）。

補充：目前 `plan_enemy_turn` 選攻擊的篩選條件（`app/combat.py:757-759`）
只接受 `range_band` 是 `engaged`/`near`/`any` 的攻擊，`far` 雖然是合法
的資料值（`AttackRule`／`_range_rank` 都認得），但永遠不會被選中當作這次
要用的攻擊——這是既有行為，這次沒有改動，只是記錄下來：建立遠程攻擊時應該
用 `near`，不要用 `far`。

新增測試鎖定這個預設行為：`tests/test_combat_cards.py::
test_attack_without_explicit_range_band_defaults_to_melee`（沒填
`range_band` 時 `plan_enemy_turn` 回傳的還是 `"engaged"`，符合 schema
description 現在承諾的行為）。

#### 歷史調查：玩家打 NPC 這條路徑上的重複呼叫／pending check 覆蓋檢查

以下是檢定所有權改成 Keeper 代擲前的調查紀錄；目前行為以本文件上方的
「目前檢定所有權（後續修正）」及 deterministic check spec 為準。

Marco 追問「玩家打 NPC、Keeper 自己敘事判斷」這條路徑有沒有查過重複呼叫／
lock 問題——這條路徑（`skill_check` 玩家自己的攻擊擲骰、`roll_weapon_damage`／
`roll_impaling_damage` 算傷害、`apply_combat_damage`／`damage_combatant`
套用傷害）之前沒有被系統性檢查過，補查了一次：

- `roll_weapon_damage`／`roll_impaling_damage`：純函式，不寫 state、不用鎖，
  重複呼叫最多是白算一次數字，沒有正確性風險。
- `skill_check`（玩家自己出手的攻擊擲骰請求）：已經在風險 1 的
  `_reject_if_check_already_pending` 保護範圍內，跟 NPC 打玩家共用同一套
  守衛。
- `apply_combat_damage`／`damage_combatant`：跟風險 2 同一類，本來就該被
  呼叫很多次（一場戰鬥每次命中各呼叫一次），沒辦法用「擋重複呼叫」的方式
  保護，也不需要——鎖沒有巢狀問題，各自獨立走 `_mutate_and_save_state`
  （單一 `get_state_lock`），`_execute_tool` 同一回合內單執行緒依序執行。

**查的過程中發現一個真實的漏洞，已修正**：`apply_combat_damage`
（`app/combat.py`）內部呼叫的 `_register_major_wound_check`（COC7e 重傷
規則，傷害達最大 HP 一半時觸發 CON 檢定）直接
`state.pending_checks[pc.owner_id] = {...}`，完全繞過風險 1 加在
`skill_check`／`sanity_check`／`offer_check_choice`／
`offer_npc_attack_defense_choice` 這四個「正門」工具上的
`_reject_if_check_already_pending` 保護。如果玩家當下剛好已經有一筆待處理
的檢定（例如另一次 NPC 攻擊的防守選擇還沒解決），這次重傷觸發會把它靜默
覆蓋掉——玩家原本要處理的那筆檢定連同上下文一起消失，沒有任何錯誤或警告。
範圍很窄（要同時撞上「已有 pending check」+「這次傷害達重傷門檻」），但
確實是個沒被防到的洞，不是這次新增的邏輯，是既有 `apply_combat_damage`
的既有行為，只是剛好跟這次加的 pending check 保護機制打架。

修正：`_register_major_wound_check` 寫入前先檢查 `pc.owner_id` 是否已經有
pending check，有的話直接跳過（回傳 `False`，不觸發、不覆蓋），等玩家先
處理完手上那筆再說；傷害本身照常套用，只跳過「順便登記重傷檢定」這個
次要的 side effect。新增測試
`tests/test_combat_cards.py::
test_apply_combat_damage_does_not_clobber_an_existing_pending_check`。

### 後續全部補上：獨立 review agent 的其他發現（Marco 要求「之前有查到的都幫我修掉」）

上面幾個原本記錄「暫不處理」的項目，除了 ally/enemy 分支那個需要 Marco 判斷
未來設計意圖的問題，其餘全部補上了：

- **`_reject_if_check_already_pending` 跟 `/coc check` 解析用不同的鎖，已
  修正**：原本的守衛檢查對呼叫方傳入的外層 `state`（可能是這個 Keeper 回合
  開始時的舊快照）直接判斷，跟玩家解 `/coc check` 用的鎖（`get_state_lock`）
  不是同一個保護範圍，理論上有個很窄的競態窗口會誤判「還有待處理的檢定」而
  拒絕新呼叫。修法：把檢查搬進 `_mutate_and_save_state` 的 mutator 內部，
  對著剛從 `load_state` 重新讀出來、鎖保護下的 `target_state` 判斷，跟實際
  寫入用同一次鎖——`skill_check`／`sanity_check`／`offer_check_choice`／
  `offer_npc_attack_defense_choice` 四個工具都改成這個寫法（後者連攻擊方
  擲骰也一起搬進鎖裡，擋下的呼叫一樣不會浪費一次骰子）。四個 handler 也因此
  變乾淨：不再需要 mutate 完之後另外 `require_character` 拿一次最新角色物件
  才能組回應——mutator 內部直接把完整的成功／被擋回應建好回傳。
- **pending check 沒有清除機制，已補上**：新增工具 `clear_pending_check`
  （`investigator` 參數），讓 Keeper 在判斷某筆待處理檢定已經過時（劇情跳過、
  角色離場/倒下等，不會再有人回覆）時主動清掉它，不用擲骰也不用判定成敗，
  單純把 `pending_checks[owner_id]` pop 掉；沒有待處理檢定時呼叫是安全的
  no-op。已加進 `_KP_ASSISTANT_ALLOWED_TOOL_NAMES`（KP 助手也能用）跟
  `_KP_ALWAYS_CANONICAL_GAME_TOOL_NAMES`（清除也是真實的遊戲狀態變更，算
  canon），系統提示也補了一段：先確認那筆檢定真的過時、玩家還沒回覆的不要
  清，避免被拿來取巧繞過守衛。
- **DRY 重複，已修正**：`offer_check_choice`／`offer_npc_attack_defense_choice`
  裡幾乎一模一樣的「把 raw options 轉成帶 skill_value 的選項清單」邏輯抽成
  共用函式 `_resolve_defense_options`。
- **端到端測試，已補上**：新增
  `tests/test_npc_attack_latency.py::
  OfferNpcAttackDefenseChoiceEndToEndTests::
  test_choosing_fight_back_resolves_with_the_system_rolled_attacker_tier`
  ——真的把 `offer_npc_attack_defense_choice` 寫入的 `pending_checks` 一路
  串到 `legacy_commands._resolve_check_deterministically`（`/coc check` 的
  實際解析），驗證系統擲的 `attacker_tier`（critical）確實贏過玩家後來擲的
  防守方骰（regular），敘事文字正確講「反擊沒有生效」，而且檢定解決後
  `pending_checks` 真的被清空，不會卡住。這之前完全沒有測試覆蓋過。

**仍然沒動、留給 Marco 決定**：`ally:`/`enemy:` 分支目前不可達（見上面
「已修正」小節），是要保留當未來隊友互打/敵方內鬥功能的前瞻設計，還是先
拿掉等真的支援了再補，這是設計意圖問題，不是可以直接判斷對錯的 bug，這次
沒有動。

新增測試：`tests/test_npc_attack_latency.py` 的 `ClearPendingCheckTests`
（4 個：清除既有 pending check、沒有 pending check 時安全 no-op、找不到
角色的錯誤處理、清除後可以重新註冊新的檢定）、`test_kp_assistant_v2.py`
的白名單測試補上 `offer_npc_attack_defense_choice`／`clear_pending_check`
兩個之前沒被明確斷言在內的工具名稱。全套測試 260 題（原 255 + 5 個新測試），
只剩跨分支未合併造成的 2 個預期性失敗（PR #44 修的 reply metrics，跟這個
分支無關）。
