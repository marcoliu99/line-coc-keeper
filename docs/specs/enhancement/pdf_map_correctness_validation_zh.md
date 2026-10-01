# PR155 graph correctness 本輪驗證

Production rollout 維持 **hold**。Production code 與 regression tests 已完成，但新版本 provider-enabled 地圖／image-only 實驗尚未執行。自動核准審查拒絕將真實 PDF 衍生圖片上傳至設定中的 OpenAI API（`api.openai.com`），理由是缺少圖片內容及目的地的明確授權；沒有改用其他 transport 或 provider 繞過拒絕。

## 修改

- `app/scene_map.py`：完整 defensive structural validation，檢查唯一合法 ID、必要且存在的 entry、local／cross-map target 語法及 compass。Duplicate／self／conflicting／asymmetric edges 留 diagnostics，單向連線仍合法；未修改 movement runtime。
- `app/pdf_map_analysis.py`：分開 NOT_ANALYZED／ANALYSIS_FAILED／GRAPH_MISSING／INVALID／INCOMPLETE／VERIFIED。結構通過後，以原圖盤點位置並 audit 每個 room／exit／entry。缺少可見標籤為 incomplete；有實牆穿越或 unsupported node／edge 證據為 invalid。Provider audit 是 evidence，不能代替人工 ground truth。
- 每次 analysis 最多一次 repair，沿用既有共享 durable request budget。輸入原圖、graph、deterministic／image-audit errors；不得改 source transcription 或虛構 room／入口。Repair 後重新 validate／audit，不能刪掉先前 evidence 的位置來通過。入口無法確認時保留 blocked private draft。
- `app/pdf_loader.py`、`app/scenario_library.py`：unverified candidate 只存 private；寫入 gameplay library 或重用 draft cache 前，檢查 graph／image／PDF-bound certificate。Map description 不混入 canonical source text。Pipeline identity 為 `multicolumn-v6`。
- `app/pdf_ingestion_drafts.py`：Continue 保留各次 graph attempt 的輸出、錯誤、時間、hash，存 private provenance history。
- OCR metrics 補 page attempts、independent agreements／conflicts；map report 保存 candidate／attempted／failed／generated／invalid／incomplete／repaired／verified counts。
- CPU smoke 使用 hash-pinned RGB 圖，保存 font／DPI／size／antialiasing provenance，實際測試 worker network denial。Neutral inference 與 exact mechanics rejection 分開判定。macOS Apple Silicon 是必要 production gate；Linux 是 optional portability。

## 真實地圖證據

| 頁面 | 使用者已確認的先前結果 | 本輪 code 的 provider 結果 |
| --- | --- | --- |
| The Haunting p7 | 20 rooms／44 directed exits；entry 不存在；地下室有 unsupported 實牆出口 | 尚未執行：最終 rooms／exits／entry、repair 次數及 Corbitt 實牆正確性未驗證 |
| Lightless Beacon p16 | 9 rooms／15 directed exits；stairs 指向不存在的 Lamp Room；Service Room／Lamp Room／Lantern Gallery 缺漏 | 尚未執行：三個位置與 stairs topology 未驗證 |

本輪真實 map status counts 為 unavailable，不是 0，也不使用 mock counts 宣稱成功。`pdf_map_correctness_results.json` 明確保存此狀態。Tests 證明 structural rejection、bounded repair 成功／失敗、wall-evidence rejection、missing-location rejection 及 invalid graph 不會發布；不能據此宣稱兩張真實地圖正確。

## OCR 證據與 gates

| 項目 | 結果 | 範圍 |
| --- | --- | --- |
| macOS Apple Silicon CPU positive | PASS；neutral candidate 正確、offline cache、實際 worker socket calls denied | 必要平台 gate；synthetic fixture，不是 real-page acceptance |
| macOS mechanics safety | PASS；實際 `ld6+2` 被拒絕 | numeric／dice gates 未放寬 |
| Linux x86_64 CPU positive | PASS | optional portability；GitHub run 36861007223、commit 403b339 |
| Linux mechanics safety | PASS；實際 `ld6+2` 被拒絕 | 與 macOS 使用相同 pinned PNG |
| 既有公平 Paddle OFF／ON | 102 real pages；review 50／50、blocked 48／48；無 safe-page regression | 先前 `multicolumn-v5`，原紀錄保留 |
| 最後版 code targeted OFF／ON | 9 個既有 safe pages，兩組均 9／9 accepted；selected-text hash 相同；numeric／dice／pair errors 均 0 | `multicolumn-v6`；兩組 provider OFF、hidden OCR OFF、Docling OFF；沒有重跑完整 corpus |
| Image-only agreement／conflict／local-only | Regression PASS：authoritative／unverified／unverified | Provider mocked，不是新 real authoritative evidence |
| Pure illustration | Regression PASS；Tesseract diagnostic noise 不會阻擋 | 既有真實 Beacon p5 紀錄保留；本輪 provider recheck 待執行 |
| 真實 image-only authoritative positive | PENDING | 已備妥 Haunting p19 原整頁與 hash-bound Dead Boarder p17 crop；crop 成功只能證明該 bounded derivative，不能代表整張原頁 |
| 真實 image-only conflict | 先前 Haunting p20 正確為 unverified | 本輪 provider rerun 待執行；未放寬 safety |

Targeted sample：Haunting 3／6／12、Dead Boarder 5／6／11、Beacon 6／10／15。`scripts/experiments/validate_pdf_safe_page_sample.py` 可重現 isolated subset，公開紀錄僅包含 sanitized metrics。原始 source text、candidate graphs 與 provider responses 保持 private。Controlled evaluation 不發布 scenario。

## 工具檢查

本地 **2039 passed、2 skipped**；Ruff pass；mypy pass（132 source files）；`python -m compileall app tests` pass；`git diff --check` pass。GitHub implementation CI 36861007088 pass。Provider architecture、Paddle model／safety gates、Docling OCR／table flags、hidden OCR policy 保持原設定。

## Standards

Review 找到一處舊函式名稱註解，已修正為 `_resolve_map_action_core`。兩項 heuristic maintainability 建議保留：legacy visibility fixture setup 重複、untrusted audit／provenance 使用寬鬆 dictionary。兩者不是 correctness failure 或 tooling violation。

## Spec

按必要 entry 的明確要求與最新平台 gate override，review 無重大 implementation mismatch。Real provider evidence 仍待取得；沒有以 mocked result 宣稱 rollout ready。

Review counts：Standards 一項 documented violation 已修、兩項 heuristic observation；Spec 零項 material findings。Production rollout **hold**，直到兩張真實 map verified、Corbitt 無實牆出口、Beacon 三個位置／stairs 正確，以及至少一個真正 image-only authoritative positive 成功。
