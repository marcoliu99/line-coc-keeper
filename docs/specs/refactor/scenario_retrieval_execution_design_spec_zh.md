# 劇本檢索執行

[English](scenario_retrieval_execution_design_spec.md)

狀態：**backlog，須先通過下述深度門檻並完成規格審查**。基準：2026-09-29 的 `main_v2`，commit `7cef87c`。

## 問題與現況證據

`scenario_templates.search_for_state` 已共用授權後的中文／原文核心搜尋、章節範圍、依據不完整處理與續取綁定，不應再把這段搜尋抽一次。兩個遊戲呼叫端仍重複或分散請求編排：`app/agents/context_builder.py` 準備主動預取的身分、prompt 預算、context variables、檢索指標、只接受語意來源與格式化文字；`app/keeper_tools/scenario.py` 準備明確搜尋的來源、續取、身分、指標、完整性、紀錄 ID 和續取 token。`app/agents/executor.py` 另設明確搜尋的預算 context。第三個呼叫端是更正裁決，使用固定身分，須保留自己的依據政策。

目標是深模組，集中共用的請求身分、預算套用、診斷與依據完整性解讀，同時保留呼叫端的不同政策及已共用的搜尋實作。

## 實作前的深度門檻

先列出主動上下文、Keeper 明確搜尋工具、Executor 預算設定及更正裁決的呼叫點盤點表，分清真正重複和有意差異。只有一個小介面可以真正擁有**全部三項**共用知識——身分／續取綁定、預算／context 生命週期、正規化的結果完整性／診斷——且不要求呼叫端傳入整包 prompt 或龐大政策 dict 時，才新增模組。必須通過 deletion test：若刪掉它，重要知識會散回多個呼叫端。若盤點後只剩格式或指標的重複，應縮小或停止重構並報告原因，不新增薄轉發模組。

## 通過門檻後的範圍與介面

- 明確區分主動上下文與 Keeper 明確搜尋模式。模組取得當前授權狀態、查詢、呼叫者身分與必要預算輸入，按原政策呼叫既有 `search_for_state`，回傳對應模式的結構化依據、來源、完整性、診斷和續取資料。
- 集中 `scenario_retrieval.BUDGET` 和 `MODEL` context variables 的設定與還原。保留保守的 byte 後備和 `budget_tokens=0` 行為。計算預算仍納入現行 prompt、工具、provider 歷史、輸出保留量與安全餘裕，不增加 context 上限，也不默默丟棄必要事實。
- 保留續取與群組、時間線、版本、允許章節、來源、查詢及歷史的綁定。其他身分、改過時間線或過期來源的續取 token 必須維持失效。
- 主動上下文只接受成功的語意來源，並可跳過 prompt 中的詞面後備；明確搜尋可用 BM25、`source=original` 和有效續取。既有 `search_for_state` 的中文轉原文補查維持，包括已命中但護甲、攻擊、能力、觸發、花費或限制仍不完整的情況。
- 授權與 `complete_for_action` 必須保守。命中不等於依據完整；缺漏保留現有標記，讓 Executor 補查原文。維持日誌分界：自由文字查詢只在文字日誌，結構化事件只放有界診斷。
- 更正裁決保留目前依據路徑；除非呼叫點盤點證明共用相同身分和完整性規則，否則不強迫搬入，也不把更正報告變成遊戲回合預取。

## 資料與相容性

不計畫變更資料庫、模板 schema、來源紀錄、provider 或 prompt 政策。相同狀態、查詢和模式下，前後的結果文字、依據紀錄 ID、續取 token 和指標必須一致。新模組不在遊戲中翻譯查詢、不新增 LLM 呼叫，也不改章節範圍。

## 流程

```text
主動上下文政策 ──┐
                 ├─→ 檢索執行模組
明確搜尋政策 ────┘     → 請求身分＋限定預算＋診斷
                       → 既有 search_for_state
                       → 正規化依據／完整性結果
```

## 非目標

不重建索引、不改排序、不改原文後備、不合併主動／明確搜尋的接納政策、不在遊戲中自動翻譯，也不因程式碼搬移就宣稱加速。

## 驗證

1. 比對改動前後的原文版、完整中文版、中文零命中、中文局部不完整、敵人必要細節缺漏、續取失效、身分不符、章節更改及 tokenizer byte 後備。依據文字／順序、ID、完整性、來源標籤和續取行為須等價。
2. 驗證主動預取仍排除詞面後備、明確搜尋仍可用；必要依據不能默默遺漏。測預算歸零，以及例外和並發後 context variable 正確還原。
3. 同案例量測 LLM 呼叫、RAG 呼叫、預算、檢索耗時和完整回合耗時。不得新增 LLM 呼叫；只量測速度，不預設會改善。
4. 維持 `tests/test_retrieval_prefetch.py`、`tests/test_retrieval_readiness.py`、`tests/test_scenario_query_fallback.py`、`tests/test_scenario_template_units.py` 通過。實作後跑完整 pytest、Ruff 0.16.8、`mypy app` 和 `python -m compileall app tests`。

## 審查決定

Marco 已確認獨立分支、保留後備差異、輸出等價、零新增 LLM 呼叫及真正的深度門檻。既有共用搜尋應保留；不能因已共用就再包一層。呼叫點盤點通過門檻且規格審查後，才開始實作。
