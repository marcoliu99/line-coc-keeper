# 本地 Discord Bot 生命週期

[English](bot_lifecycle_scripts_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`feature`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 腳本管理 Discord 模組程序與 runtime 檔案；驗證執行檔／模組／程序識別，不能只信 PID 檔。

2. 啟動須區分已執行 Bot 與過期 PID；停止只針對已驗證 Bot，先給正常結束時間再有限度升級處理。

3. Profiling 可選；停止時封存 runtime log 與 profile，status 回報真實程序狀態與有用路徑。

4. 資料清理是獨立明確操作，不能成為啟停意外副作用；測試假程序須遵守相同識別契約。

## 流程與介面

```text
啟動 -> 記錄身分／log 路徑 -> 查詢狀態 -> 驗證身分後停止 -> 封存
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [scripts/start_bot.sh](../../../scripts/start_bot.sh)
- [scripts/stop_bot.sh](../../../scripts/stop_bot.sh)
- [scripts/bot_status.sh](../../../scripts/bot_status.sh)
- [tests/test_bot_lifecycle_scripts.py](../../../tests/test_bot_lifecycle_scripts.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/bot_lifecycle_scripts_design_spec.md)
