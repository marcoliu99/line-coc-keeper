# Project Review Fixes Design Spec

## Scope

本次修正針對全專案 review 中已確認、且不涉及重新設計 `/coc usepregen` `game_started` guard 的問題：

- scenario 切換時不得留下與舊劇本不一致的 pending pregen LUCK 狀態。
- scenario lifecycle 操作不得在 pending PDF／相似劇本流程未完成時互相覆蓋。
- PDF import／merge／reparse 不得在同一個 asyncio conversation lock 內重入同一把不可重入的 lock。
- PDF 選擇、相似劇本處理與劇本庫清理必須由目前 KP Assistant 或 Discord Keeper role 授權。
- lifecycle script 測試的 fake process 必須符合 process identity 驗證契約，完整測試不可因測試替身失敗。
- Discord event loop 不直接執行 staged PDF 流程中的同步 state load。
- 對外錯誤訊息不暴露 exception 內容；詳細錯誤只進 log。
- 文件必須描述目前實作的 `/coc start` 必須先完成，並說明 scenario library 的 `scenario_text` 相容快照行為。

## Explicit non-goal

本次不修改 `game_started=True` 時 `/coc usepregen` 的既有 guard 判定；該流程依目前決策視為已處理，保留現有行為。

## Scenario switching rule

若目前有 `pending_pregen_luck`，`/coc scenario use`、PDF 解析／重新解析與劇本切換流程直接拒絕，要求相關玩家先完成 `/coc luck roll`。這比清除 pending 狀態安全，避免新劇本開始時被舊角色的 LUCK 流程永久阻塞。

若目前有 `pending_pdf_upload` 或 `pending_scenario_upload`，`/coc scenario use` 也直接拒絕，避免舊按鈕或舊相似劇本流程在新劇本選定後覆蓋目前狀態。

`scenario import`、`scenario merge`、`scenario reparse` 在 router 層不持有 conversation lock；`handle_pdf_upload` 只在短的 state commit 區段取得 lock，避免不可重入的 `asyncio.Lock` 發生 nested-lock deadlock。

`SCENARIO_LIFECYCLE_KP_ONLY` 控制 `/coc pdf new|fix`、`/coc scenario reparse|cancel|clean` 與 PDF choice button 的 authorization。預設為 `false`，讓初期同時負責角色與 KP Assistant 的使用者可以完成流程；設定為 `true` 後才要求目前 KP Assistant 或 Discord Keeper role。按鈕 callback 會先檢查頻道與策略，實際套用時再於 lock 內重新驗證。

`SCENARIO_LIFECYCLE_KP_ONLY` 未設定時必須視為 `false`，設定於 `.env` 或環境變數後需重新啟動 Bot 才生效。

## Runtime safety

- staged PDF attachment path 使用 `asyncio.to_thread(load_group_state, ...)`，避免 SQLite/JSON 反序列化阻塞 Discord gateway。
- `on_message` 對外只回傳固定錯誤提示，exception 維持完整 logging。

## Documentation alignment

- `/coc start` 是正式進入劇情的必要步驟；未 start 時一般文字不會交給 Keeper。
- scenario library 目前仍將選定劇本文字保存於 `GroupState.scenario_text` 作為相容快照；啟用 Scenario RAG 時再由 RAG path 控制 prompt 取用範圍。

## Testing plan

- lifecycle test 使用仍保留 `app.discord_bot` command token 的 Python fake process。
- scenario switch with pending pregen luck 必須拒絕且不改變目前 scenario/pregen state。
- scenario switch with pending PDF state 必須拒絕且保留 pending state。
- import／merge／reparse 的 router flow 不得重入 conversation lock。
- 非 KP／Keeper 不得透過文字指令或 PDF button 套用、取消或清理 scenario lifecycle state。
- staged PDF path 測試同步 load 改為 thread dispatch。
- error handler 測試不把 exception 內容送到 channel。
