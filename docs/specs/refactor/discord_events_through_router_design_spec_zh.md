# 所有 Discord 事件一律經過 command router

[English](discord_events_through_router_design_spec.md)

狀態：**partial**：第 1–3 步已完成，第 4–5 步尚未開始。基準：`main_v2` 的 `a68df95`。

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
2. **上傳**（風險最低）：新增 `router.handle_upload`，分派到 `handlers/uploads.py`，並把 PDF 分段暫存的狀態變更移出 `discord_bot.py`。一個 PR。*已完成：* `router.handle_uploads`／`handle_unsupported_attachment`、`handlers/uploads.py`（`Upload(filename, read)`），並修正盤點發現 2：暫存前先檢查佔用狀態，什麼都不寫；暫存途中才開始佔用時，保留可能被其他對話或待決選擇引用的內容定址檔案，並回覆佔用通知。
3. **按鈕：** 新增兩個按鈕入口，把認領和排序邏輯從 `discord_bot.py` 搬到 router，完全保留 `acquire_legacy_for_keeper` 的語意。一個 PR，以既有的按鈕／幸運值競態測試作為關卡。 *已完成：* `router.handle_check_button`／`handle_luck_button` 執行 `handlers/buttons.py`，順序和原本完全相同（擁有者、防重複送出、回應 Discord、在鎖內驗證並執行、解鎖前認領、解鎖後補貼按鈕）。認領和身分驗證搬到 `app/services/pending_buttons.py`，文字路徑也共用；`discord_bot.py` 只保留 Discord I/O，以 `ButtonIO` 傳入。競態測試的 **patch 對象**需要從 `discord_bot` 改到 `buttons`／`pending_buttons`，斷言不變。搬移時也發現一個靜默跳過：`test_state_loss_amnesia` 把 `ImportError` 當成「沒安裝 discord.py」，名稱不存在時身分測試會被跳過；現在直接從不依賴 discord 的 service 匯入，一定會執行。
4. **權限：** *已由 `docs/specs/bug/keeper_role_superuser_design_spec.md` 取代。* 這裡原本「把 `keeper` 身分組相關函式改名成 `host`」的計畫，建立在錯誤的理解上。名為 `keeper` 的 Discord 身分組是機器人自己的，所以這個檢查是一條要移除的隱藏超級使用者路徑，而不是要改名的一種人。
5. 步驟 4 之後，`discord_bot.py` 從 `legacy_commands` 只匯入 `Reply`／`SendImage` 型別，並用測試斷言這一點。

## 第 1 步盤點（main_v2 的 `2a61269`）

各入口路徑目前實際得到的保護。「guard」指 handler 上的 `@mutation_admission.guard_async_entry`；文字路徑另外有 router 的 `is_held` 事先檢查。

| 路徑 | admission | 對話鎖 | 優先關卡 | 防重複送出 | 權限 | 排隊通知／router 觀測 |
| --- | --- | --- | --- | --- | --- | --- |
| 一般文字（基準） | router 事先檢查 | 有 | **有** | — | sudo 規則 | 有 |
| `/coc check`、`/coc luck` 文字 | router + guard | 有，附通知 | 無 | `try_acquire_check` | 擁有者 | 有 |
| 檢定按鈕（`discord_bot.py:687-710`） | guard | 有，無通知 | 無 | `try_acquire_check` | 點擊者須為擁有者 | 無（`check.button.*` 事件） |
| 幸運值按鈕（`:1086-1105`） | guard | 有，無通知 | 無 | `try_acquire_check` | 點擊者須為擁有者 | 無 |
| PDF 上傳（`:2188`、`legacy_commands.py:338`） | guard | handler 內上鎖兩次，中間做擷取 | 無 | 未決上傳檢查 | **無，刻意為之** | 無 |
| 多段 PDF 暫存（`discord_bot.py:2168-2176`） | **只在 repository 存檔時** | 有，在 `discord_bot.py` 內 | 無 | — | 無 | 無 |
| 地圖上傳（`:2202`） | guard | 有，在 handler 內 | 無 | — | 無 | 無 |
| 角色卡上傳（`:2232`） | guard | 有，在 handler 內 | 無 | — | 上傳者本人 | 無 |
| 劇本比對上傳（`:2241`） | guard | 無（唯讀） | 無 | — | 無 | 無 |
| PDF 選擇按鈕（`:1409`） | guard | 有，在 handler 內 | 無 | — | `_is_kp_or_keeper` | 無 |
| 不支援的附件（`:2249`） | 無（唯讀回覆） | 有 | 無 | — | — | 無 |

發現：

1. **只有一般文字會經過 Keeper 優先關卡。** 其他路徑都只靠對話鎖和 Keeper 回合排隊。這回答了待決問題：上傳目前實際上只有對話鎖。
2. **暫存路徑繞過了 admission 通知。** 它先暫存檔案再存檔；群組被佔住時，repository 的 `assert_admitted`（`repositories/group_state.py:101`）丟出 `MutationHeld`，落到通用錯誤處理：使用者看到「發生內部錯誤」而不是佔用通知，已暫存的檔案也成了孤兒。第 2 步應在暫存前先檢查 admission，並回覆通知。
3. **PDF 上傳沒有權限檢查，是刻意的**（Marco 確認）：即使 `SCENARIO_LIFECYCLE_KP_ONLY=true`，任何玩家都能上傳，第一次上傳會立即生效。第 4 步必須保留這一點；只有選擇按鈕和 `/coc scenario` 限定 KP／主辦人。
4. 按鈕在上鎖和防重複送出上與文字路徑一致；差別只在排隊通知和 router 觀測，這是 3 秒互動回應期限造成的刻意設計。

除了發現 2 是修正之外，第 2–4 步都是純搬移。

## 測試

- 每個步驟既有的測試不修改即通過（`tests/test_luck_buyup_gate.py`、檢定按鈕和上傳的測試、`tests/test_kp_sudo.py`）。
- 新增：依步驟 1 的盤點結果，每種事件遇到同一對話中正在進行的 Keeper 回合時，排序方式和文字管線相同。
- 新增：`app/discord_bot.py` 沒有從 `app.legacy_commands` 匯入任何可呼叫物件。

## 待審查決定的問題

- 上傳要經過 Keeper 優先關卡（可能讓上傳排在一個很長的回合後面），還是只要對話鎖。步驟 1 的盤點應該能看出它們目前實際得到的是哪一種。
- Discord 互動期限：按鈕必須在 3 秒內回應。router 層對按鈕做的任何排隊，都必須先回應，和目前的直接路徑一樣。
