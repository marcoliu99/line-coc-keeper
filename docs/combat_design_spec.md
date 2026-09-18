# Combat Design Spec

本文描述下一版戰鬥系統的目標設計。現有 `app/combat.py` 只追蹤先攻順位、HP、倒下與暫離；本規格要補上敵人專用戰鬥卡、特殊能力決策、固定結算時點，以及玩家多角色狀態隔離。本文先作為實作前契約，尚未代表程式已完成。

## 目標

1. 戰鬥開始前，系統必須為每個敵人建立內部戰鬥卡，包含 HP、護甲、攻擊、特殊能力、觸發條件、每輪或全戰鬥可用次數、狀態與環境效果。
2. 敵人回合不是「玩家站在面前就揮拳」。每個敵人回合先檢查特殊能力與觸發條件，再決定近戰、移動、防禦、撤退、施法、等待或其他行動。
3. 像 Song of Lost Dreams 這類核心能力，首次可用時依規則執行 POW 對抗與效果敘述，但公開敘事不得洩漏玩家尚未發現的數值、弱點、冷卻或內部觸發條件。
4. 火焰、護甲、重傷、環境效果分開結算，並在每輪固定時點處理，避免 Keeper 臨場忘記。
5. 角色切換、暫離、測試角色與 Partner 角色改用獨立角色狀態，不再讓 Mark、測試、Partner 的技能、裝備、HP 混在一起。

## 非目標

- 不在本階段重寫 COC7e 全部戰鬥規則。
- 不做戰棋格子、距離精算或完整追逐規則；距離先用抽象 range band。
- 不讓玩家直接看到敵人戰鬥卡。只有 KP Assistant / Keeper 內部可見。
- 不把 LLM 自然語言敘事當作 authoritative state；HP、護甲、次數、狀態、環境效果必須由程式狀態保存。
- 不在本階段從 PDF 劇本自動完整抽取所有怪物 stat block；`add_npc_to_combat` 可先由 Keeper/KP Assistant 提供護甲、攻擊與能力資料，缺漏時建立 minimal card。
- 不在本階段實作精確距離/接戰狀態，因此 engaged-only 攻擊的完整距離判斷是後續工作；目前回合規劃先在抽象 range band 內選擇可用能力/攻擊。

## 目前實作範圍

截至 `feature/combat-cards-and-character-state` 目前 commit，本規格已有一個可運作的第一階段實作：

- `Combatant` 已有 stable `combatant_id`、`display_name`、`side`、`character_id`、`enemy_card_id`，並保留舊 `name/is_pc/is_ally` 相容欄位。
- `EnemyCombatCard`、`ArmorRule`、`AttackRule`、`SpecialAbility`、`EffectState` 已加入 state model，並支援 `to_dict()` / `from_dict()` round trip。
- `GroupState` 已加入 `characters_by_id` 與 `active_character_id_by_user`，舊 `characters` 存檔會 migrate 到新角色索引。
- `start_combat` 只把目前 active 且未倒下/未暫離的角色放入 initiative，避免 Partner/test 角色與 primary 狀態混在一起。
- `add_npc_to_combat` 仍是公開 tool 名稱，但會建立 enemy combat card；未提供攻擊/能力時建立 minimal card，並用預設徒手攻擊維持相容。
- `plan_enemy_turn` 會在敵人回合先處理 `turn_start` effects，再依 priority 檢查可用 special ability；沒有可用能力時才選攻擊或移動。
- `resolve_enemy_action` 會消耗特殊能力 usage/cooldown，且同一個 `plan_id` 重複 resolve 會回 `already_resolved=True`，不會重複扣次數。
- `apply_combat_damage` 已保存 raw/armor/final/hp breakdown，對 PC 重大傷害會註冊 pending CON check；公開摘要不得洩漏護甲精確數值。
- `add_combat_effect` 已可建立固定時點 effect；`process_timing` 可在 round/turn timing 套用固定傷害或骰式傷害。
- KP Assistant allowlist 開放 `apply_combat_damage` 與 `add_combat_effect`，這兩個成功結果會成為 canonical game event；`damage_combatant` 仍不開放給 KP Assistant。

