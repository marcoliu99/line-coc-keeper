# Codex 結構化文件與圖片分析 Provider

## 目標

讓 `ANALYSIS_PROVIDER=codex` 能透過已登入的 Codex CLI 處理現有結構化文字與圖片分析請求，同時維持分析呼叫端既有的同步 Provider 介面。

## 現況與已查證事實

截至 2026-09-28，`main_v2` 已包含 PR #120：

- 分析呼叫端透過 `registry.analysis_provider()` 選擇 Provider；剩下的選擇限制在 Provider 登錄表與設定驗證。
- `codex_provider` 有對話決策能力，但沒有 `analyze_text` 或 `analyze_image` adapter。
- 目前七個分析呼叫點都在事件迴圈之外執行：呼叫端使用 `asyncio.to_thread` 或工作程序。因此同步 adapter 可以在其中以 `asyncio.run` 執行現有非同步 transport，不必改動呼叫端。
- Codex CLI 0.157.1 支援 `codex exec -i/--image` 與 `--output-schema`。`ExecTransport.request` 已會建立 schema 檔並傳給 `codex exec`，但目前沒有圖片參數。
- `ANALYSIS_PROVIDERS` 排除 Codex，`app/config.py` 也拒絕 `ANALYSIS_PROVIDER=codex`。
- PDF 圖片修復及地圖／頁面圖片分析可能每頁呼叫一次。`ExecTransport` 每次請求都會啟動 CLI 子程序，可能使啟動延遲與 ChatGPT 方案額度按頁數累積；實作前必須先量測。

Provider 依工作類型分流，不只看資料是否來自 PDF。路由如下：

| 工作 | 設定 | 原因 |
|---|---|---|
| PDF 頁面圖片分類、場景地圖／房間圖擷取、以圖片修復／OCR PDF | `ANALYSIS_PROVIDER` | 直接處理頁面圖片或文件擷取，可能逐頁呼叫。 |
| 預製調查員／角色卡擷取，包含結構欄位與技能 | `ANALYSIS_PROVIDER` | 依使用者決定，即使輸入已是抽出的文字，角色卡仍屬於文件分析。 |
| 劇本索引（NPC／地點）、開場敘事擷取、劇本文字比較、Keeper 歷史摘要，以及其他非 PDF 結構化文字分析 | `LLM_PROVIDER` | 這些是一般文字工作，應跟隨對話 Provider。 |

目前實作的七個分析呼叫點都使用單一全域 `analysis_provider()`。為符合此路由，實作時須把非 PDF 文字工作改由目前的 LLM Provider 處理，同時讓預製角色擷取、PDF 修復及場景地圖／頁面圖片分析繼續使用分析 Provider。結構化分析維持現有的「處理失敗時回傳 `None`」慣例。

## 範圍

- 新增同步函式 `codex_provider.analyze_text(text, tool, prompt_text)` 與 `codex_provider.analyze_image(png_bytes, tool, prompt_text)`。
- 依 `tool["input_schema"]` 衍生 Codex strict-output-schema，再以 `jsonschema` 對照原始 schema 驗證回傳 JSON；驗證成功才回傳字典。衍生 schema 必須替每個 object 設定 `additionalProperties: false`，並符合 Codex strict mode 的 required 欄位規則，且不能放寬原始 schema。
- CLI、逾時、解析或 schema 驗證等可處理的失敗回傳 `None`，不改送其他 Provider。
- 為 `ExecTransport.request` 新增可選 PNG 圖片參數。將 bytes 寫入暫存 `.png`，以 `-i`／`--image` 傳入路徑，並在成功、失敗、逾時或取消時清除檔案。
- 分析請求使用一次性的 `ExecTransport`；圖片輸入與 `--output-schema` 已在此 `codex exec` 路徑查證。此工作不改動對話 transport 選擇，也不改 app-server 協定。
- 將 Codex 加入 `ANALYSIS_PROVIDERS`，並允許 `ANALYSIS_PROVIDER` 設為 `codex`。
- 劇本索引、開場敘事擷取、劇本文字比較與 Keeper 歷史摘要改走 `LLM_PROVIDER`；預製角色擷取、PDF 修復、場景地圖／頁面圖片分析繼續使用 `ANALYSIS_PROVIDER`。
- 更新 `.env.example` 與 Provider／設定文件，說明 `ANALYSIS_PROVIDER=codex`、Codex CLI 安裝和 `codex login`。
- Codex 分析不依賴或讀取 `OPENAI_API_KEY`。文件須說明：另外啟用的 RAG embeddings 仍走現有 OpenAI Embeddings 路徑，可能獨立需要該金鑰。

