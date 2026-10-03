# Paddle 原生文字閱讀順序比較

## 目標與範圍

只確認：現有 Paddle 版面分析能改善上一輪四個 PyMuPDF4LLM 失敗案例中的幾個。這是調查，不是 production implementation。不改匯入器、scenario library、遊戲、publication、地圖、reparse、依賴或 schema，不加入第三個工具/API。

Baseline main_v2：`7d2cc8cab76d25aa1f47c47568ad2f9ff05d910b`。沿用實驗環境 Python 3.11、PaddleOCR 3.7.0、PaddleX 3.7.2、PaddlePaddle 3.3.0、PyMuPDF 1.28.2。用現有套件支援的 `LayoutDetection(model_name="PP-DocLayoutV3")`，CPU 四 threads、關閉 MKL-DNN、150 DPI。只下載官方模型權重至 cache，不安裝/升級套件。PyMuPDF4LLM 1.28.2 結果原樣沿用上一輪，包括當時預設的套件自動 OCR 行為。

重用原 `selected_pages.pdf`，SHA256 `362531220b2ad0d5d4479b6c5fed754de9a3660d6d1245727cd5127f0f4b322e`。三張真實頁（PDF 1、3、15／印刷 17、19、31），加單欄、跨欄標題三欄 fixture。沒有重建 fixture 或跑整本。

## 介面與實際方法

```text
既有 subset PDF -> PyMuPDF 原生文字行與位置
                -> 150 DPI 圖片 -> Paddle 版面區塊與 order
                -> 每行原生文字配對一個文字區塊
                -> Paddle 區塊順序、區塊內原生 y/x 順序
                -> 原生文字重排結果 -> 人工正確順序比較
```

沒有建立 OCR detector/recognizer，也沒有跑 PP-Structure 整套 parsing。Paddle 只給區域/順序；文字完全來自 PDF。實驗 script 不 import app，也不呼叫 scenario import/save/index/reparse。

人工預期順序依上一輪渲染圖，在 inference 前固定：標題/頁首→完整左欄→完整右欄（三欄則左→中→右）→頁尾。人工區域只用於評分，不能流入 Paddle 排序。

映射依實際 pixel/PDF 比例，用「交集面積／原生行面積」最高的區域，最低 overlap=0.15。圖片、chart、seal、header/footer image 不作文字區域。正文依 Paddle `order`；沒有 order 的 header 放前、footer/number/footnote 放後。區塊內只依 y/x 排行，沒有再猜欄序。未配對行保留到末端且強制 FAIL；所有原生行恰好使用一次。沒有針對單頁調參或修正三欄失敗。

## 結果與證據

Evidence：`/Users/marcoliu/workspace/paddle-layout-order-audit-20261003/`。

每頁保存 raw 文字、原生位置 JSON、上一輪 PyMuPDF4LLM Markdown、Paddle layout JSON、Paddle ordered 文字、人工 expected 文字、映射/預期 ID JSON、Paddle 區塊 PNG。另存原 subset、固定 oracle、模型 hash、設定、script、時間與測試 logs。

兩種方法用相同人工標題/欄序 anchors 評分。Paddle 另要求全部原生行符合完整人工順序且沒有未映射行，避免只排對幾個標題就當改善。

| 證據頁 | 版面 | PyMuPDF4LLM | Paddle |
|---|---|---|---|
| 01／PDF 1 | 大標題雙欄 | FAIL | PASS |
| 02／PDF 3 | 普通雙欄 | FAIL | PASS |
| 03／PDF 15 | 技能、法術、圖片，複雜雙欄 | FAIL | PASS |
| 04／synthetic | 單欄控制組 | PASS | PASS |
| 05／synthetic | 跨欄標題三欄 | FAIL | FAIL |

**PyMuPDF4LLM 1/5；Paddle 4/5；原四個失敗案例改善 3/4。**

Paddle 四個 PASS 的完整原生文字都與人工 expected 完全相同，含頁首頁尾。五頁都沒有未映射、遺失或重複的原生行；raw 文字和上一輪全部 byte-identical。複雜頁插畫正確判為 image、不參與文字排序、沒有 native 行被放入圖片。第 02 頁的裝飾放大鏡沒有獨立偵測，但也沒有被當成文字，不影響原生文字。

剩餘失敗是 reading order：三欄的九個正文項目都有獨立 text boxes，卻按左1→中1→右1→左2→中2→右2→左3→右3→中3排序。標題仍在前，並不是把三欄合成一個區塊。第 03 頁 Skills 標題沒被獨立偵測，原生行因和鄰近技能文字區塊重疊仍完整保留、位置正確。因此欄序 PASS 不表示語意區塊分類完美。

原生文字、數字 token counters 相同，包含百分比、SAN loss、骰子公式。單欄 `1d6`、`1d10`、`1d4+2`、`SAN 1/1d6`、`50%` 全保留。`+20`、`-10` 沒有出現在這五頁，不能宣稱已驗證真實 PDF 的這兩個 literal；計數器支援正負號，不算額外頁面證據。

## 驗證與限制

- 獨立 evidence pytest：**25 passed、5 failed**。四個原 PyMuPDF4LLM 失敗＋Paddle 三欄失敗；不標 xfail。
- 測試讀實際保存輸出，需先重跑 compare_layout.py 才更新 inference。放在 repository CI 外，不是新增持續 CI regression tests。
- 既有 full suite：**1862 passed、1 skipped**，另有 **152 passed subtests**。
- CPU 每頁 layout inference 約 0.91–1.24 秒，不含初始化、渲染、mapping；不是 production latency benchmark。
- 五頁是偏重失敗案例的小樣本，不能代表所有 PDF 或掃描頁。
- 人工 oracle 與 0.15 threshold 均公開。映射仍是實驗，production adapter 必須另測 coverage、ambiguity、fallback。

## 決策與下一個最小方案（未實作）

Paddle 在三張真實雙欄頁有明確改善，但不能可靠解決所有多欄。值得考慮獨立小型 production PR，限「可明確分成兩欄、具有原生文字」的頁面，初期 opt-in；單欄、三欄、版面不明仍維持原流程。最小 adapter 只用 Paddle 區域順序排列 PDF 原字，要求所有原生行一對一配對、token 完整，遇錯誤/不明就回原擷取流程。回原流程只維持既有行為，不能保證欄序正確。本輪沒有實作 adapter、flag 或 production fallback。
