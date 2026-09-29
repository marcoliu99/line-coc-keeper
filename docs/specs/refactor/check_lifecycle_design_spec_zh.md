# 檢定註冊生命週期

[English](check_lifecycle_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

類別：`refactor`。狀態：**已實作**。依據 2026-09-28 的 `main_v2` 版本 `83e0c54`。

玩家檢定的註冊分散在 Keeper 工具、戰鬥傷害及劇本開場。各呼叫端都必須自行記得 pending／Luck 准入、重複身分、狀態修改順序，以及何時可沿用既有擲骰。目標是以介面精簡、規則集中的註冊模組統一這些不變條件，方便直接測試；不增加 LLM 呼叫。

## 現況依據

- `keeper.py` 分別註冊手動技能、SAN 與選擇檢定。手動技能自有 pending／Luck 判斷；`offer_check_choice` 分支只查 pending，沒有分支內 Luck 判斷。技能與 SAN 的自動擲骰也各自實作准入。
- `keeper.py` 的 `adjust_character` 與 `combat.py` 都可能註冊重傷 CON 檢定；傷害與檢定必須維持同一筆狀態交易。
- `commands/handlers/system.py` 直接為所有調查員建立開場檢定。現有資料沒有新版檢定身分，讀取舊資料時仍需支援既有推導方式。
- `check_identity.pending_check_blocker` 已表達共同的 pending／Luck 規則，但呼叫端仍需知道何時呼叫，以及重複請求何時應視為無變更。
- `legacy_commands.py` 的部分賦值是結算期間恢復原本的 pending，不屬於建立新檢定，不應移進新的准入流程。

「選擇檢定」分支缺少 Luck 判斷是已確認的程式差異。外層防護是否使它無法觸發，仍要用針對性測試確認；本規格不宣稱已重現玩家端缺陷。

## 範圍

1. 註冊模組負責在最新 `GroupState` 判斷准入、pending 身分、重複／無變更語意，以及「准入前不能擲新骰或改狀態」。回傳明確的已註冊／相同／阻擋結果、實際保存的檢定身分和是否需要保存。呼叫端維持原有工具回應及玩家訊息。
2. 納入 Keeper 的手動技能／SAN、一般選擇、NPC 攻擊防禦選擇；`adjust_character` 與戰鬥的重傷 CON；以及劇本開場檢定。各種檢定的內容、選項過濾、擲骰、Luck 選項、傷害及 SAN 損失，仍由原本的規則模組處理。
3. 自動擲骰也要在同一筆交易中先准入再擲骰，並保留既有請求快取。只有核對當前時間線與所有權後才能沿用快取；快取不能越過不同的 pending 或未決 Luck。
4. 保留既有重複語意：相同手動技能／選擇請求沿用 pending 且不保存；遠程 NPC 攻擊不沿用先前攻擊骰；近戰只有完整原始選項、攻擊參數及距離條件一致才沿用。只有選項名稱的舊 pending 無法證明完全一致，須拒絕且不重擲。不同請求拒絕且不寫入、不擲骰。
5. 所有入口使用既有狀態鎖及重新讀取；註冊模組不另加鎖或自行保存。戰鬥及開場的觸發事件與註冊維持原子性。必要的 CON／開場檢定若被擋住，必須在提交前拒絕或明確回報，不能靜默遺失。
6. 保留既有 pending 欄位、舊版身分推導、Discord 按鈕 token、玩家所有權及時間線檢查。新的 NPC 防禦選擇增加選填 `raw_option_request` 指紋，用於安全比對重試；舊資料仍可讀取，但只有選項名稱時不得宣稱相同並沿用骰值。不做資料庫遷移。

## 流程

```text
工具／戰鬥／劇本開場
  -> 既有狀態鎖 + 最新 GroupState
  -> 建立檢定候選資料（尚未擲骰）
  -> 註冊模組：所有權 + 時間線 + pending/Luck + 防重複
       | 阻擋 -> 明確結果；不改狀態、不擲骰
       | 相同 -> 沿用身分；不保存、不擲骰
       | 准入 -> 建立 pending，或准許自動擲骰一次
  -> 原規則模組執行准許的擲骰／效果
  -> 一次提交權威狀態
  -> 既有結算與 Supervisor 後續
```

## 不處理項目

- 不更改 `/coc check`、Luck 決定結算、後續流程、Narrator 或工具 schema。
- 不增加固定 LLM 審稿、不重擲已結算檢定、不變更 autoroll 預設，也不把無關的戰鬥／SAN 規則塞進泛用規則引擎。
- 不把結算期間恢復原 pending 的動作視為新檢定註冊。

## 測試計畫

- 各入口共用測試矩陣：無 pending、相同 pending、不同 pending、未決 Luck、過期時間線，以及外層快照與鎖內重讀之間發生的結算。核對狀態版次，並確認阻擋／相同路徑不耗用新骰。
- 戰鬥傷害與 `adjust_character`：受阻的重傷檢定不能只提交 HP 變更卻丟失必要檢定；手動及自動路徑維持原本的重傷效果。
- 開場：既有 pending／Luck 不得被覆寫；不同調查員各有身分；新版及舊版項目皆可正常送達。
- NPC 選擇：重複近戰沿用原攻擊骰；重複遠程與選項變更不得產生新骰或繞過過濾結果。
- 執行相關既有測試 `test_luck_buyup_gate.py`、`test_npc_attack_latency.py`、`test_scenario_action_check_handoff.py`、`test_combat_cards.py`、`test_state_persistence.py`，再跑 Ruff、mypy 與完整 pytest。

## 實作決定

- 註冊介面位於既有交易內，不額外建立持久化 adapter。這保留 HP／檢定與開場／檢定的原子性；交易契約仍需整合測試。
- 新建立的開場檢定已補上新版身分；舊資料繼續使用穩定的舊版推導身分。
- 多人開場只要任一玩家被 pending／Luck 阻擋，即整批不註冊。部分註冊可能在開場已提交後只通知到部分玩家；明確阻擋結果可讓開場流程決定如何呈現。

## Keeper 工具註冊表對齊

PR #132 已將 Keeper 檢定 handler 搬到 `app/keeper_tools/checks.py`。技能、SAN、選項與 NPC 防禦流程已在新位置呼叫本模組的 `admit()`、`register()`、`metadata()` 和快取驗證，移除舊的手動 pending／Luck 判斷。PR #133 的角色 handler 將屬性變更交給 Keeper 內含 `_apply_attribute_delta` 的輔助函式；它留在權威狀態交易裡，並用 `blocker()`、`register()` 處理重傷 CON 檢定。戰鬥傷害與開場檢定仍由各自模組負責。兩項對齊已在 `integration/keeper-check-character-lifecycle` 同時驗證；各 PR 不再等待對方實作，但最終註冊表整合前仍須協調合併順序。
