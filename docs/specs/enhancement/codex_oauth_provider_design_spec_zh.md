# Codex OAuth 對話 Provider

狀態：已在實驗分支實作；50 案例真實測試已完成，47／50 通過狀態／敘事斷言。
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

## 實作紀錄（2026-09-28）

兩種 transport 均已在 codex-cli 0.157.1，以 gpt-6-luna 通過真實 OAuth
檢定／gateway／Python 結算／敘事 smoke test。另以假 transport 回歸測試
完整 Supervisor -> Executor -> skill_check -> 持久化 pending -> Python
檢定 -> Narrator，確認沒有重骰。

實際 wire 格式為 `{"decision": ...}`；工具參數以 `arguments_json` JSON
字串編碼，避免 strict output schema 將既有選填工具參數全部變必填。
Python 解碼後依目前真正工具 schema 驗證，再執行。final.content 保持字串，
包含 Executor 所需的裁決 JSON 字串。

app-server 採本機 stdio，每段 conversation 一個 server，每次決策建立新
的 ephemeral thread 並重送權威 context。工具決策之間保留 server，段落
結束即關閉，不是全域長駐 pool。逐項停用繼承的 MCP／plugin；關閉原生 shell
的設定僅影響遊戲子程序，不動目前開發 shell 與全域 Codex 設定。

已開始的遊戲變更沿用 gateway 所有權完成持久化，可能超過 LLM 期限。
期限到後不能再啟動模型，不撤銷或重播變更。同回合完全相同工具與參數的
重複呼叫會保守拒絕；也可能暫緩合理的重複操作，應另行提出動作而非自動
繞過限制。

50 案例指每種 transport 25 個合成劇情案例，包含必要的檢定後續；測量
完整 Supervisor 行為、工具收據與狀態。不是正式團務 log 回放，也不能
代表所有長期團或戰鬥情境。操作方式見[測試指南](../../guides/codex_oauth_testing_zh.md)。

[50-case evaluation / 50 案例結果](../../evaluations/codex_oauth_50/README_zh.md)

## 工具正確性修正

已從真實 trace 重現：同一輸入提供 skill_check，模型卻宣稱沒有這個工具。
用明確 JSON 呼叫／收據範例區分 CLI 原生工具與真正可執行的 host 操作。
原 prompt 已有 pending 身分資料；補上每次更新的結構化 Executor 決策資料
與等待裁決候選。建立檢定工具只允許沒有 pending／Luck 的角色；每次工具
後重算。保留明確取消／更正、其他角色檢定與獨立物品操作，不一概封鎖回合。
僅接入 Codex 呼叫端，Python 裁決及依據驗證保持原規則。

只在明確啟用的合成測試 trace 記錄模型原始提案、裁決驗證代碼與完整收據。
格式錯誤或非法工具提案即使被擋，也計為工具正確率失敗。不增加每輪固定
LLM 審稿、不代替模型自動建立檢定，不開 PR／merge。補 stale schema 等
回歸測試，重測 pending、新建檢定及不相關操作。
