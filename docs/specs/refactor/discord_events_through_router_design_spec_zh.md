# 所有 Discord 事件一律經過 command router

[English](discord_events_through_router_design_spec.md)

狀態：**backlog**（等待規格審查，尚未實作）。基準：`main_v2` 的 `a68df95`。

## 問題

README 的 `## Architecture` 描述的是單一管線：`discord_bot.py` 轉換 Discord 事件，交給 command router（`app/commands/router.py`），再由它分派到 `app/commands/handlers/*`。實際上只有**文字訊息**走這條路（`command_router.handle_text_message`，`app/discord_bot.py:1593`、`:2284`）。其他事件都直接呼叫 `legacy_commands`（匯入在 `app/discord_bot.py:53-65`）：

| 事件 | 直接呼叫 | 位置 |
| --- | --- | --- |
| 劇本 PDF 上傳 | `handle_pdf_upload` | `app/discord_bot.py:2188` |
| 多段 PDF 暫存 | **在 `discord_bot.py` 裡**，於 `locks.get_conversation_lock` 下載入狀態、修改 `staged_pdf_parts` 並存檔 | `app/discord_bot.py:2169-2175` |
| 地圖圖片上傳 | `handle_map_upload` | `:2202` |
| 角色卡上傳 | `handle_role_sheet_upload` | `:2232` |
| 劇本比對上傳 | `handle_scenario_compare_upload` | `:2241` |
| 檢定按鈕 | `handle_check_command(…, acquire_legacy_for_keeper=False)` | `:754` |
| 幸運值按鈕 | `handle_luck_decision` | `:1134` |
| KP 權限檢查 | `_is_kp_or_keeper`（私有函式） | `:1409` |

文字管線的 sudo 處理、Keeper 優先關卡、附排隊通知的對話鎖和路由觀測，都在 router 這層。直接呼叫的路徑則各自拼湊其中一部分保護。有些是刻意設計的：按鈕會傳 `acquire_legacy_for_keeper=False`，並在自己的鎖下認領待處理按鈕。但任何一條路徑是否和文字管線**等價**，只能逐條閱讀才知道。`discord_bot.py`（2,375 行）裡還有狀態變更邏輯（PDF 暫存）和私有權限函式，這些都屬於它下面的層。

## 目標

`discord_bot.py` 只負責轉換：把 Discord 事件變成帶有型別化資料的 router 呼叫。**每一種**事件的順序、鎖、權限和觀測都由 router 負責，行為則由 handler 負責。

```python
# app/commands/router.py
async def handle_upload(conversation_id, user_id, upload: Upload, reply, send_image) -> None
async def handle_check_button(conversation_id, owner_id, choice: CheckChoice, io: ButtonIO) -> None
async def handle_luck_button(conversation_id, owner_id, decision: LuckChoice, io: ButtonIO) -> None
```

## 計畫

1. **先盤點，不改程式。** 針對上表每一列，記錄它目前實際得到哪些保護：對話鎖、Keeper 優先關卡、mutation admission、sudo，以及能否和 Keeper 回合交錯執行。結果補進這份規格。它決定每次遷移只是單純搬移，還是同時要修正；進入步驟 2 之前要再審查一次。
2. **上傳**（風險最低）：新增 `router.handle_upload`，分派到 `handlers/uploads.py`，並把 PDF 分段暫存的狀態變更移出 `discord_bot.py`。一個 PR。
3. **按鈕：** 新增兩個按鈕入口，把認領和排序邏輯從 `discord_bot.py` 搬到 router，完全保留 `acquire_legacy_for_keeper` 的語意。一個 PR，以既有的按鈕／幸運值競態測試作為關卡。
4. **權限：** 把 `_is_kp_or_keeper` 換成 router 或 `commands/sudo.py` 裡的公開函式，名稱依照術語表（`CONTEXT.md`）：真人是 **KP**，伺服器管理員是**主辦人**（Host），「Keeper」只指 AI。把 `_is_keeper_member`／`is_keeper` 改名為 `_is_host_member`／`is_host`，輔助函式改名為 `can_administer_group`（或 `_is_kp_or_host`），使用者看到的「KP Assistant 或 Discord Keeper」改成「KP 或主辦人」。Discord 身分組的**名稱**維持 `keeper`，已部署的伺服器不用改；身分組檢查旁加一行註解說明這點。
5. 步驟 4 之後，`discord_bot.py` 從 `legacy_commands` 只匯入 `Reply`／`SendImage` 型別，並用測試斷言這一點。

## 測試

- 每個步驟既有的測試不修改即通過（`tests/test_luck_buyup_gate.py`、檢定按鈕和上傳的測試、`tests/test_kp_sudo.py`）。
- 新增：依步驟 1 的盤點結果，每種事件遇到同一對話中正在進行的 Keeper 回合時，排序方式和文字管線相同。
- 新增：`app/discord_bot.py` 沒有從 `app.legacy_commands` 匯入任何可呼叫物件。

## 待審查決定的問題

- 上傳要經過 Keeper 優先關卡（可能讓上傳排在一個很長的回合後面），還是只要對話鎖。步驟 1 的盤點應該能看出它們目前實際得到的是哪一種。
- Discord 互動期限：按鈕必須在 3 秒內回應。router 層對按鈕做的任何排隊，都必須先回應，和目前的直接路徑一樣。
