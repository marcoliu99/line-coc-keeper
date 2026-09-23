# Spec: 閃避/反擊平手規則修正 + 遠程對抗機制重建 + UI 顯示強化

## Changeset Tracking
- **main_v2 start**: origin/main_v2:faf9fe6
- **implementation end**: TBD

## 0. 緣起

原始需求只是「閃避/反擊按鈕顯示強化」（按鈕上只顯示技能%，容易讓玩家誤會判定方式，
想加上『需要達到什麼等級/百分比才能成功』的提示）。調查過程中發現兩個更嚴重、影響
遊戲判定正確性的規則 bug，經使用者確認 + WebSearch 交叉驗證 COC7e 官方規則後，
決定一起處理。

## 1. 問題一：近戰平手規則 bug（CONFIRMED，已用官方規則驗證）

### 1.1 現狀

`app/dice.py::resolve_opposed(defender_tier, attacker_tier)`：

```python
def resolve_opposed(defender_tier: str, attacker_tier: str) -> str:
    d_rank, a_rank = TIER_RANK[defender_tier], TIER_RANK[attacker_tier]
    if d_rank <= TIER_RANK["fail"] and a_rank <= TIER_RANK["fail"]:
        return "both_miss"
    if d_rank > a_rank:
        return "defender_wins"
    if d_rank == a_rank:
        return "tie_attacker_wins"   # ← 平手一律判攻擊方贏
    return "attacker_wins"
```

`app/legacy_commands.py::_describe_opposed_outcome(defender_name, is_counter, defender_tier, attacker_tier)`
有 `is_counter: bool` 參數區分「閃避」跟「反擊」，但呼叫 `resolve_opposed()` 時
**完全沒有把 `is_counter` 傳進去**——不管閃避還是反擊，平手都被判定攻擊方贏。

### 1.2 正確規則（WebSearch 驗證，來源見下）

- **反擊（Fight Back）平手 → 攻擊方贏**（現狀對這半邊是對的）
- **閃避（Dodge）平手 → 防禦方贏**（現狀是錯的，目前程式碼一律判攻擊方贏）

來源：
- https://philgamer.wordpress.com/2018/02/14/lets-study-call-of-cthulhu-7th-edition-part-2b-opposed-rolls-and-hand-to-hand-combat/
- https://morganhua.blogspot.com/2017/06/call-of-cthulhu-7th-ed-combat-q.html

使用者提供的原因說明（供 Narrator 敘事引用）：反擊本質是「以傷換傷」，平手時攻擊方
的攻勢先命中；閃避是全神貫注的純防禦姿態，放棄了傷害對手的機會、全力閃避，因此平手
時規則傾向保護採取純防禦姿態的一方。

### 1.3 影響範圍

**玩家實際遊戲判定錯誤**：近戰對抗中，玩家選「閃避」且雙方成功等級打平時，目前系統
判定玩家挨打（攻擊方贏），但正確規則應該是玩家躲開（防禦方贏）。這不是顯示問題，是
每一場包含平手閃避的戰鬥都可能算錯結果。

### 1.4 修正方式

`resolve_opposed()` 需要新增一個參數區分閃避/反擊，讓平手分支依情境給不同結果：

```python
def resolve_opposed(defender_tier: str, attacker_tier: str, is_counter: bool) -> str:
    d_rank, a_rank = TIER_RANK[defender_tier], TIER_RANK[attacker_tier]
    if d_rank <= TIER_RANK["fail"] and a_rank <= TIER_RANK["fail"]:
        return "both_miss"
    if d_rank > a_rank:
        return "defender_wins"
    if d_rank == a_rank:
        return "tie_attacker_wins" if is_counter else "tie_defender_wins"
    return "attacker_wins"
```

`_describe_opposed_outcome()` 呼叫點要把已有的 `is_counter` 參數傳進去；新的
`"tie_defender_wins"` 結果要走跟 `"defender_wins"` 相同的成功敘事分支（差別只在
要不要註記「平手」），呼叫端（`_build_check_narration` 等）也要確認沒有寫死只認
`"defender_wins"`/`"tie_attacker_wins"` 兩種平手/非平手字串而漏掉新結果。

## 2. 問題二：遠程對抗機制目前完全錯誤地套用近戰 opposed roll（CONFIRMED）

### 2.1 現狀

目前 `offer_npc_attack_defense_choice`（`app/keeper.py:266` 附近）不分近戰/遠程，
一律：程式先擲好攻擊方（NPC）的技能檢定結果（tier），玩家選閃避/反擊後，玩家的
擲骰結果直接拿去跟攻擊方的 tier 做 `resolve_opposed()` 比較。

