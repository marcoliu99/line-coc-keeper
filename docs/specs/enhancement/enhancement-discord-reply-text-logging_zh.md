# 記錄實際交付的 Discord 回覆

[English](enhancement-discord-reply-text-logging.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 透過文字 log 記錄公開回覆，以便把事故對上玩家所見內容；LOG_TEXT_ENABLED 與結構化 metrics 分開控制。

2. 交付狀態反映實際傳送結果，包含失敗與分段訊息；生成完成不代表未送出的回覆已交付。

3. 結構化效能事件維持受限欄位與識別政策；自由格式玩家／劇本文字只進獨立設定的文字 log。

## 流程與介面

```text
渲染／分割回覆 -> 成功傳送 -> 文字日誌與傳送指標
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/discord_bot.py](../../../app/discord_bot.py)
- [app/logging_config.py](../../../app/logging_config.py)
- [tests/test_discord_reply_text_log.py](../../../tests/test_discord_reply_text_log.py)
- [tests/test_logging_completion.py](../../../tests/test_logging_completion.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-discord-reply-text-logging.md)