## 非目標

- 不改分析呼叫端介面，也不把分析請求搬回事件迴圈。
- 不改玩家對話、CoC 工具、遊戲規則或劇本資料格式。
- Codex 失敗時不自動改送 OpenAI、Anthropic 或 Gemini。
- 不更換對話 transport，也不在此工作新增 app-server 圖片支援。
- 不遷移未使用分析 Provider registry 的功能。
- 不移除 RAG 索引／搜尋中獨立使用的 OpenAI Embeddings。

## 介面與資料流程

公開分析介面維持同步：

```text
既有工作執行緒／工作程序呼叫端
  -> registry.analysis_provider()
  -> codex_provider.analyze_text / analyze_image
  -> asyncio.run(一次性 ExecTransport.request(...))
  -> 衍生 Codex strict schema
  -> codex exec --output-schema [ -i 暫存頁面.png ]
  -> 嚴格 JSON 與原始呼叫端 schema 驗證
  -> 回傳字典；可處理的失敗回傳 None
```

`analyze_text` 將提供的文字與任務提示作為請求內容。`analyze_image` 傳送任務提示，並透過 CLI 圖片參數傳入 PNG。分析 schema 只是輸出契約；Codex 不得執行應用程式或遊戲工具。

Adapter 必須沿用 Codex 逾時與輸入／輸出上限。同步呼叫會建立短生命週期事件迴圈，因此實作必須確認請求准入與並發上限能跨這些呼叫生效；只有事件迴圈範圍的 `asyncio.Semaphore` 無法協調不同的 `asyncio.run` 迴圈。不得記錄提示詞、擷取出的文件文字、圖片 bytes、圖片路徑、憑證或完整環境值。診斷資訊可記錄 Provider、任務種類、耗時和安全的錯誤類別。

## 設定行為

目標設定如下：

```dotenv
LLM_PROVIDER=codex
ANALYSIS_PROVIDER=codex
```

對話與非 PDF 文字分析使用 `LLM_PROVIDER`；文件／圖片擷取與預製角色卡擷取使用 `ANALYSIS_PROVIDER`。兩者都可獨立選用已登入的 Codex CLI。子程序沿用現有環境變數 allowlist；Codex 呼叫不得要求或讀取 `OPENAI_API_KEY`。RAG embeddings 是獨立功能，不在此保證範圍內。

### 非 PDF 文字與結構化擷取測試（2026-09-28）

五次直接 Codex CLI 測試使用真實劇本／角色資料、`gpt-6-luna` 與 medium reasoning effort。這些測試用於確認能力，不是與現有 Provider 的對照基準，也不代表全功能準確度保證。

| 工作 | 對照來源的結果 | 端到端耗時 |
|---|---|---:|
| 開場敘事／檢定擷取 | 從《Dead Boarder》來源頁正確擷取 Sanity 檢定：成功損失 `1`、失敗損失 `1D4`。 | 16.4 秒 |
| 劇本索引 | 從《The Haunting》角色數值頁擷取科比特資料，未補造原文沒有的 HP；回傳地點尚未完整核對。 | 20.0 秒 |
| 劇本文字比較 | 在受控的兩版本比較中，正確找出刻意刪除的 Armor 段落及其 5 點數值。 | 29.0 秒 |
| 預製角色擷取 | 從《Doors to Darkness》10 張角色頁核對：姓名 10/10、職業原文 10/10、抽樣數值 120/120、Luck 來源核對 10/10 正確；每張有 14–18 個技能項目。年齡放置位置尚未完整驗證；現行 schema 沒有專用年齡欄位。 | 160.5 秒 |
| Keeper 歷史摘要 | 四項檢查事實都保留：抵達科比特宅邸、持有鑰匙、尚未取得油燈／煤油／斧頭、購買尚未完成。 | 9.9 秒 |

