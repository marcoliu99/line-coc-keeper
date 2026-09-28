# 名為 `keeper` 的 Discord 身分組是隱藏的超級使用者

[English](keeper_role_superuser_design_spec.md)

狀態：**backlog**（等待規格審查）。基準：`refactor/discord-buttons-through-router` 的 `5106d47`（建立在 `main_v2` 的 `83e0c54` 之上）。取代 `docs/specs/refactor/discord_events_through_router_design_spec.md` 的第 4 步。

## 問題

`app/discord_bot.py:402` 的 `_is_keeper_member`，只要成員在任何伺服器上擁有一個**名稱**為 `keeper` 的身分組（不分大小寫）就回傳 true。這個旗標 `is_keeper` 會給予和群組登記的 KP 助手相同的權限，而且是在該伺服器的**所有**群組，不需要任何登記。

在我們的部署上，`keeper` 身分組屬於**機器人自己**（Marco 確認）。機器人會忽略機器人帳號的訊息（`on_message` 遇到 `message.author.bot` 直接返回），機器人帳號也不能按按鈕，所以這個旗標對正當使用者永遠不成立。因此這條分支：

- **對原本的用途來說是死碼：** 沒有真人應該擁有它；
- **卻是一條實際存在的超級使用者路徑：** 任何能管理身分組的人，只要把某個身分組取名為 `keeper`，不論有意或無意，就能讓任何成員在所有群組擁有完整的 KP 權限；
- **在介面上會誤導人：** 約 18 則訊息寫著「只有目前的 KP Assistant 或 Discord Keeper 可以……」，暗示有另一種並不存在的人。

## 這個旗標目前給予的權限

來源（傳輸層）：

| 來源 | 位置 |
| --- | --- |
| 一般訊息 | `discord_bot.py:1937` → `router.handle_text_message(is_keeper=…)` |
| 說明的執行按鈕 | `discord_bot.py:1332` → `handle_text_message` |
| PDF 選擇按鈕 | `discord_bot.py:1145`（檢查）、`:1156` → `resolve_pdf_upload_choice(is_keeper=…)` |
| 來源準備完成按鈕 | `discord_bot.py:1257` |

它解鎖的動作。**一律檢查**，不論設定：

| 動作 | 位置 |
| --- | --- |
| `/coc sudo`：代替其他玩家操作 | `router.py:221` |
| 移動**其他玩家**的調查員（透過 sudo 回合） | `router.py:190/257/326` → `supervisor.py:192` → `executor.py:86` → `movement.py:385` |
| 建立、查看、回溯回溯節點 | `handlers/system.py:157` |
| 查看場景摘要 | `handlers/system.py:213` |
| 管理英文來源、模板、手動角色卡；選擇劇本 | `handlers/system.py:260`、`:313`、`:366`、`:506` |
| 查看 `/coc index` 完整數值 | `handlers/system.py:795` |
| 核准敘事修正 | `handlers/correct.py:79` |
| 來源準備完成按鈕 | `discord_bot.py:1257` |

**只在 `SCENARIO_LIFECYCLE_KP_ONLY=true` 時檢查**（`legacy_commands._is_kp_or_keeper`），否則人人都能做：

| 動作 | 位置 |
| --- | --- |
| 重新解析、取消、清理劇本庫；`/coc pdf` | `handlers/system.py:435/448`、`:493`、`:605`、`:647` |
| PDF 選擇按鈕 | `legacy_commands.py:631`、`discord_bot.py:1145` |

它**不會**改變 AI 回合：擁有這個身分組的人一般聊天時，仍以玩家身分執行，也沒有 KP 優先順序。`speaker_role="keeper"` 只出現在排隊日誌的標籤上（`router.py`，10 處）。

## 決策

KP 權限只有**一個**來源：成為群組登記的 KP 助手（`state.kp_assistant_user_id`）。任何 Discord 身分組都不給予 KP 權限。

- 新增公開模組 `app/commands/permissions.py`：
  - `is_kp(state, user_id) -> bool`：是否為登記的 KP 助手；
  - `may_manage_scenario_lifecycle(state, user_id) -> bool`：`not SCENARIO_LIFECYCLE_KP_ONLY or is_kp(...)`。
