# 合併後匯入與戰鬥完整性修復

[English](postmerge_ingestion_combat_integrity_design_spec.md)

狀態：backlog，決策 frontier 已收斂，待最終共同理解／實作確認。訪談答案尚未授權 runtime 實作。討論前提：PR155、PR156 均已整合進主線。文件實際 base 是 main_v2 6024adf（PR156 已合併）；查核時 PR155 仍 open、head 1c93712。未宣稱已執行完整兩支合併版本。

## 來源與證據

使用者 review：/Users/marcoliu/Downloads/pr155_pr156_full_code_review.md，2026-10-01，固定 PR155 cf62607、PR156 897409d。必須先核對目前 heads，再視為 regression。PR156 1f89f6b 的隔離 probes 確認 R156-01（重試使戰後重傷 CON 失效）及 R156-02（已故障且已持有的槍仍進 PLAYER_ROLL）；後者僅重現宣告，未完整跑故障／維修流程。PR155 1c93712 已靜態核對：後續 native-anchor 修正沒有變更 R155-01～11 路徑；R155-06 已由 PR156 replacement guards 部分改善。未宣稱新增動態或兩支合併驗證。舊 merge conflict 是整合歷史，不當成合併後 runtime 缺陷。

## 已確認決策

- Q1 A：分階段交付。先修檢定生命週期、accepted 資產保留、過期發布、deterministic reading order；再完成新資料的來源版本／生命週期支援、其他領域入口及發行驗證。每階段有可執行驗收與 remaining findings，不把部分修復描述為全數完成。
- Q2 A：遊戲固定明確選用的劇本版本；另一群組重新解析不會自動更新它。切版必須明確且一致處理文字、圖片、地圖與 NPC／角色來源證據。

- Q3 已取代：使用者會在上線前刪除舊劇本，故歷史劇本／來源版本 migration 移出範圍。Q7 已確認一併清理相關遊戲進度、草稿、checkpoints 與資產。Agent 未執行任何刪除。新建立資料仍須 immutable refs、安全重試與生命週期失效。
- Q4 A：切版前先完成目前戰鬥、玩家檢定與 Luck。未解持續義務須明確處理相容性，不得清空；future obligations 的詳細政策已由 Q8 確認。
- Q5 A：newgame／切新劇本使舊 worker 與其啟用 binding 失效，保留草稿、accepted assets 與診斷。重用必須在新 context 明確重新匯入／admission，不能自動重綁或過期發布。
- Q6：依使用者要求排除。不納入 Python 相容修正、版本要求變更或額外 runtime matrix，維持既有驗證環境。

- Q7 A：使用者會在上線前清理舊劇本及相關遊戲進度、草稿、checkpoints、圖片／地圖，乾淨重開。排除歷史劇本／版本及舊局恢復 migration。Agent 未執行清理或刪除；上線前須確認全新資料前提成立。新資料仍需 durable restart／retry 契約。

- Q8 A：同劇本明確切版時，當前 due checks／waits 完成後，可保留相容的 future obligations。各義務保留原來源版本、target／ownership、logical timing、roll receipts；不相容須先明確處理，不 reset／多給間隔／重骰。不放寬 newgame 或換不同劇本的 replacement guards。
- Q9 A：補故障宣告／執行 guards，僅支援有證據且留紀錄的清障／裁定。不新增完整維修技能、耗時或回合子系統。故障綁穩定 owned weapon identity，持久化／結算／回滾一致；其他武器仍可用。未知 NPC 故障／維修規則進 NEEDS_RULING。
- Q10 A：明確無重大效果的日常遺漏及有證據的持有遺漏沿既有更正；不確定或 consequential 的追溯更正走既有 review authority。檢查整句主張，不只物品關鍵字。保留合理當下取得與無 KP Assistant 時依 system evidence 的 conditional Keeper review，不新增固定每回合 judge 或全面人工批准。
- Q11 A：本批保留全部新發布版本，不實作 pruning／cleanup tool；既有廣泛 cleanup 不可靜默刪除被引用的版本。使用者上線前一次性清理，與正常 runtime reset／cancel 分開。

