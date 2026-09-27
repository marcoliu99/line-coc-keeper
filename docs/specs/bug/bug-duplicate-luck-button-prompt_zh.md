# 檢定與 Luck 按鈕的冪等交付

[English](bug-duplicate-luck-button-prompt.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 以檢定／決定識別與時間線區分新提示和已送提示；同頻道並行請求不得送出重複有效按鈕。

2. 持有回合鎖時宣告交付所有權，在耗時 Discord 網路傳送前釋放鎖；失敗或遺留的宣告必須可恢復。

3. 舊按鈕識別採保守驗證；縮短 custom ID 以符合 Discord 上限，但 callback 仍比較完整持久化識別與目前時間線。

## 流程與介面

```text
結算 -> 認領持久化的按鈕識別 -> 傳送 -> 完成／復原
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/discord_bot.py](../../../app/discord_bot.py)
- [app/check_identity.py](../../../app/check_identity.py)
- [tests/test_logging_completion.py](../../../tests/test_logging_completion.py)
- [tests/test_pending_button_latency.py](../../../tests/test_pending_button_latency.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/bug-duplicate-luck-button-prompt.md)
