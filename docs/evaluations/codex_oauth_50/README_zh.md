# Codex OAuth：50 案例測試

日期：2026-09-28；模型 `gpt-6-luna`；CLI `0.157.1`；使用本機 ChatGPT 登入。
基於 main_v2 c340984 加上這次獨立 provider 實作，對話不使用 API key。

## 方法

兩個独立程序各跑 25 個合成案例：五類情境各五次。使用完整 Supervisor、
Executor／Narrator、遊戲工具 gateway 與暫存 SQLite。檢定案例包含玩家行動、
Python 檢定結算與後續敘事；只固定 RNG 以重現成功／失敗。因此 50 案例不等於
50 次 LLM 請求，也不完全等於 50 則玩家訊息。不使用正式團資料、不發 Discord。
測試程序的分析及 embedding API key 清空；兩程序共用本機 OAuth 額度。

exec 忽略使用者 config，採模型的 medium 預設；app-server 繼承的設定也是
medium。已核對本機模型 metadata 與非機密設定值；最終程式改為明確指定 medium。
下表請求數是 host 模型決策次數，不含 CLI 內部可能發生的 HTTP 重試。

| 指標 | exec | app-server |
| --- | ---: | ---: |
| 案例數 | 25 | 25 |
| 狀態／敘事斷言通過 | 24（96%） | 23（92%） |
| 嚴格工具選擇／參數正確 | 22（88%） | 20（80%） |
| 完整案例耗時中位數 | 24.681 秒 | 23.424 秒 |
| p95（nearest rank） | 34.742 秒 | 34.836 秒 |
| 僅成功案例的耗時中位數 | 24.528 秒 | 24.855 秒 |
| Host 決策請求 | 71 | 71 |
| 遊戲工具呼叫 | 18 | 18 |
| 成功工具收據 | 15 | 14 |
| Python 玩家擲骰 | 10 | 9 |
| Prompt＋schema 位元組 | 3,425,358 | 3,420,930 |

只比較兩邊都成功的 23 組配對，app-server 減去 exec 的耗時差中位數為
**+0.187 秒**。本次沒有看到 app-server 的加速收益；模型不同決策與共用
額度也會影響耗時。目前保留 `CODEX_TRANSPORT=exec` 為預設，app-server
可用相同遊戲介面切換。

## 失敗與限制

- exec 在第 2、4、5 次 pending 情境仍嘗試建立同類檢定。Python 全部拒絕，
  未覆蓋原 pending、未擲骰；第 5 次回覆未完成，其餘回到等待檢定指示。
  即使狀態安全，三次都計為工具選擇錯誤。
- app-server 在第 1、3、4、5 次 pending 情境有相同多餘呼叫，全部被擋住且
  未改 pending、未擲骰；第 5 次回覆未完成。
- app-server 第 2 次 check_success 沒有呼叫所需檢定工具，回覆未完成，
  未建立 pending，因此沒有繼續擲骰。當輪原始模型裁決未保存，不能斷言是哪條
  prompt 導致它不呼叫工具。
- 真正走到玩家檢定結算的案例都只擲一次 Python 骰；成功才揭露測試線索，
  失敗未洩漏線索。現有斷言未觀察到重骰、覆蓋 pending 或失敗後仍給線索。
- 嚴格工具正確率檢查工具、角色、技能、難度、獎懲骰／強推參數及成功收據；
  OOC／既有 pending 情境不能新增變更工具。這不是所有 CoC 規則的完整驗證。
- 未涵蓋戰鬥、長期團、大型 PDF／RAG、Discord 私訊、完整敘事忠實度或帳號
  整體限流行為，不能由這次測試宣稱全部都可用。
- 批次結束後補強了程序結束尾端輸出的大小檢查，並明確指定原本已使用的
  medium；最終專項回歸測試通過，沒有為此重跑另一組 50 案例。

## 驗證與檔案

- 全套：1,305 passed、1 skipped、45 subtests passed。
- 最後 transport 收尾／明確 reasoning 修正後的 Codex 專項：31 passed、
  4 subtests passed。
- Ruff、mypy、diff 空白檢查通過。
- [exec 原始案例](exec.jsonl)、[app-server 原始案例](app-server.jsonl)、
  [統計 JSON](summary.json)。
- 重跑腳本：scripts/codex_evaluate.py；統計：scripts/summarize_codex_evaluation.py。
  詳見[指南](../../guides/codex_oauth_testing_zh.md)。

兩種 transport 已完成，可用於本機獨立實驗；模型決策失敗仍列在結果中，
並未達成 100% 工具正確率。本次沒有開 PR 或 merge。