- 上表每一處都改用這兩個函式之一。**完全保留**「一律檢查」和「只在設定開啟時檢查」的區分；這份規格改變的是「誰算 KP」，不是「哪些動作需要 KP」。
- 移除 `_is_keeper_member`、一路傳遞的 `is_keeper`／`actor_is_keeper` 參數，以及 `legacy_commands._is_kp_or_keeper`。移動授權改為「行動者就是目標本人，或是 KP 助手」。
- 排隊日誌的 `speaker_role` 只剩 `kp_assistant`／`player`。
- 訊息改為「只有目前的 KP 助手可以……」。這也修正了回溯節點和場景摘要那兩則原本只寫 KP Assistant 的訊息。
- `/coc help` 的說明、`docs/references/player_command_reference*.md`，以及 `.env.example`、`config.py`、`models.py` 的註解，拿掉「Discord Keeper」。歷史規格和 changelog 維持原樣。
- `CONTEXT.md`：刪除描述一種不存在的人的 **Host** 詞條，並記錄伺服器上的 `keeper` 身分組是機器人自己的，不給予任何權限。

## 安全措施：這是在移除一條超級使用者路徑

1. **可觀察的過渡期。** 在一個版本內，當**非機器人**、且擁有名為 `keeper` 身分組的成員，嘗試做這個身分組原本允許的動作時，記錄 `authz.keeper_role_ignored`（動作、雜湊後的使用者 ID；不記錄身分組清單和訊息內容），然後和其他人一樣拒絕。偵測用的函式只放在傳輸層，等日誌確認沒有觸發後，在後續 PR 刪除。
2. **部署前檢查（手動）。** 在每個伺服器確認沒有真人擁有名為 `keeper` 的身分組。如果有，先在他帶的群組把他登記為 KP 助手再部署；否則他會在那些群組失去 sudo、回溯節點和回溯的權限。
3. **退回方式。** revert 這個 PR 即可。沒有狀態或 schema 變更，雙向都不需要遷移。
4. **以安全變更的標準審查。** PR 會列出上表的每一處，以及固定它行為的測試。

## 測試

上面兩張表的**每一個**動作，都用三種呼叫者測試：

| 呼叫者 | 預期 |
| --- | --- |
| 登記的 KP 助手 | 允許（不變） |
| 不是 KP 的成員（原本 `is_keeper=True`） | **拒絕**，並回覆 KP 限定訊息 |
| 一般玩家 | 和現在一樣拒絕；「只在設定開啟時檢查」的動作在 `SCENARIO_LIFECYCLE_KP_ONLY=false` 時，和現在一樣允許 |

另外：

- sudo：不是 KP 的成員不能代替其他玩家；KP 發起的 sudo 回合仍能移動目標玩家的調查員（`movement.py`）。
- 移動：既不是目標本人、也不是 KP 的行動者，會被以 `movement_actor_not_authorized` 拒絕。
- 過渡期日誌會對擁有身分組的真人觸發，對機器人和沒有這個身分組的成員永遠不觸發。
- 用 AST 檢查 `app/` 裡沒有任何模組引用 `is_keeper`、`actor_is_keeper` 或 `_is_keeper_member`，授權判斷裡也沒有出現任何 Discord 身分組名稱。
- 既有測試裡以 `is_keeper=True` 授權呼叫者的（`test_scenario_authoring.py`、`test_scenario_source_authoring.py`、`test_state_persistence.py`），改成把該呼叫者登記為 KP 助手，關於動作本身的斷言不變。`test_state_persistence.py:246`（`_is_kp_or_keeper(state, "player", True)` 為真）的斷言要反過來，因為那正是這份規格要移除的權限。

## 限制

- 如果伺服器擁有者想讓某位真人在所有群組都擁有「超級 KP」權限，這份規格沒有提供替代方案。如果真的有這個需求，應該是在設定裡**以使用者或身分組的 ID** 明確、自願開啟的授權，預設關閉，而且絕不能用身分組名稱判斷。那是另一份規格。