## 現況落差

現有模型：

- `CombatState`: `active`、`round_number`、`order`、`current_index`
- `Combatant`: `name`、`dex`、`hp`、`hp_max`、`is_pc`、`is_ally`、`defeated`
- PC 狀態存在 `GroupState.characters: owner_id -> Character`
- 暫離是 `Character.away`

缺口：

- NPC 沒有護甲、攻擊表、特殊能力、冷卻、每輪使用次數、狀態標籤或秘密欄位。
- `add_npc_to_combat` 只接受 `name/dex/hp`，敵人行為完全靠 Keeper prompt 記得。
- 敵人回合沒有可檢查的決策流程。
- 傷害入口只調整 HP，沒有分離命中、護甲、火焰、重傷、環境效果。
- `GroupState.characters` 以 user_id 為唯一角色槽，一個玩家切換角色或測試角色時容易覆蓋同一人的目前角色狀態。

## 資料模型

### Combatant 擴充

`Combatant` 保留先攻排序用途，但應擴充成能指向角色或敵人卡：

```python
Combatant(
    combatant_id: str,        # stable id, e.g. "pc:<character_id>" or "enemy:<uuid>"
    display_name: str,
    dex: int,
    side: "pc" | "enemy" | "ally",
    character_id: str = "",   # PC/ally with real character state
    enemy_card_id: str = "",  # enemy combat card
    defeated: bool = False,
)
```

相容策略：讀到舊 `Combatant.name` 時，以 `combatant_id = "legacy:<name>"` 載入，並保留既有 status 顯示。

### EnemyCombatCard

敵人專用戰鬥卡是戰鬥中敵人的 authoritative state。

```python
EnemyCombatCard(
    id: str,
    name: str,
    aliases: list[str],
    source: {
        "scenario_id": str,
        "page": int | None,
        "chapter_id": str,
        "raw_excerpt": str,
    },
    hp: int,
    hp_max: int,
    armor: list[ArmorRule],
    attacks: list[AttackRule],
    abilities: list[SpecialAbility],
    stats: dict[str, int],       # STR/CON/SIZ/DEX/POW/INT etc.
    skills: dict[str, int],
    status_tags: list[str],
    effect_states: list[EffectState],
    hidden_notes: str,           # not player-facing
    public_description: str,     # safe narration seed
)
```

`hidden_notes` 可包含弱點、免疫、能力真名、觸發條件與劇本提示；不得直接進玩家公開回覆。

### ArmorRule

```python
ArmorRule(
    id: str,
    label: str,
    value: int,
    applies_to: "all" | "physical" | "fire" | "bullet" | "melee" | "magic",
    bypass_tags: list[str],      # e.g. ["fire", "silver", "called_shot"]
    public_hint: str,
)
```

護甲不是 HP。傷害結算必須先保存 raw damage，再保存 armor reduction，最後才改 HP。

### AttackRule

```python
AttackRule(
    id: str,
    label: str,
    skill_name: str,
    skill_value: int,
    damage: str,
    range_band: "engaged" | "near" | "far" | "any",
    max_targets: int = 1,
    tags: list[str] = [],
    ammo_or_uses: int | None = None,
    public_description: str = "",
)
```

`damage` 使用既有 dice expression。是否可閃避、反擊或對抗仍依 COC7e 與現有 Keeper 工具流程決定。

### SpecialAbility

```python
SpecialAbility(
    id: str,
    name: str,
    priority: int,
    trigger: AbilityTrigger,
    check: AbilityCheck,
    effect: AbilityEffect,
    usage: UsageLimit,
    cooldown_rounds: int = 0,
    reveal_policy: RevealPolicy,
)
```

`trigger` 必須是可檢查條件，例如：