預製角色 schema 的技能與額外欄位使用動態鍵值字典。Codex strict output 要求封閉物件 schema，因此測試先將這些字典表示成鍵值陣列，再轉回原格式並依現有 schema 驗證。這項轉換及年齡保留需要明確的實作測試。10 張角色卡耗時 160.5 秒，是批次匯入的主要風險。其他測試使用選定頁面或短對話；劇本索引的地點結果與完整文件涵蓋率仍未驗證。

## 實作前真實劇本 PDF 量測門檻

修改執行程式碼前，使用一份真實劇本 PDF。PDF 必須同時包含可選取文字頁、至少一頁掃描／圖片文字頁，以及至少一頁地圖或示意圖。先直接以已登入的 Codex 執行 `codex exec -i --output-schema` 作為 transport PoC；不要先建 adapter。

記錄項目：

- PDF 總頁數，以及現有 PDF 修復和頁面圖片／地圖流程會送去分析的頁數。
- 每一分析頁的子程序啟動時間與端到端耗時，包括中位數、p90、p95、最大值、逾時／錯誤數及總耗時。若量測方式可行，分開記錄子程序啟動和模型完成時間。
- 執行前後的 ChatGPT 方案用量／額度。若 CLI 或 Provider 有提供用量，記錄該數值；否則記錄帳戶畫面前後的額度並標示為估算。服務未公開精確用量時，不推算精確 token 數或額度消耗。
- 掃描頁辨識品質，依對照原稿確認必要文字／欄位擷取成功數、錯誤值和無依據新增內容。
- 地圖／示意圖品質，依對照資料確認標籤／房間及可見連線的正確識別數、漏失、錯誤連線和虛構項目。
- Codex CLI 版本、模型與 reasoning 設定、頁面圖片尺寸、schema，以及請求為循序或併發。

量測報告必須說明「每頁啟動一次 CLI」用於整份劇本匯入是否可接受。若延遲或方案額度不理想，須先調整設計再實作，例如評估安全的批次處理或有上限的常駐 transport。Marco 審查量測結果與因此產生的設計修訂後，才開始實作。

### 首次真實 PDF 測試（2026-09-28）

測試使用 `/Users/marcoliu/Downloads/PDF文件/The_Haunting_Scenario_trimmed.pdf`，共 27 頁。設定 `ANALYSIS_PROVIDER=codex` 時，現有 MarkItDown OCR adapter 不支援 Codex，因此 PDF 流程會將 12 個低文字圖像頁（第 7、17–27 頁）送去圖片分析。每頁以 200 DPI（1650 × 2150 像素）輸出，循序啟動一個 Codex CLI 子程序。CLI 版本 0.157.1；模型 `gpt-6-luna`，reasoning effort `medium`。

前 12 次呼叫原封不動傳入現有工具 schema，模型尚未執行就被 `invalid_json_schema` 拒絕：Codex 要求每個 object 都有 `additionalProperties: false`。改以 strict schema projection 衍生後（所有 object 屬性改為 required，缺省的選填欄位以空字串／空陣列表示），12 次呼叫全部成功。解析後仍須依原 schema 驗證；strict projection 只是 transport 限制，不代表可以改變呼叫端語意。

| 量測項目 | 結果 |
|---|---:|
| strict projection 後完成／錯誤呼叫 | 12／0 |
| 12 頁循序總耗時 | 294.3 秒（4 分 54 秒） |
| 單頁端到端耗時 | 中位數 21.0 秒；p90 29.1 秒；p95／最大值 58.2 秒；範圍 12.3–58.2 秒 |
| Codex CLI event 回報的 12 次用量 | 輸入 236,756 tokens（其中 cached 16,128），輸出 10,030 tokens，reasoning 727 tokens |
| 頁面分類 | 12／12 符合目視參考 |
| 第 18 頁已填角色卡 | 抽查 26 組已填標籤／數值，25 組配對成功；漏出年齡 36。Drive Auto 欄位原本空白，因此不列入預期已填值。這是有限欄位探測，不代表整頁準確率。 |
| 第 7、17 頁地圖結構 | 每張地圖的 `rooms` 都沒有擷取到 13 個實際房間，結構化 exit 也為 0。地下室另有標成 wall space 的區域，不計為房間。第 17 頁另外加上明確 room-graph 指令重測，仍回傳空房間清單。因此 `scene_map.analyze_page_image` 不會建立這兩張地圖。 |