Prompt 裡目前唯一的近戰/遠程差異只有「要不要給反擊選項」（近戰兩個選項、遠程只給
閃避），**攻擊方是否命中的判定機制本身沒有區分**——遠程攻擊也被當成 opposed roll
處理。

### 2.2 正確規則（WebSearch 驗證）

來源：
- https://rpggeek.com/thread/2027510/firearms-point-blank-range-and-fighting-back
- https://cthulhuwiki.chaosium.com/rules/combat.html

**不能對槍械/遠程攻擊做傳統意義的 dodge-as-opposed-roll 或 fight back。** 正確流程：

1. 攻擊者（NPC）做自己的技能檢定：D100 ≤ 技能值即命中，**不能孤注一擲/Push**。
   這個檢定**不跟防禦方比較**，是純粹的單方檢定。
2. 防禦方唯一能做的事是「撲向掩體」（dive for cover）——這是防禦方自己單獨的
   Dodge 技能檢定，**不是跟攻擊方比 tier 的 opposed roll**。
3. 若防禦方的 Dodge 檢定成功，攻擊方這次射擊要承受 1 個 penalty die
   （官方規則裡跟其他 penalty die 情境並列：連發/點射、快速移動的目標、
   移動中開槍、對戰局中開槍……都是同樣「加一個 penalty die」的機制，
   跟「撲向掩體成功」是同一類修正，不是特殊例外）。
4. 遠程攻擊沒有反擊選項（維持現狀不變）。

### 2.3 影響範圍

`offer_npc_attack_defense_choice` 目前的呼叫順序（先擲攻擊方 tier → 玩家選 →
玩家擲骰跟攻擊方比較）在遠程情境下整個是錯的：正確順序應該反過來——**先讓防禦方
決定要不要撲向掩體並擲骰，再依撲向掩體是否成功決定攻擊方要不要多帶一個 penalty
die，然後才擲攻擊方的命中判定**。這是這次影響範圍最大的架構調整，因為要交換
「誰先擲骰」的順序，且遠程與近戰現在必須走兩條不同的流程分支。

### 2.4 修正方式（草案，待實作時細化）

1. `offer_npc_attack_defense_choice` 新增/確認一個「近戰 or 遠程」的 range 參數
   （呼叫端 prompt 已經在判斷要不要給反擊選項，代表這個資訊呼叫時已經知道，只是
   目前沒有正式參數化、也沒有拿去分流判定機制）。
2. 遠程分支：
   - 不預先擲攻擊方的檢定。
   - 提供玩家一個「撲向掩體（Dodge）」的按鈕選項（沒有「反擊」選項，維持現狀）。
   - 玩家觸發擲骰後，先算防禦方的 Dodge 檢定結果（純粹自己的技能值 vs D100，
     不是 opposed roll）。
   - 防禦方 Dodge 成功 → attacker 這次的技能檢定帶 1 個 penalty die；
     防禦方 Dodge 失敗 → attacker 正常擲，無修正。
   - 攻擊方擲骰命中與否是純粹的單方技能檢定（≤ 技能值成功，不能 push），
     跟防禦方的 tier 完全無關；不要再呼叫 `resolve_opposed()`。
   - 命中後的傷害/貫穿判定維持現有的 `roll_weapon_damage`/`roll_impaling_damage`
     機制不變（這部分已用官方規則核對過是對的，見 §3）。
3. 近戰分支：維持現有「先擲攻擊方 tier、玩家選閃避/反擊、比較 tier」的 opposed
   roll 流程，只套用 §1 的平手規則修正。

### 2.5 未決問題（待 review 時確認）

- 「撲向掩體」是否需要場景上真的有掩體才能選（官方規則字面上沒有強制要求，但
  KP 可能想加場景合理性判斷）？本 spec 先不強制要求，交給 Narrator/Keeper 敘事
  層判斷，不在程式碼加掩體存在與否的機械檢查。
- penalty die 的加成，是要程式碼自動套用到攻擊方後續的 `skill_check`/擲骰
  呼叫，還是只在敘事跟結果說明中提及、由 Keeper 自己決定何時呼叫帶
  `penalty_dice=1` 的檢定？傾向前者（程式碼自動套用，比較不會被 LLM 漏掉），
  但要確認 `offer_npc_attack_defense_choice` 目前的呼叫介面能不能乾淨地把這個
  修正值傳到後續實際執行攻擊方檢定的地方。

## 3. 貫穿（Impale）傷害規則 — 已核對，不需修改

`app/dice.py::calculate_impaling_damage()` 的邏輯（極限成功：穿刺武器 = 武器
最大傷害 + 最大傷害加值 + 額外重擲一次武器傷害；非穿刺武器 = 只算最大值不重骰）
跟官方規則一致，經 WebSearch 驗證：
https://call-of-cthulhu-nachtstadt-berlin.fandom.com/wiki/Determining_Damage

