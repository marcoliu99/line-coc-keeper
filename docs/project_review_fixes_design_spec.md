# Project Review Fixes Design Spec

## Scope

本次修正針對全專案 review 中已確認、且不涉及重新設計 `/coc usepregen` `game_started` guard 的問題：

- scenario 切換時不得留下與舊劇本不一致的 pending pregen LUCK 狀態。
- lifecycle script 測試的 fake process 必須符合 process identity 驗證契約，完整測試不可因測試替身失敗。
- Discord event loop 不直接執行 staged PDF 流程中的同步 state load。
- 對外錯誤訊息不暴露 exception 內容；詳細錯誤只進 log。
- 文件必須描述目前實作的 `/coc start` 必須先完成，並說明 scenario library 的 `scenario_text` 相容快照行為。

## Explicit non-goal

本次不修改 `game_started=True` 時 `/coc usepregen` 的既有 guard 判定；該流程依目前決策視為已處理，保留現有行為。

## Scenario switching rule

若目前有 `pending_pregen_luck`，`/coc scenario use` 直接拒絕並要求相關玩家先完成 `/coc luck roll`。這比清除 pending 狀態安全，避免新劇本開始時被舊角色的 LUCK 流程永久阻塞。

## Runtime safety

- staged PDF attachment path 使用 `asyncio.to_thread(load_group_state, ...)`，避免 SQLite/JSON 反序列化阻塞 Discord gateway。
- `on_message` 對外只回傳固定錯誤提示，exception 維持完整 logging。

## Documentation alignment

- `/coc start` 是正式進入劇情的必要步驟；未 start 時一般文字不會交給 Keeper。
- scenario library 目前仍將選定劇本文字保存於 `GroupState.scenario_text` 作為相容快照；啟用 Scenario RAG 時再由 RAG path 控制 prompt 取用範圍。

## Testing plan

- lifecycle test 使用仍保留 `app.discord_bot` command token 的 Python fake process。
- scenario switch with pending pregen luck 必須拒絕且不改變目前 scenario/pregen state。
- staged PDF path 測試同步 load 改為 thread dispatch。
- error handler 測試不把 exception 內容送到 channel。
