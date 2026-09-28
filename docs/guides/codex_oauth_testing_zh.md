# 本機 Codex OAuth 測試

遊戲對話與一般結構化文字分析可透過本機 Codex CLI 的 ChatGPT 登入執行；Python
仍負責 CoC 工具、規則與持久化。不會把 OAuth token 當作 OpenAI API key 使用。

## 環境

測試目錄為 `/Users/marcoliu/workspace/coc_codex`，分支
`enhancement/codex-oauth-provider`，基於 `main_v2`。
已複製原部署的 `.env`，並把資料庫、劇本、備份及匯入路徑改到新目錄。
`.env` 不入版控，權限為 0600；原部署設定、資料與執行中的 Bot 不受影響。

```sh
cd /Users/marcoliu/workspace/coc_codex
source .venv/bin/activate
codex login status
```

若需要登入，執行 `codex login` 並選 ChatGPT。已驗證 CLI 版本為 0.157.1；
升級 CLI 後需重新驗證 transport 設定及 app-server 協定。

```dotenv
LLM_PROVIDER=codex
ANALYSIS_PROVIDER=anthropic
CODEX_MODEL=gpt-6-luna
CODEX_REASONING_EFFORT=medium
CODEX_TRANSPORT=exec
CODEX_TIMEOUT=120
CODEX_MAX_CONCURRENCY=2
CODEX_MAX_INPUT_BYTES=2097152
CODEX_MAX_OUTPUT_BYTES=1048576
MAX_TOOL_ITERATIONS=6
MAX_TOOLS_PER_TURN=4
```

PDF 頁面圖片修復／地圖分析及預製角色卡擷取的 `ANALYSIS_PROVIDER` 必須使用
API Provider（`openai`、`anthropic` 或 `gemini`）。Codex 的圖片分析在抽樣地圖頁
沒有產生結構化房間，因此程式會拒絕把 Codex 用於這項設定。劇本索引、開場擷取、
劇本比較與 Keeper 歷史摘要則跟隨 `LLM_PROVIDER`，可使用 Codex 文字分析。Codex CLI
使用本機 ChatGPT 登入，不需要 `OPENAI_API_KEY`；可選 RAG embeddings 仍是獨立的
OpenAI API 功能。Provider 失敗時不會自動切換。

真實 PDF 測試中，Codex 對抽樣頁面的分類正確，但兩張地圖都沒有擷取出結構化
房間。因此 PDF／圖片／OCR 與角色卡擷取不可使用 Codex。

## 分析煙霧測試（會呼叫已登入的 CLI）

一般 CI 預設略過。使用下列指令會送出一次合成文字分析：

```sh
RUN_CODEX_ANALYSIS_SMOKE=1 python -m pytest -q tests/test_codex_analysis_smoke.py
```

測試會使用本機 ChatGPT 登入並消耗可用方案額度，只傳送合成文字；不發 Discord
訊息，也不讀寫遊戲狀態。

## 不發 Discord 的測試

```sh
python scripts/codex_smoke.py --transport exec --scenario text
python scripts/codex_smoke.py --transport exec --scenario check
python scripts/codex_smoke.py --transport app-server --scenario check
python scripts/codex_smoke.py --transport app-server --scenario pipeline
```

測試腳本停用 dotenv、建立暫存資料、清空測試程序的 API／Bot 密鑰，並使用
合成劇情；OAuth 仍使用本機現有登入。`check` 走真正遊戲 gateway 與檢定
結算；`pipeline` 加上完整 Supervisor、Executor、Narrator。僅固定 Python
隨機骰值以重現結果，不偽造模型、工具或資料庫結果。不啟動 Bot 或發送訊息。

## 50 輪

```sh
python scripts/codex_evaluate.py --transport exec --output /tmp/coc-exec-25.jsonl
python scripts/codex_evaluate.py --transport app-server --output /tmp/coc-server-25.jsonl
```

每組五類案例各五次：檢定成功、檢定失敗、普通物品拾取、場外規則問題、已有
待檢定時再次行動。合計 50 個案例，各 transport 25 個。檢定案例包含玩家
行動、Python 結算 `/coc check`、後續敘事，因此一個案例可能含多個玩家請求
與 Codex 請求。這是合成案例，不是回放正式團務 log 的 50 次玩家動作。

輸出記錄整個案例耗時、模型決策請求數、prompt/schema 位元組、真正工具與
參數、收據是否成功、骰子次數，以及狀態／文字斷言。工具正確率檢查預期工具、
參數與成功收據，並禁止場外或既有 pending 案例新增不必要變更；不代表所有
敘事或所有規則都已驗證。報告不覆蓋舊檔。兩組可在獨立程序並行，耗時仍會
受共用 OAuth 額度與網路影響。

## 運作邊界

僅遊戲 Codex 子程序關閉原生 shell、檔案、網頁及外掛工具，不關閉開發中的
Terminal，不修改全域 Codex 設定。Python 驗證 JSON schema 與當前允許工具，
再呼叫既有 callback；模型宣稱完成不能直接改 state。

同一玩家回合跨 agent 共用四次遊戲工具額度。每段 conversation 最多六次
模型決策（若呼叫端指定更少，從其限制）；格式修復最多一次並計入額度。
工具例外回傳錯誤收據，不自動重播同一操作；已完成變更保留。未完成時先查看
目前 state，避免直接重送原行動。

期限包含排隊與 LLM 工作。已開始的遊戲變更由既有 gateway 完成持久化，
可能讓總牆鐘時間超過 LLM 期限，但期限後不再啟動模型請求。
取消／逾時只回收本次擁有的 Codex process group；輸入與輸出都有大小上限。
一般 log 不記 stderr 內容，只記位元組數與安全錯誤代碼。

app-server 在單段 conversation 的多次決策間保持啟動，結束即關閉。每次
決策建立新的 ephemeral thread，重送 Python 權威 context，避免過期隱藏
歷史或跨玩家污染；目前不是全域長駐 server pool。可切回 exec 比較。

本次不開 PR、不 merge。

## 官方參考

- [Codex 非互動模式](https://developers.openai.com/codex/noninteractive)
- [Codex 驗證方式](https://developers.openai.com/codex/auth)
- [Codex app-server](https://developers.openai.com/codex/app-server)

## 工具決策診斷

使用 `--kinds pending check_success --repeats 3 --trace` 重測合成案例。
`--trace` 包含模型決策、動態上下文、工具收據與裁決驗證碼，只適用於
這些自建測試場景。`rejected_proposals` 計入已被 Python 擋住的無效提案；
`incomplete_retries` 記錄 Executor 未執行工具就回未完成時的一次重試。
首次決策、最終工具正確率、完整流程成功率須分開報告。

`pending_pickup` 是額外壓力案例：既有驗證器在 pending 期間只接受完整
調查員間物品交接例外，一般拾取不屬於此例外。因此背包工具成功並不
代表最後裁決也成功。若保留原 pending 身分，回等待裁決仍可能有效；
上述例外專指 resolved／resolved_without_check 的完成宣告。

### 裁決驗證回饋

Codex Executor 現在可在交回 final 前收到既有 Python 驗證器的拒絕原因，
最多一次，沿用原本模型迭代與期限預算。已提交工具保留在對話收據中，
不得重播；再次無效仍交由原本 Python 路徑拒絕。`final_retries` 分開記錄
這項成本。此能力只由 Codex provider 啟用，其他 provider 不變。