- `first_available`: 戰鬥中第一次符合條件就應考慮。
- `round_start`: 每輪固定時點。
- `on_enemy_turn`: 敵人回合開始。
- `on_damage_taken`: 受到傷害後。
- `target_in_range`: 有目標在指定 range band。
- `hp_below`: HP 低於門檻。
- `state_missing`: 某效果尚未套用。

`usage`：

```python
UsageLimit(
    per_round: int | None,
    per_combat: int | None,
    used_this_round: int = 0,
    used_total: int = 0,
)
```

`reveal_policy`：

```python
RevealPolicy(
    player_facing_name: str,      # empty means do not name it yet
    hide_numbers: bool = True,
    hide_weakness: bool = True,
    reveal_after_observed: bool = False,
)
```

### EffectState

持續效果需獨立追蹤，不混進 notes。

```python
EffectState(
    id: str,
    label: str,
    source_id: str,
    target_id: str,
    timing: "round_start" | "turn_start" | "turn_end" | "round_end",
    remaining_rounds: int | None,
    damage: str = "",
    damage_type: str = "physical",
    save_or_check: dict = {},
    tags: list[str] = [],
    public_description: str = "",
)
```

火焰、流血、中毒、夢境/精神效果、壓制、抓握都走這個結構。

`damage` 支援兩種 authoritative 表示：

- 固定傷害：`"1"`、`"3"`。用於碎玻璃、臨時火焰、場景壓迫、玩家創意造成的確定傷害。
- 骰式傷害：`"1d2"`、`"1d6+1"`。沿用既有 dice expression。

非法傷害表示式不得被靜默吞掉。效果引擎必須回傳 private error result，且不得在完全沒有套用成功時消耗 `remaining_rounds`。

### KP Assistant Fixed Damage Authority

KP Assistant 可以把玩家臨場發想或主持層裁定轉成正式的固定傷害，但必須經由 deterministic tool 寫入或結算，不能只靠自然語言改變 HP。

允許的情境：

- 立即固定傷害，例如「Marco 被碎玻璃割到，直接 1 點傷害」。
- 持續固定傷害，例如「燃燒每個回合開始造成 1 點火焰傷害，持續 3 輪」。
- 場景或怪物能力造成的特殊固定傷害，例如「夢境壓迫每輪 2 點，但不是普通物理攻擊」。

工具邊界：

- 立即傷害使用 `apply_combat_damage(target, raw_damage, damage_type, tags, source_id)`，`raw_damage` 是整數，仍走護甲、重傷與 HP 同步流程。
- 持續或固定時點傷害使用 `add_combat_effect(target, label, timing, damage, damage_type, remaining_rounds, tags, source_id)`，`damage` 可為固定整數字串或骰式。
- KP Assistant 使用這些工具時，該回合必須成為 canonical game event，而不是單純 OOC 討論。
- 公開敘事只呈現玩家可感知效果；private result 可以包含 source、tags、raw/final damage、非法表示式錯誤等診斷。
- KP Assistant allowlist 開放 `apply_combat_damage` 與 `add_combat_effect`，但仍不開放泛用 `damage_combatant`，避免用正負 delta 繞過護甲、重傷與傷害來源紀錄。

Effect damage parser：

```python
resolve_effect_damage("1")      -> 1
resolve_effect_damage("1d6+1")  -> dice roll total
resolve_effect_damage("1d1")    -> error, because existing dice rules reject one-sided dice
```

固定時點處理規則：

1. `process_timing` 找到 timing 與 target 符合的 effect。
2. 若 `damage` 解析成功，呼叫 `apply_combat_damage`，並把 result 回傳給 caller。
3. 只有當 effect 成功套用，才扣 `remaining_rounds`。
4. 若 damage expression 非法或 target 不存在，回傳 `ok=False` 的 private error result，effect 留在 state 中等待 KP 修正。
5. `remaining_rounds=None` 代表無限期效果；成功套用後不會被自動移除。

### Character Identity

為了避免 Mark、測試、Partner 混在一起，角色狀態必須從「每個 user_id 一格」改為「每個角色一格，user_id 只是擁有者」。

