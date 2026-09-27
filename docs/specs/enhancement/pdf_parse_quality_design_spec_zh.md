# 保留證據的 PDF 解析與局部修復

[English](pdf_parse_quality_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 保存完整來源；MAX_SCENARIO_CHARS 限制 prompt／比較輸入，不截斷劇本庫 PDF；保留頁界、原文語言與來源座標。

2. 每頁以數字與文字覆蓋選 native／layout，短頁也檢查；配對不符、未解析或未驗證的 layout 不能成權威來源。

3. 用字詞／區塊幾何解析橫列及對齊直排屬性表；保留任意多字技能與百分比；正文提到 STR 檢定不等於空白角色欄位。

4. 本地 OCR 修復有限受損區域並保留完好字詞／數字；匯入時 AI 視覺透過設定 provider 處理選定不確定區域，預設最多 8 次頁面呼叫、每次 8 區塊。

5. AI 只轉錄可見證據，區分可讀、空白、不可讀；拒絕重複區域回應、編造數字與更改已驗證配對，不需真人 KP 登入。

6. Luck 空白保持空白，不由 AI 補值；其他未確認核心屬性用 PDF_UNRESOLVED_FIELDS 標記，建角時不得悄悄預設 50。

7. 只阻擋受影響卡片認領，其他頁仍可用；明確手動更正須在後續解析保留，預算用盡、provider 失敗與不支援旋轉均保留原證據。

8. 保存候選文字、檢查、選用方法、修復決定與裁切 hash 供診斷；覆蓋 heuristic 與驗證轉錄可減錯，但不證明所有 PDF 語意完整。

## 流程與介面

```text
PDF -> 原生幾何資訊＋版面候選 -> 覆蓋率／配對驗證 -> 有上限的局部 OCR -> 有上限的 AI 修復 -> 完整來源＋報告
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/pdf_loader.py](../../../app/pdf_loader.py)
- [app/pdf_quality.py](../../../app/pdf_quality.py)
- [app/pdf_ai_repair.py](../../../app/pdf_ai_repair.py)
- [app/pregen_extractor.py](../../../app/pregen_extractor.py)
- [tests/test_pdf_loader.py](../../../tests/test_pdf_loader.py)
- [tests/test_pdf_numeric_pairs.py](../../../tests/test_pdf_numeric_pairs.py)
- [tests/test_pdf_ai_repair.py](../../../tests/test_pdf_ai_repair.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/pdf_parse_quality_design_spec.md)
