# Camp Sunny 修正：定向回歸報告

依規格第 10 節的順序列出每個階段**實際做了什麼、沒做什麼**。結論先講：**Stage 1–2（離線）完成；Stage 3–5（真實執行）沒有做，原因是本環境沒有 Codex 登入、沒有 Discord、沒有 Camp Sunny 的 PDF 與索引。** 規格第 15 節的合併門檻因此仍是 **HOLD**。

## Stage 1 — 單元／整合與靜態檢查

七個分支各自都跑過，並在合併進 `main_v2` 之前各自併入最新的 `main_v2` 再跑一次：

```
ruff check .          # 通過
mypy app              # 通過（158 個檔案）
pytest                # 通過
```

全部合併後的 `main_v2` 共 2740 項測試（修正前為 2513 項，增加 227 項），全部通過。整合檢查發現並修掉一個跨 PR 問題：事件義務 gate 的 docstring 寫到驗證的範例用語，被「執行期程式不得寫出特定劇本或其觸發物」的檢查擋下（已修在 [#181](https://github.com/marcoliu99/line-coc-keeper/pull/181)）。

審查階段（每個 PR 都有自動審查意見，都經確認並修正，並以先失敗再通過的測試涵蓋）另外抓到的問題，包括：相鄰 chunk 重複與重疊切分、`disabled` 檢索被誤當成沒有依據、復原後原因分類、守門順序與取消清理、embedding 切分與重試上限、隊伍人數改寫範圍。

**測試本身找到的漏洞：** 戰鬥覆蓋測試重放過期的檢定按鈕時，發現回覆文字仍印出原始等級（CS-003 的另一個洩漏處，[#185](https://github.com/marcoliu99/line-coc-keeper/pull/185) 沒有涵蓋），已在 [#186](https://github.com/marcoliu99/line-coc-keeper/pull/186) 修正。這也說明了離線測試能抓到的是呈現與機制路徑，而不是真實模型行為。

這些 PR 彼此在 `docs/README.md`、`docs/README_zh.md`、`docs/specs/catalog.json`、`app/config.py` 與 `app/agents/supervisor.py` 的 import 行有相鄰行衝突（內容不衝突），合併時都以「兩邊都保留」解開，並在解開後重跑上述檢查。

## Stage 2 — 定向情境回歸（離線，合成 fixture）

| 規格要求 | 測試 | 說明 |
| --- | --- | --- |
| 1 接待處按鈴／相鄰觸發 | `tests/test_scenario_adjacency.py` | 鈴 → 經理的合成 fixture；另一個劇本與語言的 fixture（A4）；執行期不得出現特定劇本名稱的檢查 |
| 2 不洩漏未來場景 | 同上 | 鄰居之後的 chunk 不會被帶出；跨頁、看不到的 chunk 不附；自足命中不擴充（A2、A3） |
| 3 通用 cannot-continue 與復原 | `tests/test_turn_fallback.py` | 每個驗證 code 都有原因；復原最多一次搜尋與一次重跑；狀態改變後不重試（B1–B5） |
| 4 紅水桶揭露 + 同回合 SAN | `tests/test_event_obligations.py` | 揭露與待處理 SAN 在同一則回覆；氣氛恐怖不扣 SAN；重播不重複；傷害與強制檢定走同一機制（C1–C4） |
| 5 記憶過長／切分／fallback | `tests/test_memory_embedding_bounds.py` | 超限內容不送出；part 可追溯；不洩漏 provider 訊息；之後成功恢復向量；拒絕不重試 |
| 6 隊伍人數 | `tests/test_presentation.py` | 五位調查員不會被敘述成六位 |
| 7 等級／check_id | `tests/test_presentation.py` | 最終回覆與私訊不含原始等級與內部識別碼 |
| 8 戰鬥／機制 | `tests/test_combat_mechanics_coverage.py` | 見 [覆蓋說明](../specs/maintenance/combat_mechanics_coverage_design_spec_zh.md) |
| 延遲相關 | `tests/test_turn_phases.py` | 階段計時的計算、沿用條件、搜尋上限；**不量測延遲** |

**這些測試證明的是機制與契約，不是真實模型的行為。** 例如 A1 證明相鄰的後果文字會進入 Executor 的依據，並且政策要求使用它；不證明真實模型因此不再自創檢定。

## Stage 3 — 真實執行的定向 Camp Sunny 測試：**未執行**

需要：已登入的 Codex、Discord、Camp Sunny 的 PDF 與既有索引。本環境都沒有。規格要求的最小路徑（按鈴 → 登記流程 → 走出開場 → 紅水桶／斷手 → 同回合 SAN → 後續內容）全部沒有走過。

## Stage 4 — 五人併發短測：**未執行**

同上。沒有任何延遲、擁有者隔離、檢索放大的真實數據。`turn.phases` 事件現在可以回答「時間花在哪」，第一次真實執行應先讀它。

## Stage 5 — 500 回合長跑：**未執行**，也不應在 Stage 3–4 之前進行

所以**不產生** `camp_sunny_real_5p_500_after_fix.md`。

## 第 15 節合併門檻

| 條件 | 狀態 |
| --- | --- |
| P0 = 0、P1 = 0 | **無法判定**：CS-002（P1）只在合成 fixture 上重現與修正，真實情境未驗證 |
| CS-002 不再重現 | **未驗證** |
| CS-008 已分類且有效行動能復原 | 分類與復原已實作；17 個歷史回合**未重播**；復原是否有效**未驗證** |
| CS-007 同回合 SAN | 合成 fixture 通過；真實情境**未驗證**；偵測為詞彙式 |
| 記憶 embedding 有界且可觀察 | 已實作並以合成資料測試；真實 provider **未驗證** |
| 五人延遲門檻 | **未評估**（未量測） |
| 戰鬥／機制定向覆蓋通過 | 離線通過；真實執行**未做** |
| 長跑沒有新的 P1 | **未執行** |

結論：**HOLD**。阻擋項目是上表中所有「未驗證」與「未執行」，不是程式失敗。
