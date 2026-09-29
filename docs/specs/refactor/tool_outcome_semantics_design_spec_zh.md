# 工具結果語意

[English](tool_outcome_semantics_design_spec.md)

狀態：**backlog，待規格審查**。基準：2026-09-29 的 `main_v2`，commit `7cef87c`。

## 問題與目標

Keeper 工具已在 `app/keeper_tools/registry.py` 集中 schema、handler 與能力旗標，但工具*執行後的結果*仍由 `app/agents/executor.py`、`app/services/turn_resolution.py` 和 `app/services/turn_delivery.py` 各自辨識。新增 `initialize_combat` 時，暫緩戰鬥的驗證與公開觀察都須另外修改；PR #148 review 修掉兩處遺漏。目標是建立深模組，從已驗證的工具事件產生一致、有限的交接事實。

## 範圍與介面

- 盤點每個可變更狀態的已註冊工具及其結果形狀、完成語意、後續需求和安全公開資訊。唯有現有消費端依賴時，才納入唯讀骰子與查詢結果。按工具家族搬移並做等價測試，不長期保留兩套清單。
- 共用解讀放在獨立模組，不塞入 `ToolSpec`。輸入是真實工具事件與可用的權威前後狀態；輸出區分已驗證效果、未完成或失敗效果、可能的戰鬥建立，以及可安全觀察的事實。工具名稱或 `ok=true` 本身都不能證明狀態已變更。
- `turn_resolution` 繼續以最新狀態、依據引用、角色與時間線身分、待處理檢定／Luck 優先序和嚴格的 `_setup_only` 快照比較做最後證明。第一輪可以建立戰鬥與登記戰鬥員；不能藉此放行傷害、推進既有回合或其他變更。
- `turn_delivery` 繼續負責受眾和私密投影。共用模組不得洩漏敵人角色卡、能力細節、私人線索、原始 RAG、內部錯誤或未過濾參數。KP 助手權限仍由註冊表與 gateway 管理。
- 新的可變更狀態工具若尚未分類，完成裁決和公開觀察採保守處理；完整性測試要求每個此類工具都有明確語意，於部署前抓出遺漏。

## 資料與相容性

不計畫變更資料庫、工具 schema、prompt 或 provider 協定。解讀結果是內部型別，不取代原始收據持久資料。保留工具順序與 `tool:N` 依據身分。除非證實目前文字會洩漏資料，玩家看到的用語維持等價。

## 流程

```text
Keeper 工具 → 真實收據＋權威狀態快照
            → 工具結果語意模組 → 已驗證且有限的事實
            → turn_resolution：對最新狀態做整回合證明
            → turn_delivery：受眾／私密投影
            → Narrator 交接
```

## 非目標

不把工具執行或狀態變更搬入本模組；不以註冊表能力旗標代替完成證明；不合併發話角色授權；不放寬劇本正典檢查；不增加 LLM 審稿呼叫；不在此實作 Discord 通用可靠傳送。

## 驗證

1. 鎖定各家族目前的結果行為，涵蓋成功變更、無變更、工具失敗、缺欄位、過期快照與私人結果。
2. 驗證 `initialize_combat`、`start_combat`、`add_npc_to_combat` 可以建立第一輪戰鬥；暫緩裁決須拒絕傷害、推進回合及不相關變更。
3. 確認註冊表內沒有未分類的可變更狀態工具；未知結果不得公開或證明完成。
4. 保持 `tests/test_tool_gateway_speaker_role.py`、`tests/test_keeper_tool_registry.py`、`tests/test_initialize_combat.py`、`tests/test_turn_consistency_handoff.py` 通過。實作後執行完整 pytest、Ruff 0.16.8、`mypy app` 和 `python -m compileall app tests`。

## 取捨與審查決定

深模組擁有共用的工具事件意義，整回合證明與受眾政策仍由現有擁有者負責。這不只是把工具名稱集合搬進薄 helper：若刪掉模組，語意知識應重新散回 Executor、resolution 和 delivery。Marco 已確認獨立分支、全量盤點後按家族搬移、保守預設，以及權限／私密分界。規格審查前不開始實作。