```python
Character(
    character_id: str,
    owner_id: str,
    name: str,
    slot: "primary" | "partner" | "test" | "npc_ally",
    active: bool,
    ...
)
```

`GroupState` 目標形狀：

```python
characters_by_id: dict[str, Character]
active_character_id_by_user: dict[str, str]
```

相容策略：

- 舊 `characters: owner_id -> Character` 載入時，生成 deterministic `character_id = "legacy-user:<owner_id>"`。
- 存檔初期可同時寫舊欄位與新欄位，等指令與 Keeper 完全改用新欄位後再移除舊欄位。
- `/coc away` 只標記目前 active character，不把同一 user 的 Partner / test 角色一起暫離。

## 戰鬥開始流程

1. `start_combat` 建立 `CombatState`，seed 所有 active 且 HP > 0 的 PC。
2. Keeper 或 KP Assistant 加入敵人時，不再只傳 `name/dex/hp`；要先建立或選取 `EnemyCombatCard`。
3. 建卡來源依序：
   - 從 `scenario_npc_index` 精準匹配 NPC / monster。
   - 從目前章節 Scenario RAG 檢索敵人 stat block。
   - 從 Keeper 明確提供的自然語言 stat block 建立。
   - 若仍不足，只能建立 minimal card，且狀態標示 `incomplete=True`，Keeper prompt 必須提醒需要補齊攻擊/能力。
4. 敵人加入 initiative 時，`Combatant.enemy_card_id` 指向該卡。

## 敵人回合決策流程

每個敵人回合必須由程式或工具回傳一份 `EnemyTurnPlan`，Keeper 只能依 plan 敘事與請求必要檢定。

```python
EnemyTurnPlan(
    enemy_card_id: str,
    selected_action: "special_ability" | "attack" | "move" | "defend" | "wait" | "flee" | "other",
    selected_id: str,
    target_ids: list[str],
    required_rolls: list[dict],
    private_reason: str,
    public_hint: str,
)
```

決策順序：

1. 清除或重置 `used_this_round`（只在新輪開始時）。
2. 處理 `turn_start` effects。
3. 讀取敵人卡與場上狀態。
4. 依 `priority` 檢查 special abilities：
   - trigger 成立
   - usage 未耗盡
   - cooldown 可用
   - 目標合法
5. 若有能力可用，優先產生 `special_ability` plan。
6. 否則檢查攻擊：
   - 已接戰且有合適攻擊才近戰。
   - 有遠程/精神/範圍能力則可不接近。
   - 若目標不在 range，產生 `move` 或 `wait`，不是硬揮拳。
7. 產生公開敘事提示，但 private_reason 不進玩家回覆。
8. Keeper 執行 required rolls，完成後呼叫 action resolution tool 寫回 usage/cooldown/effects/damage。

`resolve_enemy_action(plan_id)` 必須是 idempotent：第一次成功 resolve 才會消耗 usage/cooldown，之後同一個 `plan_id` 重複呼叫只回報 `already_resolved=True`，不得重複扣特殊能力次數。這保護 LLM/tool retry、網路重送與主持誤按造成的重複結算。

## Song of Lost Dreams 類能力

這類能力定義成 `SpecialAbility`，而不是 Keeper prompt 裡的提醒。

範例：

```python
SpecialAbility(
    id="song_of_lost_dreams",
    name="Song of Lost Dreams",
    priority=100,
    trigger={"type": "first_available", "range_band": "near_or_audible"},
    check={
        "type": "opposed",
        "attacker_stat": "POW",
        "defender_stat": "POW",
    },
    effect={
        "on_success": "apply_effect",
        "effect_id": "lost_dreams_trance",
    },
    usage={"per_combat": 1},
    reveal_policy={
        "player_facing_name": "",
        "hide_numbers": True,
        "hide_weakness": True,
    },
)
```

公開敘事可說：

- 「那聲音像從夢裡滲出來，讓你的意識短暫失焦。請做 POW 對抗。」

公開敘事不可說：

