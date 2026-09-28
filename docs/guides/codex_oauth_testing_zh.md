# 本機 Codex OAuth 測試

遊戲對話透過本機 Codex CLI 的 ChatGPT 登入執行；Python 仍負責 CoC 工具、
規則與持久化。不會把 OAuth token 當作 OpenAI API key 使用。

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
ANALYSIS_PROVIDER=openai
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

`ANALYSIS_PROVIDER` 應選原本使用的分析後端；本機複製設定保留了原選擇。
PDF／影像分析、擷取、歷史摘要與可選 embeddings 仍可能使用 API，並非所有
功能都遷移到 OAuth。對話不會自動退回付費 API。已用本機帳號實測指定的
`gpt-6-luna`，未替換模型。

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
