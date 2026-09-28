# Codex OAuth 對話 Provider

狀態：提案；已準備環境，程式實作待規格確認。
分支：`enhancement/codex-oauth-provider`，基於最新 `origin/main_v2`。

## 目標與範圍

在獨立的 `workspace/coc_codex` 透過本機 Codex CLI 的 ChatGPT 登入測試
CoC。Codex 決策與敘事；Python 掌管授權、規則、骰子、狀態與工具。
先驗證 exec，再以相同介面加入 app-server。不自動啟動 Discord Bot。
第一階段不把 OCR、文字擷取與影像分析移到 Codex。
複製的 .env 不入版控，權限 0600；資料庫、備份、劇本及匯入路徑全部指向
新目錄，既有部署不受影響。保留原 API 設定供分析功能使用。

## 現有程式與介面

目前 run_conversation 已接收 static/dynamic prompt、tools、history、
new_message、非同步 execute_tool、max_iterations、enable_wrapup。
保留這份契約，新增 typed Protocol 與共用 provider registry。
Executor 的 final.content 必須原樣交回既有裁決 JSON 字串；Narrator 則是文字。

需整合 Executor、Narrator、Guard、Keeper/KP Assistant，並把分析能力拆出
ANALYSIS_PROVIDER。檢查 pregen_extractor、scenario_compare、scenario_index、
scenario_intro、scene_map、pdf_ai_repair、markitdown_shim、Keeper 分析，
以及 Discord 預熱與關閉流程。避免切換後匯入功能默默回傳空結果。
Executor/Narrator 目前僅對 OpenAI 啟用的動態工具／戰鬥狀態限制，改以能力
判斷提供給 Codex，維持既有 provider 行為。

## 設定

| 設定 | 測試值 | 說明 |
| --- | --- | --- |
| LLM_PROVIDER | codex | 遊戲對話 |
| ANALYSIS_PROVIDER | 原 .env provider | 文字與影像分析；既有後端預設沿用 LLM_PROVIDER |
| CODEX_MODEL | gpt-6-luna | 依使用者指定，實測可用性，不擅自換模型 |
| CODEX_TRANSPORT | exec | 第二階段可選 app-server |
| CODEX_TIMEOUT | 120 | 單次對話總期限，包含排隊、修復與工具等待 |
| CODEX_MAX_CONCURRENCY | 2 | 依事件迴圈管理准入 |
| CODEX_MAX_OUTPUT_BYTES | 1048576 | 包含 stderr 的 transport 輸出上限 |
| MAX_TOOL_ITERATIONS | 6 | 模型決策請求上限，包含格式修復 |
| MAX_TOOLS_PER_TURN | 4 | 同一玩家回合跨 agent 共用工具額度 |

啟動時驗證正數、provider 名稱；Codex 必須明確指定支援的分析 provider。
不用修改資料庫 schema。使用明確的回合 context 保存期限、額度、回合／對話
識別與工具收據。新玩家動作或另行提交檢定才建立新 context；agent 交接或
transport 重啟不能重置同回合額度。

## 流程與 JSON 協定

```text
Player -> existing turn pipeline -> latest state + scenario evidence
       -> Executor -> provider interface -> CodexProvider
                                        -> ExecTransport / AppServerTransport
                   <- validated final | tool_call
tool_call -> schema + current allowlist + shared budget
          -> execute_turn_tool / execute_tool
          -> Python state + evidence receipt -> CodexProvider
final.content -> Python resolution validation -> Narrator
              -> narrative/check-button validation -> reply
Pending check -> player check -> Python dice/Luck -> follow-up -> narration
Analysis/import -> ANALYSIS_PROVIDER -> existing API adapter
```

每次模型決策只接受一個完整物件：

```json
{"type":"tool_call","name":"skill_check","arguments":{"skill":"Spot Hidden","difficulty":"regular"}}
```

```json
{"type":"final","content":"敘事文字，或 Executor 既有裁決 JSON 字串"}
```