## 保留契約

保留 ADR0003 整場 provisional、bot Keeper 結算／回滾、原骰收據、due ownership 與 future obligations；保留 ADR0002 presentation／authority 分離。不新增固定每回合 judge、不自動清空戰鬥、不猜歷史來源、不把解析／來源發布成功混成遊戲已啟用。OCR 留在 import-time，遊戲使用已驗證匯入證據。

## 設計基礎

Logical scenario 保留穩定身分，published versions 不可變；runtime／rollback 固定選用版本，不讀會移動的 latest pointer。依更新後 Q3 排除初次歷史來源 migration；首次清理依 Q7；未來版本依 Q11 全部保留。共用來源副作用前先核對發布資格；state commit 成功與 derived-image refresh 失敗分開回報。

驗收覆蓋 restart／retry／failed-save 與真實 repository boundaries。Review 狀態區分 confirmed、already_fixed、not_reproduced、blocked。外部 review 的測試數字是歷史資料，不是這次未實作工作的驗證結果。

## 最終交付決策

- Q12 A：分開可 review 的 commits／PRs，但本批修復及整合驗收全數完成後，才由使用者執行乾淨重開上線。不在中間來源 schema 產生 production 資料後，再要求已排除的 migration。訪談未授權刪除資料、部署或開新 PR。

## Findings 與證據

原 review 的 detailed examples 保留為參考；下列契約與驗收使本規格可獨立執行。靜態確認不得冒稱新動態重現。

| Finding | 目前證據 | 修復目標 |
|---|---|---|
| R155-01 | 1c93712 靜態；原 review 動態 probe | 跨欄標題兩側對稱幾何約束 |
| R155-02 | 1c93712 靜態 | 完整 accepted 頁面 snapshot 不被後續例外覆寫 |
| R155-03 | 1c93712 靜態 | 匯入 ownership 固定 lifecycle，不只 attempt |
| R155-04 | 既有 source-library 靜態缺口 | 完整 immutable versions 與 pinned consumers |
| R155-05 | 既有 publication 靜態缺口 | 共用來源副作用前 preflight |
| R155-06 | PR156 部分防護，合併行為未測 | 具名 transition policy 與有效 history projection |
| R155-07 | 既有更正靜態缺口 | 整句 consequential／evidence admission |
| R155-08 | map admission／validator 靜態缺口 | 共用 type-safe graph／references validation |
| R155-09 | 分段路徑靜態 | 每個來源區域有分類，不丟 prefix 卡 |
| R155-10 | 最新 PR156 靜態 | alias／schema 先驗型別再 normalize |
| R155-11 | async 匯入入口靜態 | 每 attempt 一份 off-loop immutable OCR identity |
| R156-01 | 1f89f6b 動態 | 區分重傷 CON 與週期瀕死 CON |
| R156-02 | 宣告動態，下游靜態 | stable-instance 故障 gates 與 recorded clearing |
| MERGE-01 | 舊固定 heads 的 catalog conflict | 保留兩邊 entries，不當成合併後 runtime bug |

實作前逐項在真正整合 base 核對，記錄 confirmed（static／dynamic）、already_fixed、not_reproduced 或 blocked。沒有 failing probe 不代表已修復。safe-short-native coverage／完整 action fingerprint 仍是待驗假設，須先用測試證明再改。PR156 1f89f6b 已修的 owned weapon／NPC attack-card 契約必須保留 regression。

## 領域契約

### 劇本版本與發布

