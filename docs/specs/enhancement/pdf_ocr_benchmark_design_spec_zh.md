# 本地 PDF OCR 實證測試

狀態：backlog（等待 benchmark 規格審查）

## 目標與現況

以真實 CoC PDF 證據判斷 PaddleOCR 是否值得取代 Tesseract 成為主要本地 OCR；本次不整合 production。分支基於 main_v2 `189bc8e`；PR155 尚未合併，參考版本 `ead28a4`，執行前重核兩個版本。

已核對 pdf_loader、pdf_quality、pdf_ai_repair、PR155 的 pdf_layout／pdf_layout_adapters、markitdown_shim、scene_map、既有 PDF tests 與 validation。既有報告只有候選統計及一個 synthetic 修復，不能證明真實 OCR 正確率。Docling 維持 do_ocr=False、do_table_structure=False；MarkItDown 與地圖辨識分別維持自己的流程。

## 範圍與資料

新增可移除的 scripts/experiments/benchmark_pdf_ocr.py、獨立依賴及測試；不修改任何 app runtime、正式 requirements、Keeper、RAG、戰鬥或地圖。不得呼叫完整 extract_text／provider，避免意外啟用外部分析。無 Camelot、雲端 OCR 或文件上傳。

使用 Downloads/PDF文件 的三個原始 PDF：The_Haunting_Scenario_trimmed.pdf（27頁）、Dead Boarder.pdf（32頁）、The Lightless Beacon - Call of Cthulhu.pdf（43頁）；完整 SHA256 在英文版。

預選實體頁：Haunting 11/12/14/15/17/18/20/22/24；Dead Boarder 17/18/20/21/23/25/27/29/31；Beacon 13/16/17/23/24/26/27/28/29/30/31/34/37/40；正確正文對照 Dead5、Beacon6。地圖頁只測文字，不測 graph。執行前固定最終選頁與排除原因，不依結果挑勝出頁。

原生 PyMuPDF 只是來源候選；損壞文字不可當真值。直接檢視 PDF 渲染建立必要數字、配對、技能與骰式 gold；掃描角色卡使用可視核對的有限區域，不用任一 OCR／AI 輸出當真值。無法核對者保留未評分原因。真實頁數按實際執行的不同實體頁計算。

完整轉錄、圖片及 raw OCR 留在 repository 外；commit 僅放來源／輸入 hash、頁碼、bbox、指標、必要短 token 診斷與錯誤。Synthetic 與真實結果分開；合成樣本不可支持 production 勝出結論。

## 公平輸入與環境

主要是 region：重現 unresolved pair block 或 replacement character 的選取，bbox 每邊加2點、與頁面交集、300 DPI；兩引擎使用同一 PNG。旋轉頁按現有政策另記跳過。掃描卡手動選取區域標記 representative，不冒稱現有 native suspect 選取。全頁300DPI 是次要結果，記錄同一输入 hash／座標。

Tesseract 直接使用現有 _ocr_image、語言順序 chi_tra+eng→eng。記錄 pytesseract／CLI 路徑及有效 PSM：前者預設，後者 --psm6，不得混稱同一設定。記錄版本與語言模型 hash；缺依賴／語言包是 failure。英文限定與調參只能當獨立敏感度測試。

Paddle 明確 isolated setup 安裝官方穩定相容版本，鎖定 package／Python／model ID／hash、語言、CPU backend、方向與 threads。檢查 macOS arm64 及專案 Python3.14 相容性；必要時另用支援的 Python，但報告部署差異。模型僅 setup 下載，指定本地 cache；推論必須離線使用明確模型路徑。安裝／初始化／推論失敗要獨立記錄，禁止改用 Tesseract 冒稱 Paddle 成功。

Worker／page 有 timeout 並清理程序。初始化 cold 與首推論分開，warmup 後至少三次 warm 重複、交替引擎順序。每筆 timing、median／p95、RSS（註明OS單位）、可取得的CPU、下載大小／時間、主機版本都保留。未實際在 Linux 執行不得宣称 Linux 驗證通過。

