# 外部預製中文優先檢索

[English](scenario_templates_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 中文化在 Bot 外部準備並於遊戲重用；沒有自動背景翻譯、啟動翻譯工作或每回合固定 query 翻譯請求。

2. Schema v3 綁定來源／章節 hash、records_hash 與 compiler 版本；語意記錄攜帶精確 source spans 與欄位證據，覆蓋及 source_quote 驗證拒絕過期或無依據工作簿。

3. 私密預覽、核准及啟用分開；Help 由伺服器選項選來源／檔案或版本配對，檔案须先在 IMPORT_DIR 並通過權威驗證。

4. 只編譯目前允許章節記錄；展開依賴時處理去重／循環並隔離可見性，結果去重前先合併允許的 public／KP 同記錄範圍。

5. 投影上限每記錄 6000 字元、依賴組 12000、回應 18000；整筆省略與不可存取依賴明示，不能默認依據完整。

6. runtime 檢索使用中文投影，原文引句留作私密稽核；cache 識別包含來源／章節／模型／compiler 與檔案 stamp，過期／未核准／缺少版本改用原文。

7. 共用 search_for_state 處理主動與明確查詢；中文零命中或已知缺漏／預算警示，在 state.scenario_text 內補查一次原文，未中仍保留有用中文依據。

8. Executor 發現缺護甲、攻擊、能力、觸發、代價或限制時，以 source=original 跳過中文索引；中英文命中均不證明語意完整，未確認事實仍未知。

9. 原文補查不解鎖其他章節，也不編造地點；機制行動前仍遵守完整敵人卡與個體區分要求。

10. 歷史 pilot：RAG＋Executor 中位數原文 7.26 秒、局部中文 4.22 秒，生成呼叫 3 比 2，每組僅三案例且檢定為 mock；單獨 query 改寫中位數 3.35 秒。這些是動機，不是現行完整遊戲 benchmark。

## 流程與介面

```text
匯出工作檔 -> 外部中文化 -> 匯入／驗證 -> 私人預覽 -> 核准 -> 選用
目前章節記錄 -> 中文檢索 -> 足夠依據／有上限的原文補查
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/scenario_templates.py](../../../app/scenario_templates.py)
- [app/scenario_projection.py](../../../app/scenario_projection.py)
- [app/scenario_rag.py](../../../app/scenario_rag.py)
- [app/help_actions.py](../../../app/help_actions.py)
- [tests/test_scenario_template_v3.py](../../../tests/test_scenario_template_v3.py)
- [tests/test_scenario_template_units.py](../../../tests/test_scenario_template_units.py)
- [tests/test_scenario_query_fallback.py](../../../tests/test_scenario_query_fallback.py)
- [tests/test_scenario_template_review_fixes.py](../../../tests/test_scenario_template_review_fixes.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/scenario_templates_design_spec.md)