- 「牠使用 Song of Lost Dreams。」
- 「牠 POW 90，你 POW 50，所以很危險。」
- 「這招一場只能用一次。」
- 「火焰可以打斷牠。」

能力被玩家觀察、研究或成功檢定後，才可依 `reveal_policy` 逐步揭露名稱、規則或弱點。

## 固定結算時點

每輪流程：

1. `round_start`
   - reset per-round usage
   - 火焰/環境場域效果
   - 全場倒數效果
2. 每個 combatant 的 `turn_start`
   - 個人持續傷害
   - 昏迷/束縛/恐懼等限制行動效果
   - 敵人特殊能力決策
3. 行動解析
   - 命中/對抗
   - 傷害骰
   - 護甲/抗性/弱點
   - HP 變更
   - 重傷/瀕死/倒下
   - 觸發 on_damage_taken / on_defeated
4. `turn_end`
   - 本回合結束效果
   - cooldown 減少（若規則指定）
5. `round_end`
   - 場景火勢擴散、煙霧、坍塌、儀式進度等環境效果

重傷規則必須與現有 PC `adjust_character` 行為一致：單次傷害達門檻時註冊 CON 檢定或套用對應狀態。NPC 是否需要重傷檢定由卡片或全域設定決定，預設普通敵人只用 HP/defeated，不替每個雜兵跑完整重傷流程。

目前 PC 重傷契約：

- `apply_combat_damage` 對 PC 造成單次 final damage 達 `hp_max / 2` 且角色仍存活時，設定 `major_wound_triggered=True`。
- 同時在 `state.pending_checks[owner_id]` 註冊一次 CON 檢定，格式與 `adjust_character` 的重傷檢定一致：

```python
{
    "type": "skill",
    "skill": "CON",
    "skill_value": character.con,
    "bonus_dice": 0,
    "penalty_dice": 0,
    "difficulty": "regular",
    "major_wound_trigger": True,
}
```

- NPC/敵人預設不註冊 CON 重傷檢定，只更新 HP/defeated。

## 傷害結算契約

新增或重構後的傷害結果應保留完整 breakdown：

```python
DamageResolution(
    target_id: str,
    raw_damage: int,
    damage_type: str,
    armor_reduction: int,
    weakness_bonus: int,
    final_damage: int,
    hp_before: int,
    hp_after: int,
    major_wound_triggered: bool,
    defeated: bool,
    public_summary: str,
    private_notes: str,
)
```

公開回覆可以描述「刀刃被硬殼擋去一部分」，但不應在未揭露前說「護甲 3」或「弱點火焰 +1D6」。

目前公開摘要契約：

- `public_summary` 可以說「部分傷害被擋下」。
- `public_summary` 不得包含 `armor_reduction` 的精確數字或 `armor_label`。
- `private_notes` 可以保存 `raw`、`armor_label`、`armor_reduction`、`source_id`，供 Keeper/KP Assistant 內部追蹤。

## 工具/API 目標

保留既有工具名稱可相容。第一階段已實作/接線的 tool：

- `start_combat()`
- `add_npc_to_combat(name, dex, hp, is_ally=False, armor=None, attacks=None, abilities=None)`
- `get_combat_status()`
- `plan_enemy_turn(enemy="")`
- `resolve_enemy_action(plan_id)`
- `apply_combat_damage(target, raw_damage, damage_type="physical", tags=[], source_id="")`
- `add_combat_effect(target, label, timing, damage="", damage_type="physical", remaining_rounds=None, tags=[], source_id="", public_description="")`
- `advance_combat_turn()`
- `end_combat()`

後續可再拆出更高階 tool：

- `create_enemy_combat_card(source_name, stat_block=None, count=1)`
- `add_enemy_card_to_combat(enemy_card_id)`
- `get_combat_status(include_private=False)`
- `apply_combat_effect(target_id, effect)` 若未來需要非傷害 effect 的泛用 schema