## 指標

重用 pdf_quality.block_evidence、numeric_pairs、check_pairs、_NUMBER、normalize、accept_region，不改 production。每區／頁與總體皆列分子分母與成功覆蓋率。

- 數字採 occurrence multiset：exact recall 加新增數字數量；重複次數也要比。O/0、I/1 不修正後算成功。
- 既有 _NUMBER 不完整涵蓋 d100、+DB 與百分號邊界；benchmark 額外測完整骰式／百分比 token。嚴格原樣與忽略大小寫的骰式分數分開，不接受 ld6 當1d6。
- Label/value 必須名稱、值及次數正確；區分 missing、mismatch、來源不明及幾何不明。另以 OCR bbox 轉回 PDF 座標輔助，保留歧義。現有文字 validator 与幾何輔助分數分開，不能偷偷重排表格換取成功。
- 技能名稱／specialization 與百分比都必須對；只保留数字仍算錯。空白 Luck 不得補值。
- Text coverage 使用已核對區域的詞 multiset；完整 gold 才計 CER。普通 prose 不得抵銷數值錯誤。
- 新增數字／骰式／label 經可視來源確認才算 severe；未核對範圍只能標 unverified。保留否定／條件診斷。

accept_region 另列 eligibility／accepted／rejection，不能混成 OCR 品質。它要求原文有 replacement character；讀對 unresolved 表格未必符合接受條件。本次只報限制，不改 validator。

## 報告與結論

產出 pdf_ocr_benchmark_results.json、pdf_ocr_benchmark_validation.md／_zh，包含 revisions、manifest、setup、engines、real_pages、synthetic_pages、failures、每指標分母、region／full-page、cold／warm、severe regressions、未核對範圍。Failure 不可當零耗時成功或從完成率消失。

每頁列 Paddle win、Tesseract win、tie、both failed、incomparable／unscored。先比較核心正確性与 severe errors，不用總文字平均蓋掉問題；無gold的指標不能當勝利。

A：真實 region 多種核心類型穩定改善且無新增严重錯誤；C：只在特定類型有可重現改善；B：不足以支持增加依賴；D：參考／環境／覆蓋不足或結論混合不明。列 absolute counts、每類率與配對差異，不強選贏家。若A/C，僅提出 _ocr_image→Paddle→deterministic validation→Tesseract fallback 的後續最小方案與 tests，不實作，保留其餘既有 pipeline。

## 流程及驗收

原 PDF → 固定頁／crop manifest → 同300DPI PNG → isolated OCR workers → 私有 raw/gold → 既有驗證與嚴格指標 → 脫敏 JSON＋中英報告 → 工程建議。

測試包含替字／新增／重複數值、配對交換、技能交換、d100／+DB、零分母、partial gold、旋轉／座標、timeout／failure、不偷換引擎、彙總分類及報告脫敏。Committed tests 只用 synthetic。執行全 pytest、ruff0.16.8、mypy app、compileall app tests scripts/experiments、diff --check；記錄真實結果。規格階段尚未執行 benchmark 或 runtime gates。

依 branch-spec-workflow，規格確認後才寫 benchmark、安裝及推論。模型相容性與 gold 覆蓋是待量測風險。

## Execution authorization — 2026-10-01

User approved evidence-only execution. Compare Tesseract, en_PP-OCRv5_mobile_rec and PP-OCRv5_server_rec on identical real English inputs. Prioritize exact numeric/dice/skill quality over speed; defaults confer no production preference. Use PaddleOCR 3.7.0 and officially documented macOS CPU PaddlePaddle 3.3.0 in isolated Python 3.11 (project Python 3.14 is a deployment mismatch). Download models only in an explicit setup action without document inputs; inference requires explicit local model paths and OS network denial. Record package/model/cache identities and setup requirements. No cloud calls or application/runtime changes; no production integration.
