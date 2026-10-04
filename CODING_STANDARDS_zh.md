# 程式碼規範

這裡只收錄工具檢查不了的規則。其餘由 ruff、mypy、pytest 負責：設定分別在 `pyproject.toml`、`mypy.ini`、`pytest.ini`，每個 PR 由 `.github/workflows/ci.yml` 執行。各模組的職責見 README.md 的 `## Architecture`。

每條規則都寫出目標行為和理由。既有程式碼不符合規則時，在下次修改那一段時順手修正；不要讓差距擴大。

## 分層

**Discord 事件一律經過 command router。** `discord_bot.py` 負責轉換 Discord 事件（訊息、上傳、按鈕回呼），交給 `app/commands/router.py`，再由它分派到 `app/commands/handlers/*`。
_理由：_ KP／sudo 權限檢查和回合路由都在 router 這層；直接呼叫 `scenario_ingestion` 這類 service 的入口會繞過它們。

**Handler 只負責解析和回覆，遊戲規則放在規則模組。** 戰鬥、檢定登記和存檔點分別放在 `combat.py`、`keeper.py`、`checkpoints.py`。Handler 呼叫這些模組，不自己複製一份防護邏輯。
_理由：_ 複製出來的防護邏輯會慢慢不一致。開戰存檔點和敵人重複加入的防護，曾經被複製進 `/coc combat` handler，副本組 `event_id` 時用的還是另一個欄位；現在兩者都在 `combat.py`（`begin_combat`、`add_combatant`）。

**私有就是私有。** 底線開頭的名稱只在自己的模組內使用。其他模組需要時，先在原模組提供一個不帶底線的公開名稱再呼叫。由 ruff SLF001 自動檢查，`pyproject.toml` 列出了既有的違規作為待清理項目。
_理由：_ `keeper._build_static_prompt`、`_mutate_and_save_state` 等函式被其他模組直接呼叫，重構 `keeper.py` 時影響會波及整個 repo。

**LLM 供應商只從一個地方取。** 透過 `app/providers/` 中的單一函式取得目前使用的供應商。供應商特有的分支邏輯放在各自的 provider 類別裡。
_理由：_ `anthropic`／`gemini`／`openai` 的對照表目前複製在 9 個模組，每次調整供應商都要全部改一遍。

**Keeper 工具寫成獨立函式。** 每個新工具的邏輯寫成自己的函式，`_execute_tool` 只依工具名稱分派。
_理由：_ `_execute_tool` 已經是約 1,180 行的 `if name == ...` 分支，每多一個分支，審查和測試都更難。

## 遊戲狀態

**所有遊戲狀態寫入都走 `state_transaction`。** 用 `state_transaction.mutate`（對最新資料列套用 delta）改狀態；若變更是在已載入的快照上算出、無法寫成 delta，就用 `commit_snapshot`。可能被重送的操作，要由擁有它的程式碼給 `action_id`（turn id、check id、event id），不可由文字推導。必須與狀態同時落地的其他資料表，透過 `ctx.conn` 寫入；mutation 內不可再開第二個交易。
_理由：_ 在鎖外載入的寫入者不是遺失更新，就是以 revision 衝突失敗，而且沒有任何東西能分辨重試與新操作。`tests/test_architecture_state_writes.py` 會拒絕直接使用 `save_state`、`write_state_tx` 或對遊戲狀態資料表的 `db.set_json*`。

**檢定規則只在 `app/checks` 裡寫一次。** 指令、按鈕與 Keeper 工具都呼叫 `checks.service`；它們只負責解析輸入與格式化回覆。骰子經由 `DicePort` 進來，誰能花 Luck 由 `checks.luck` 決定，已結算的檢定以 `checks.events.persist_resolved_event`（一個 `check-event:<event_id>` action）記錄。不要把等級文字、Luck 處理或 SAN → INT 串接複製進 handler。
_理由：_ 同一個檢定過去會經過三份各自維護的規則。`tests/test_architecture_checks.py` 讓引擎不匯入傳輸層、Keeper、provider 與戰鬥程式碼，已刪除的重複碼回來時也會失敗。規格：`docs/specs/refactor/check_engine_design_spec_zh.md`。

**戰鬥 action 一律走 `CombatEngine`。** 指令、工具與轉接層呼叫 `combat_engine.handle(state, action)`；只有引擎讀取戰鬥模式，`combat` 絕不匯入 `combat_flow`。legacy 與 managed 戰鬥規則不同的地方，接收一個 `ModeOps` 參數，而不是自己判斷模式；新增 action 就是在 `combat_actions` 加一個 dataclass 與一個 handler。可重複的 action 帶上其 ledger 所用的穩定 `action_id`／`event_id`。
_理由：_ 兩個戰鬥模組曾互相匯入，並在 15 個地方反覆詢問「是不是 managed」，所以對沒有戰鬥的對話呼叫只屬於 managed 的操作，會憑空造出一場戰鬥。`tests/test_architecture_combat.py` 在匯入圖上檢查分層，含延遲匯入。規格：`docs/specs/refactor/combat_engine_design_spec_zh.md`。

**劇本一律經由 `scenario_admission` 載入。** 入口（PDF 或 Markdown 上傳、全新／修正選擇、`/coc scenario use`）只負責解析、檢查權限、抽取與組回覆；安裝章節、退場舊角色卡、安裝新卡池、提交、發布頁面圖片，都是 `scenario_admission` 的事（`admit_upload`、`activate_pending_choice`、`activate_selected`）。不要在入口裡呼叫 `commit_and_refresh` 或 `install_context_fields`。
_理由：_ PDF 入口、Markdown 入口和 `/coc scenario use` 各自重複了待處理狀態檢查、內容雜湊查詢，以及安裝、提交、發布圖片的順序，順序要改就得改三次。`tests/test_architecture_scenario_admission.py` 會在入口又長出自己的副本時失敗。規格：`docs/specs/refactor/scenario_admission_design_spec_zh.md`。

**每個新的待處理檢定都要經過歸屬檢查。** 登記技能、SAN 或 CON 檢定前，在 `_mutate_and_save_state` 內、針對重新載入的狀態，同時檢查 `pending_checks` 和 `pending_luck_decisions`（參考 `_reject_if_check_already_pending`）。被擋下時要讓玩家或模型知道。
_理由：_ 無聲返回的檢定會讓規則後果直接消失。規格：`docs/specs/bug/bugfix_duplicate_pending_checks.md`。

**固定的值集合用型別表示。** `speaker_role`、檢定類型這類固定值用 `Literal` 或 `Enum`，不用裸字串。
_理由：_ 這樣 mypy 才能抓到錯字和漏掉的分支。

## 註解與文件

**註解引用的程式必須存在。** 改名或刪除函式、檔案時，用 `grep` 搜尋它的名字，更新引用它的註解。
_理由：_ 目前有註解還在描述已移除的 `keeper.run_turn` 路徑，以及已改名的 `app/commands.py`／`app/state.py`，會誤導人和 agent。

**行為變更要和規格一起提交。** 在同一個 PR 裡更新 `docs/specs/<category>/` 下的規格和它的 `_zh` 版本，並新增或更新 `docs/specs/catalog.json` 中的條目。
_理由：_ 目前有 5 份規格沒登錄進 catalog，另有 2 筆條目已經落後於規格內容。