玩家/Keeper 公開工具預設不回傳敵人 private notes。KP Assistant 可以使用查詢與已開放的正式流程工具；目前只開放 `apply_combat_damage` / `add_combat_effect` 這類會走傷害契約的 mutation，不開放 `damage_combatant` 這種泛用 HP delta。

## Prompt 契約

Keeper prompt 必須改成：

- 正式戰鬥中，不得自行假設敵人只有普通攻擊。
- 輪到敵人時，先呼叫 `plan_enemy_turn`。
- 只有收到 plan 後才敘事。
- 不得公開 private_reason、hidden_notes、未揭露 ability name、數值或弱點。
- 若 plan 要求玩家檢定或對抗，使用既有 deterministic check/roll tool，而不是自己擲骰。

## 指令相容

既有指令先保留：

- `/coc combat start`
- `/coc combat addnpc 名稱 DEX HP`
- `/coc combat addally 名稱 DEX HP`
- `/coc combat status`
- `/coc combat next`
- `/coc combat damage 名稱 增減量`
- `/coc combat end`

但 `addnpc` 應成為 minimal card shorthand。未來可新增：

- `/coc combat addenemy 名稱`
- `/coc combat enemycard 名稱`
- `/coc combat effect ...`
- `/coc switch 角色名`
- `/coc characters`

## 測試需求

必須新增單元測試：

1. 敵人建卡包含 HP、護甲、攻擊、特殊能力、usage。
2. `plan_enemy_turn` 在特殊能力首次可用時選能力，不選普通拳擊。
3. 特殊能力 usage/per-round/per-combat 正確消耗與重置。
4. Song of Lost Dreams 產生 POW 對抗，但 public summary 不含能力真名、POW 數字、弱點。
5. 目標不在近戰距離時，敵人不會使用 engaged-only 攻擊，會 move/wait/使用遠程能力。
6. 傷害結算保存 raw damage、armor reduction、final damage。
7. 公開傷害摘要不洩漏 armor reduction 精確數字。
8. 火焰/環境效果在固定 round/turn timing 觸發，且固定傷害字串可用。
9. 非法 effect damage 回傳錯誤且不消耗 duration。
10. PC 重傷仍走現有 major wound pending CON 行為。
11. `resolve_enemy_action` 對同一 plan idempotent，不重複消耗 ability usage。
12. `away` 只跳過 active character，不影響同 user 的 Partner/test 角色。
13. 舊 `GroupState.characters` 存檔可 migrate 到 `characters_by_id`。
14. KP Assistant allowlist 包含 `apply_combat_damage` / `add_combat_effect`，但不包含 `damage_combatant`；成功傷害工具會 creates canon。

整合測試：

1. 從一段 scenario NPC stat block 建立敵人卡，加入戰鬥，跑完一輪。
2. 多名玩家、多角色、Partner、暫離混合時，status_text 顯示不混淆。
3. Keeper prompt 在戰鬥中包含公開狀態與必要 private planning instruction，但不把 hidden enemy notes 放到玩家可見輸出。

## 實作順序建議

1. 資料模型相容擴充：`EnemyCombatCard`、`ArmorRule`、`AttackRule`、`SpecialAbility`、`EffectState`。
2. 角色 identity migration：新增 `character_id` 與 active character mapping，保持舊欄位可讀。
3. enemy card 建立與 minimal card shorthand。
4. combat status 與 initiative 改用 `combatant_id`。
5. 固定時點與 effect engine。
6. damage resolution breakdown。
7. `plan_enemy_turn`。
8. Keeper prompt/tool wiring。
9. 指令與測試補齊。

## 完成定義

本功能完成時，真實遊戲中應滿足：

- 開戰前每個敵人都有可查的內部戰鬥卡。
- 敵人回合必定先檢查特殊能力，再選普通攻擊或移動。
- 核心能力可依規則執行對抗，但公開敘事不洩漏未發現資訊。
- 火焰、護甲、重傷、環境效果在固定時點結算，並可在測試中重現。
- 同一玩家控制/測試多個角色時，每個角色 HP、技能、裝備、暫離狀態互相獨立。
