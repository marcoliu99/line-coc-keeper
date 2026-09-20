# 設計規格：NPC／怪物攻擊回合延遲調查與優化選項

> 適用範圍：`main_v2` 分支（多 agent 架構：`app/agents/supervisor.py` →
> `executor.py`／`narrator.py`，實際工具執行仍在 `app/keeper.py` 的
> `_execute_tool`）。

## 目標

調查「每次到怪物（NPC）攻擊時，Keeper 回應總是特別久」這個現象的根因，並列出
可行的優化方向。這份文件目前只到「調查＋選項」，還沒有定案要做哪個，等 review
後再決定範圍、動手實作。

## 現況（根因）

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