另一次量測第 18 頁時，CLI 第一個 event 出現在 0.70 秒，完整呼叫於 29.21 秒完成；該樣本的大部分耗時在子程序啟動之後。這 12 頁測試採循序執行；匯入器可能同時執行最多 12 個圖片請求，因此併發延遲與限流行為仍未測量。CLI 提供每次呼叫的 token 用量，但 `codex login status` 與 JSON events 均沒有提供 ChatGPT 方案剩餘額度，因此無法報告測試前後額度餘額。不可把這些 token 數當成方案額度的精確消耗。

**實作前門檻結果：** 真實劇本確認基本頁面分類可用，也發現 schema 相容性需求；但兩張地圖都沒有產生必要的結構化房間，即使加上針對地圖的指令仍然失敗。Marco 之後指示繼續實作，代表接受測得的耗時與 token 用量風險以進行這次整合測試；這不代表地圖擷取已可靠。正式使用於劇本前，仍應檢查地圖輸出並保留此限制。

## 失敗與隱私行為

- 僅解析一個 JSON 物件；拒絕格式錯誤 JSON、重複 key、非物件輸出，以及不符合呼叫端 schema 的值。
- 缺少 CLI／登入、圖片輸入不支援、非零結束碼、逾時、輸入／輸出超限、取消或 schema 不符時，依既有分析 Provider 慣例回傳 `None`。
- 不得從此 adapter 呼叫 CoC 工具或修改應用程式狀態。
- 一律清除暫存圖片與 schema 檔，並沿用現有子程序取消和 process-group 清理行為。
- 圖片頁失敗時，不得自動改送其他 Provider；由既有呼叫端決定如何處理 `None`。

## 測試計畫

- 離線單元測試 mock transport，涵蓋文字／圖片請求、schema 參數、圖片旗標／路徑，以及成功和失敗時的暫存檔清理。
- 驗證正確輸出，以及 malformed JSON、重複 key、錯誤 JSON 類型、schema 不符、逾時、非零退出、取消與輸入／輸出上限時的 `None` 行為。
- 測試 `ANALYSIS_PROVIDER=codex` 可解析到 Codex，且不要求 `OPENAI_API_KEY`。
- 新增預設停用的真實登入 smoke test，只有明確設定測試環境變數才執行；使用已安裝 CLI 各做一次文字與圖片分析並驗證 schema。一般 CI 不執行此測試。
- 實作前執行上述真實 PDF 量測；實作後重測受限樣本，確認 adapter 行為相同且每頁呼叫數沒有非預期增加。

## 實作紀錄

已在 Codex analysis branch 實作：

- `CodexProvider.analyze_text` 與 `analyze_image` 使用 `ExecTransport`，將呼叫端 schema 轉成 Codex strict output schema，還原選填欄位與動態 key 值，再依原 JSON Schema 驗證結果。
- `ExecTransport.request` 可接收選填 PNG bytes，只寫入暫存目錄並透過 `codex exec -i` 傳送；圖片 bytes 也計入既有輸入大小上限。
- 分析請求准入限制可跨同一程序內短生命週期的事件迴圈共用。多個 worker process 之間沒有共用限流器，部署端仍須限制跨程序並發。
- Provider registry/config 接受 `ANALYSIS_PROVIDER=codex`；劇本索引、開場擷取、文字比對和 Keeper 摘要改走 `LLM_PROVIDER`。PDF 頁面圖片／OCR 修復與預製角色卡擷取仍走 `ANALYSIS_PROVIDER`。
- 離線測試涵蓋 schema 投影／還原、圖片暫存檔清理、輸出驗證失敗及並發限制。已登入 CLI 的 smoke test 預設關閉，啟用時各送出一次文字與圖片請求。
- 文件保留已量測到的限制：測試地圖沒有產生結構化房間。本次實作不宣稱改善了該模型能力。

Smoke test 不等同實作後的整份 PDF 量測。正式啟用 Codex 地圖擷取前，先用有限真實樣本檢查輸出並與上方基準比較。
