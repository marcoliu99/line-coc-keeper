# 待處理按鈕交付與劇本檢索延遲

[English](turn_latency_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 待處理按鈕在短鎖內宣告交付後於回合鎖外傳送，仍驗證所有權、時間線、重複與失敗恢復。

2. PR83 檢索目前使用外部中文準備與目前視窗原稿後備；歷史中的自動翻譯工作提案已被取代。

3. 速度優化不能刪必要劇本事實，也不能只為縮工具而把原可完成操作拆成更多模型往返。

4. 比較檢索組成、生成與 embedding 呼叫、完整回合耗時及機制／工具正確率，納入重試／排隊並區分歷史 pilot 和目前量測。

## 流程與介面

```text
鎖內認領待傳送項目 -> 解鎖 -> 傳送 Discord 訊息
預先建立的中文索引 -> 檢查依據的檢索 -> 必要時補查原文
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/discord_bot.py](../../../app/discord_bot.py)
- [app/locks.py](../../../app/locks.py)
- [app/scenario_templates.py](../../../app/scenario_templates.py)
- [app/agents/context_builder.py](../../../app/agents/context_builder.py)
- [tests/test_pending_button_latency.py](../../../tests/test_pending_button_latency.py)
- [tests/test_scenario_query_fallback.py](../../../tests/test_scenario_query_fallback.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/turn_latency_design_spec.md)