skill_check 參數只是示例；實際使用現有工具定義產生 schema。檢查完整形狀、
拒絕多餘欄位、未知工具與錯誤型別，執行前再次確認當前允許工具。
不可把串流片段、推理、Markdown code fence、工具 log 當最終結果。
若 CLI 不支援 union schema，由 transport 正規化嚴格 envelope，不能放寬驗證。

格式錯誤最多補問一次，仍消耗同一期限與迭代額度，修好前不執行工具。
工具例外轉成有界錯誤收據；成功過或完成狀態不明的 mutation 不自動重播。
額度耗盡不得宣稱成功。遵守 enable_wrapup=False；可選收尾也必須在總請求
額度內且關閉工具。待擲骰時維持玩家按鈕與 Luck 流程，不自動擲骰或推進。
四次工具可能不足以處理多目標戰鬥／大量補查；保留已完成狀態並交代未完成
部分，不能默默提高額度。

## Transport 與生命週期

exec 使用 argv、stdin，不拼接 shell。使用 --json、--output-schema、
--ephemeral、--skip-git-repo-check、空工作目錄與 read-only sandbox。
隔離使用者／專案指令、MCP、plugin、原生 shell／檔案／網頁工具與環境密鑰；
read-only 本身不能禁止讀檔，送遊戲資料前須實證 CLI 的關閉控制。
OAuth 交 CLI 管理，不複製 token、不使用非公開 OAuth endpoint；子程序移除
API key 覆蓋值並確認登入方式。只解析正式完成事件，且程序須成功結束。
限制輸入與增量 stdout/stderr 大小，stderr 去敏後才記錄。
取消／逾時終止 process group，必要時 kill、排空管道並回收程序，finally
釋放准入。工具取消沿用持久化語意，完成狀態不明時不能自動重做 mutation。

exec 測試通過後才加入 app-server：本機 stdio JSON-RPC、initialize 握手、
各對話隔離 thread、request ID 配對、完成／失敗通知與取消。
依安裝版本 schema 確認 sandbox 與工具隔離；不支援時明確報告阻礙。
限制通知大小並持續排空，斷線／重啟讓等待操作失效但不重播工具。
provider 關閉時回收 server；保留 exec 可選以便比較。禁止跨玩家共用
隱藏 context 或沿用過期狀態。

## 測試與驗收

1. 假 transport：無工具、一個工具、連續兩工具、工具失敗、收據後敘事、
   未知工具、參數錯誤、畸形／空 JSON、多餘欄位。
2. 子程序：非零 exit、錯誤 JSONL、含排隊的逾時、排隊／執行中取消、
   大量輸出與 process cleanup。
3. 額度：迭代耗盡、Executor/Narrator 共用工具上限、並行回合隔離、
   收尾不能超額、mutation 不重播。
4. 既有 provider 契約及分析／影像路由相容性。
5. 隔離 CoC 狀態：查看文件 -> 建立待檢定 -> 玩家檢定 -> Python 骰子／
   Luck -> 裁決 -> 敘事。斷言狀態與收據，不只檢查文字；禁止重骰或自行行動。
6. 明確啟用的真 OAuth 測試：純文字及隔離完整回合；exec 通過後以相同案例
   測 app-server，不發送 Discord 訊息。
7. 記錄模型、CLI 版本、登入方式（無 token）、請求／工具次數、排隊／transport／
   全回合耗時與正確性。登入成功不等於模型與遊戲流程驗證成功。

執行相關既有測試、lint、型別與 diff 檢查。測量結果另外記錄，不預設 OAuth
一定更快。

## 分階段交付與待驗證項目

規格確認後：介面／設定與純文字 PoC -> 工具協定與限制 -> CoC 整合 ->
app-server 同案例驗證。模型可用性、CLI 關閉原生工具、schema 支援與取消
行為須實測；缺少必要隔離控制則明確報告。最後才討論文字／影像分析遷移。

## 參考

- https://developers.openai.com/codex/noninteractive
- https://developers.openai.com/codex/auth
- https://developers.openai.com/codex/app-server
- 本機 codex-cli 0.157.1 help；login status 顯示 ChatGPT 登入。
