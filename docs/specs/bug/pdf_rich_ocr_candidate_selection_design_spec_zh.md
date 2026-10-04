# 保留低文字 PDF 頁面中有證據支持的豐富 OCR 候選

## 問題與目標

圖像重的頁面可能只留下頁眉、頁碼、版面標記及垂直排列的裝飾碎字。一般 `pdf_quality.select_text()` 會要求 OCR 候選保留這些短來源的字詞，因此可能整頁拒絕更豐富的 MarkItDown OCR。相同 SHA 的《The Lightless Beacon》匯入中，實體第 31、32、34、35、37、38、40、41 頁各有 1,683–4,233 字的候選，選中的非 vision 來源卻只有 87–139 字。目標是加入狹窄、可重現的本機救援規則：只有來源與 mechanics 安全檢查通過才採用。字多本身不構成通過理由。

## 範圍與不處理事項

僅修改 `app/pdf_loader.py` 的 MarkItDown 候選選擇，必要時在 `app/pdf_quality.py` 加小型 deterministic validator，並補合成測試與本規格。原生／版面來源與正常正文的 `select_text()` 維持嚴格。保留 `use_ocr=False`、Paddle OCR/Layout、Tesseract 備援、額度、provider、地圖、索引、預製角色、review warning 與遊戲行為。候選選擇不新增 LLM 呼叫，也不恢復 PR30「MarkItDown 有字就採用」。

## 候選決策

僅在現有低文字 graphic `pending` 路徑內，選中來源低於 `_LOW_TEXT_THRESHOLD`（目前 200）、MarkItDown 候選達同一門檻時，才嘗試新 validator。正常長篇正文不會進入此救援。先執行原有 `select_text()`；只有短 baseline 的**字詞覆蓋率**是唯一拒絕理由時才有救援資格。任何數值或 pair mismatch 照舊拒絕。

救援時保守分類 baseline。僅在覆蓋率比較中剔除可 deterministic 辨認的 parser 產物（例如 PyMuPDF4LLM picture-text 註解）、獨立頁碼及垂直碎字；品質報告仍保留原始 baseline。真正來源語句、標籤與數值、百分比、完整骰式／修正值，以及整組 SAN 斜線表達式都必須保留。若短 baseline 有無法明確歸為排版碎片的正文語句而候選沒有保留，以 `source_content_loss` 拒絕；不能靠劇本標題或固定頁碼猜它是頁眉。獨立頁碼不應被當成遊戲數值，但與標籤或正文綁定的數值必須保留。

沿用 native `numeric_pairs()`／`check_pairs()`；已確立的 pair 必須完整匹配，mismatch 或缺失即拒絕。mechanics 按完整 occurrence 比較：`1d6+2` 不等於 `1d6`，`1/1d6` 不等於 `1d6/1`。若適用，只沿用既有 source-aware `l/I`→`1` 骰式正規化語意，不做全文字元取代、不猜其他骰面。含替代字元或明顯損壞 mechanics 的候選不通過。

候選還需有正向結構證據，而非只有字數：帶值的 CoC 屬性／資源標籤、帶值的技能標籤，或結構化玩家表單欄位。這只是 deterministic 合理性檢查，**不是** OCR 不曾虛構內容的證明。缺乏 anchor，或無法證明來源保留時，維持拒絕與人工 review。驗證狀態至少區分 `accepted`、`candidate_too_short`、`numeric_loss`、`pair_mismatch`、`mechanic_loss`、`insufficient_anchor`、`source_content_loss`、`not_low_text_source`；名稱可依現有慣例微調。

通過時選用既有 MarkItDown 候選、設 `method=markitdown`；保留 native/layout 候選、所有原 warning 與證據。新增精簡 `rich_ocr_validation`：attempted、status/reason、來源及候選字數、mechanics/pair 檢查結果、anchor 數量，不再複製候選全文。**不修改** `_page_requires_review()`，不因選用候選自動解除歷史 warning。`pending`、vision/map 與圖片保存仍沿用現有以最終 selected text 為準的流程。

## 資料與整合

沒有 DB、API 或 publication state schema 變更。舊品質報告缺少新欄位時仍可讀。validator 僅接收短選中來源、既有候選文字、native 數值 pair 與現有門檻；不另索取 provider 證據。通用 `select_text()` 保持嚴格；救援失敗仍維持原選擇與 `ocr_evidence_loss`。

## 測試與驗證

合成案例涵蓋：豐富角色卡未複製頁眉／頁碼；屬性值衝突；骰式修正值完整性；SAN 左右順序；大量無結構自然語言；短 baseline 的真正正文遺失；大於 200 字的正常正文仍適用原覆蓋規則；空／短候選；pair 檢查。整合 fixture 確認選用的 method/text、歷史 warning 與精簡驗證 metadata，拒絕時則保持原狀。既有 MarkItDown 已選頁、正常正文、地圖圖片與 scene map 是回歸控制。

私有 43 頁 PDF smoke 前先核對 SHA-256 `14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07`。對第 31、32、34、35、37、38、40、41 頁記錄去原文的舊／新選中方法與字數、候選字數、驗證理由及 review；另核對第 3、6、13、16–19、25、27–30 頁。比較圖片、scene maps、MarkItDown/vision 呼叫及耗時，不輸出或提交劇本原文。實作後跑完整 pytest、ruff、mypy、compileall 及 `git diff --check`。不要求 8/8 通過；任何來源或 mechanics 證據不足的頁面仍拒絕。

## 待審核的取捨

短 baseline 可能只有排版碎片，整頁字詞覆蓋率過嚴；但豐富 OCR 也可能虛構數值。新規則不只看長度或「沒有 pair 衝突」，還要求結構性正向 anchor，並保留每一個可觀察、來源綁定的 mechanics 與實質語句。無法證明安全時維持原本保守路徑。