- 保留 logical scenario identity；published version 固定 text／PDF identity、images、maps、indexes、pregens、extraction provenance。其他資產改變時 text hash 不足以識別版本。
- Active game、pending selection、checkpoint、facts、templates、角色／NPC source pins、image refresh 都使用明確 scenario/version ref。待確認 V2 只安裝 V2 或明確失敗，不自動改 V3。
- Staging 完整產物經驗證才 immutable publication；同 identity retry 驗證一致，不覆寫不同 bytes。單次 context load 固定版本，不能混讀 manifest／images。
- Publication 前 fresh state 核對 timeline／generation、draft／attempt ownership、權限、明確 expected revision；保留 final state transaction／conflict checks。長 OCR 不放 conversation lock；普通 state_revision 變化不等於 lifecycle 失效。
- Filesystem／SQLite 不是跨資源 ACID。State commit 失敗可留下可診斷未啟用 immutable orphan，但不能改 selected refs。Commit 前不發布新圖。Commit 成功但 refresh 失敗須分開回報，只重試 refresh，不能誘發重做 activation。
- 全部新版本保留，包括 closed receipts／future obligations 引用；不加 pruning。上線後缺失 ref 明確失敗，不猜 latest。使用者乾淨重開前提下排除歷史來源／舊局 migration。

### 匯入恢復與生命週期

- Cached page 是 source／identity／page 一致的 snapshot，包含 selected text、diagnostics、disposition、required assets、image／map、derived description。區分本次未提供與刻意沒有資產；例外不能抹掉 accepted 資產或升級未完成頁面。
- Resume 重驗必要 graph／image；只 invalidates 受影響不相容頁面，保留 PDF／diagnostics／budget／其他 native 頁。Atomic checkpoint failure 保留舊可讀檔；description 不重複附加。
- Draft 持久化 lifecycle binding。Reset／新劇本／rollback／cancel 使不相容 ownership 與 commit 資格失效；Q5 retained inert drafts 需明確重新匯入，不能自動重綁。失效後不新發 paid requests；晚到 worker 不覆寫新 draft 或裝入另一 timeline。
- 完整 blocking OCR identity probe 移出 event loop；resume filtering／extraction 共用一份 attempt snapshot。Config／model 改變使 cache 不相容或明確新 attempt；不弱化 hashes、不允許 runtime model download。

### Transition 與歷史

Owning module 定義 transition policy，不由 handlers 各自複製清單：

| Transition | 契約 |
|---|---|
| 新劇本／newgame | 先過既有 combat／unresolved-obligation replacement guards；失效舊匯入 binding；新 timeline／opening；僅保留產品原本允許延續的 investigator 資料 |
| 同劇本切版 | 先完成 combat／check／Luck／due work；明確切完整來源；驗證 positions／claims／templates；相容 future 義務保留原來源／時序／骰 |
| 下一章 | 同版本／timeline，明確 chapter window／map context；不任意 reset 骰、戰鬥或合法 pending |
| Rollback | 還原 checkpoint ref／state 與既有 lineage rebind；舊 controls／import binding 失效；保留原骰 |

歷史保存與模型 context 分開。所有 providers／summary 投影有效目前 timeline／rollback lineage。新資料有明確 identity，不做舊 untagged-history migration。更正不假造新局、不丟骰、不追溯改寫 committed mechanics。Future 義務相容性不明即阻擋切版並明確 reconciliation，不隱式取消。

### 傷勢與故障

- Immediate major-wound CON、periodic dying CON、effect tick 使用不同 closed durable purposes。不是 dying 不得直接 resolve 尚待完成的重傷 CON；purpose-specific stop、pending ownership、完成狀態在既有 transaction 一致更新。
- Same-event／same-round／different-event retry 及 restart 保留 check／pending／roll 身分與結果。HP>0 重傷與 HP=0 瀕死分開。Manual／autoroll／多效果遵守 one-owned-check／Luck gate，不重骰／重扣／重建 CON。
- 故障綁 stable owned instance；宣告、ruling resume、execution 在新 RNG／扣彈前重查。Alias 不能繞過同一把武器；其他 owned 武器仍可用。
- 故障隨 checkpoint／provisional settlement／rollback 一致保存。清障須有證據與 explicit recorded authority，不加完整維修技能／耗時子系統。未知 NPC 故障／清障停 NEEDS_RULING；原故障射擊的耗彈遵循來源規則。