**未實作、本次也不處理的邊緣規則**：「極長距離只有極限成功才會命中時，貫穿只在
擲出 01（大成功）才觸發，極限成功一律算普通命中不貫穿」——這個規則需要知道攻擊
距離跟該距離下的難度門檻，目前專案的戰鬥系統沒有「距離」這個機械概念，範圍明顯
超出這次任務，且使用者沒有提出這個需求，不在本次範圍內。

## 4. UI 顯示強化（原始需求）

### 4.1 現狀

`app/discord_bot.py::_check_button_specs()`：

```python
if check.get("type") == "choice":
    return [
        (f"選擇並擲 {o['label']}（{o['skill']} {o['skill_value']}%）", False, f"#{index}")
        for index, o in enumerate(check.get("options", []))
    ]
```

按鈕只顯示「選擇並擲 閃避（閃避 45%）」——只有技能百分比，沒有告訴玩家「因為
NPC 已經擲出結果，你需要達到什麼程度才算贏」，容易讓玩家誤以為這是單純的技能
檢定（骰進 45% 就算成功），而非需要贏過攻擊方 tier 的對抗檢定。

### 4.2 設計（已與使用者確認）

在按鈕上方的說明文字（`_post_check_buttons()` 的 `text` 變數）裡，加入一段依
`attacker_tier`（NPC 已擲出的成功等級）+ 玩家自己技能值算出的「需要達到的最低
等級 + 對應百分比門檻」提示。百分比門檻依 COC7e 規則從玩家技能值反推：

- 困難成功門檻 = 技能值 // 2
- 極難成功門檻 = 技能值 // 5
- 大成功 = 骰出 01

依§1 修正後的規則，「需要達到的最低等級」因閃避/反擊而不同：
- **反擊**：平手判攻擊方贏，玩家需要**嚴格高於** `attacker_tier` 才算贏。
- **閃避**：平手判防禦方贏，玩家只需要**達到或高於** `attacker_tier` 就算贏
  （這點原本設計成「嚴格高於」是錯的，要一併修正提示文字的計算邏輯，呼應§1）。

若 `attacker_tier` 是 `critical`（大成功）：
- 反擊：無法贏過（沒有比 critical 更高的等級），**不提供反擊選項**（使用者已確認：
  「大成功 不能選反擊」）——這需要在 `offer_npc_attack_defense_choice` 產生
  options 的地方（或呼叫端 `_resolve_defense_options`）過濾掉反擊選項，不能只
  在 Discord 按鈕顯示層面隱藏，否則文字輸入 `/coc check 反擊` 等其他入口仍能繞過。
- 閃避：平手（`critical` vs `critical`）防禦方仍然贏，所以閃避在對方大成功時
  **仍然可能贏**（只要玩家也擲出大成功），不需要特殊排除。

### 4.3 呈現範例

```
👉 小明，對方擲出「困難成功」。
   選擇「閃避」需要達到「困難成功」（≤ {skill_value//2}）以上才能躲開；
   選擇「反擊」需要達到「極難成功」（≤ {skill_value//5}）以上才能命中。
請選擇要採取的防守／行動方式，並由你觸發擲骰：
```

（實際文案措辞可在實作時調整，數字與規則邏輯需與上述一致。）

## 5. 測試計畫

- `dice.resolve_opposed()`：新增 `is_counter` 參數後，閃避平手 → defender 贏；
  反擊平手 → attacker 贏；非平手情境行為不變（regression：舊測試案例要全部
  改傳 `is_counter` 參數並保持原本斷言）。
- 遠程對抗新流程：
  - 防禦方撲向掩體成功 → 驗證攻擊方後續檢定確實帶了 1 個 penalty die。
  - 防禦方撲向掩體失敗 → 攻擊方正常檢定，無修正。
  - 遠程攻擊全程不呼叫 `resolve_opposed()`。
- UI 文字：
  - 依 `attacker_tier` 算出的門檻百分比數字正確（用具體技能值算過一輪驗證
    floor 除法邊界，例如 45% → hard=22, extreme=9）。
  - `attacker_tier == "critical"` 時，選項清單裡沒有「反擊」。
  - 閃避的提示門檻跟反擊不同（前者用「達到或高於」，後者用「嚴格高於」）。

## 6. Notes / Review Gate

- 這是本次任務中範圍最大的一份 spec，涉及核心戰鬥判定邏輯修改（不是單純顯示層），
  請仔細 review §1、§2 的規則描述是否跟你認知的 COC7e 規則完全一致，尤其 §2.5
  的未決問題需要你明確拍板才能開始實作。
- 本文件 review 通過前，不開始修改 runtime code。
