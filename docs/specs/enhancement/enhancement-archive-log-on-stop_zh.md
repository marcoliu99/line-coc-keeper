# 停止 Bot 後封存 log 與 profile

[English](enhancement-archive-log-on-stop.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 生命週期停止流程將設定的 runtime log 與可選 profiler 輸出封存到設定目的地；停止前程序必須符合記錄的 Bot 識別。

2. 封存實際結構化／文字 log 與 profiler 輸出，不把無關檔案當成本次程序產物；部分失敗時保留有用診斷。

3. Profiling 為選用；這是維運檔案處理，不增加每回合模型請求，也不重設正式遊戲資料。

## 流程與介面

```text
驗證程序身分 -> 正常停止 -> 封存 log／profile -> 回報路徑
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [scripts/stop_bot.sh](../../../scripts/stop_bot.sh)
- [scripts/start_bot.sh](../../../scripts/start_bot.sh)
- [tests/test_bot_lifecycle_scripts.py](../../../tests/test_bot_lifecycle_scripts.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-archive-log-on-stop.md)