### 更正、maps、source coverage

- 整句追溯主張檢查：普通物品不能批准同句 access／clue／resource／mechanical claim。有證據遺漏與既有 conditional correction authority 保留；不只靠 blacklist、不加固定 judge／強制人類角色，不全面收緊當下合理取得。
- Graph ingestion／cache／publication 共用 type-safe schema＋語意驗證：typed unique IDs、entry／exits、必要同頁與完整來源 references。Chapter window 外不等於 source 不存在。合法單向／不連通圖不被虛構連通規則誤拒。Invalid required graph 保留 evidence／needs_review，不能發布；OCR 不替代 spatial analysis。
- Pregen 每個原始區域分類為 card input、可證明非卡或 unresolved coverage；bounded detection normalization 不改 quote／page。Table prefix／跨頁 backstory 不丟棄；明確有卡卻抽空是 incomplete，真正零卡仍合法。不重複卡、不無條件重送整本。
- Name／alias 在 normalize、persist、format 前驗型別；讀取也安全防 malformed inputs。不把 dict／list／number 轉成假名字，不把單一字串拆字元。Exact identity／duplicate guards 不改成 fuzzy。

## 驗收與檢查

使用真正 repository／save／reload、controlled provider／RNG、barrier 與 failure injection；僅 mock helper 成功不算證明 authoritative safety。

| 領域 | 最少情境 |
|---|---|
| Geometry | A,H,B 過；H,A,B 拒；多欄／多標題；overlap unresolved；文字／permutation 保留 |
| Snapshot | accepted map 後 ValueError／一般例外；atomic replace、index／pregen 失敗；資產/hash/budget 不退；缺 graph 頁重處理 |
| Lifecycle | 暫停 parse 後 reset／權限失去／cancel；晚到舊 worker／continue；同 timeline 普通行動；有效 attempt restart |
| Publication/version | stale 不改來源；A pending V2、B publish V3；同 text 不同資產；atomic load；commit／refresh 各自失敗 |
| Transition | upload／use／continue guards；future義務；新 opening 一次；全 provider context 隔離；correction／chapter／rollback 保留合法狀態 |
| Injury | 原 review probe；同／不同 event；restart；CON 成／敗一次；manual／auto；多效果；SQLite 失敗無 dangling check |
| Weapon | 同 instance 故障／restart／alias 擋；別把可用；有證據清障；commit／rollback；未知 NPC ruling |
| 其他入口 | notebook vs 無來源 grenade/access；混合句；壞 graph ID／endpoint；合法單向／跨頁；prefix表格＋兩文字卡；壞 aliases／exact duplicates |
| Async | identity 阻塞時 heartbeat 完成；attempt identity 一致；model/config 變更 cache 失效；取消不能 stale install |

真正整合 HEAD 執行 full pytest、ruff check .、mypy app、compileall app tests、git diff --check，使用既有環境；不新增 Python matrix／版本要求（Q6）。重用現有真實 CoC corpus 與 explicit offline CPU OCR setup／smoke；缺 corpus／dependency 如實記錄。Copyrighted pages 不進 commit，只保留 hashes／metrics／必要短 diagnostics。不捏造效能或 corpus passes。最終 report 逐 finding 與階段分開，分階段 merge 不等於可上線。

## 交付與非目標

參見[task graph](postmerge_ingestion_combat_integrity_tasks_zh.md)。涵蓋十三項 findings（須最新 head 核對）與 integration／catalog 保留；既有 debt 明示，不默默刪掉。排除 parser／OCR model 更換、gameplay 重設、固定 LLM judge、完整維修玩法、版本 pruning、歷史 migration、Python 相容、Agent 自動刪資料／部署；不混入獨立自動秀圖功能。

決策 frontier 已空。下一步是使用者確認最終共同理解；runtime 實作仍需明確授權。
