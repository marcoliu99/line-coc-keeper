# 拆分 `app/discord_bot.py`

[English](discord_transport_split_design_spec.md)

狀態：**implemented**。基準：`main_v2` 於 `27a53e2`。

## 問題

`app/discord_bot.py` 有 1,862 行，混了四種責任：事件處理、對 Discord 的遞送、持久化的 Check／Luck／PDF 按鈕，以及 Help／sudo／來源就緒的 View、Modal 與 Select。改一個 Help 的 View 就得打開同時放著 `on_message` 的檔案。見 `docs/architecture/main_v2_architecture_review_zh.md`（F11）。

## 限制：持久化按鈕必須繼續有效

Check、Luck、PDF 選擇、Help 與 Help 執行按鈕都是 `discord.ui.DynamicItem`，由 `custom_id` 的正規表示式比對。部署前貼出的訊息在部署後仍必須能解析，所以範本與程式寫出的 id 完全不變，並由 `tests/test_discord_custom_id_contract.py`（前一個變更加入）釘住。行為、文字與時序都沒有改，這是搬移。

## 配置

| 模組 | 內容 |
| --- | --- |
| `app/discord_bot.py` | 入口：`on_ready`、備份迴圈、輸入中提示、`on_message`、`_handle_message`、`main` |
| `app/discord_transport/gateway.py` | 唯一的 `discord.Client` 與 intents |
| `app/discord_transport/delivery.py` | 訊息切段、私訊、互動回覆、請求指標、`discord_operation`（有逾時界線的 Discord 呼叫）|
| `app/discord_transport/interactions.py` | 頻道的 conversation id 與權限用的伺服器資訊（不送出任何東西，所以其他模組都可以 import 它）|
| `app/discord_transport/lifecycle.py` | `observed_interaction`，按鈕／互動回呼外面的請求生命週期包裝 |
| `app/discord_transport/controls.py` | Check、Luck、PDF 選擇按鈕與張貼它們的程式 |
| `app/discord_transport/help_ui.py` | Help 頁面、Help／sudo／來源就緒的 View、Modal 與 Select |

跨模組使用的名稱改為公開（`_make_reply` → `delivery.make_reply`）；`_conversation_id` 改為 `interactions.channel_conversation_id`，因為 `conversation_id` 到處都是區域變數。原本 patch `discord_bot.<名稱>` 的測試改為 patch 程式實際所在的模組；為了操控 Help 回呼而 patch `discord_bot.load_group_state` 的測試改 patch `help_ui.load_group_state`。

## 分層

`gateway` < `interactions` < `delivery` < `lifecycle` < `controls` < `help_ui`：每個模組在模組層級只 import 比自己低的。唯一向上的邊是真實存在的：`help_ui` 透過 `delivery.make_reply`／`make_interaction_reply` 派送指令，而這兩個轉接器在回覆是「來源就緒」訊息時要附上 `help_ui.SourceReadyView`。所以 `delivery` 在這兩個函式裡才 import `help_ui`，不是載入時。這個拆分的第一版曾在模組層級 import 它，導致單獨 `import app.discord_transport.interactions` 會失敗，只是因為機器人正常啟動時剛好先 import `controls` 才被掩蓋；`tests/test_architecture_discord_transport.py` 現在檢查這個順序，並在全新的直譯器中逐一 import 每個模組。

## 驗證

`ruff check .`、`mypy app` 與完整 `pytest` 通過。`tests/test_button_routing.py` 仍斷言 Discord 層只從 `app.commands.types` 匯入型別，掃描範圍擴大為 `discord_bot.py` 與 `discord_transport/` 內的所有模組。
