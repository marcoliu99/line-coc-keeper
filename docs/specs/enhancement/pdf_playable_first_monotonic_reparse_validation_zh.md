# 首次匯入與單調 reparse 驗證

Code：`062a4943c979e3f2f6670e4eed944d5d2adb0057`；baseline：`2f16c016b9888e9a2026f1fe5ed380a7c675d062`。

六本原 PDF SHA256 全部相符。真實執行使用 `handle_pdf_upload`、各本隔離 storage、OpenAI `gpt-6-luna`／官方 `/v1`、SDK retry=0 與原 production caps（layout 8 requests／4 pages）。此文件與相鄰 results JSON 只保存 sanitized 狀態、hash、數量與 timing，不含 PDF、圖片、完整 source、prompt、response 或 candidate graph。

| 劇本 | 首次匯入 | Persisted reload／activation／start | 保留核心 review |
|---|---|---|---|
| Haunting | 保留既有已發布版本 | PASS／PASS／PASS | 無 |
| Dead Boarder | READY_WITH_WARNINGS | PASS／PASS／PASS | 無 |
| Lightless Beacon | IMPORT_PENDING | 未執行 | p13 ordering |
| Camp Sunny | READY_WITH_WARNINGS | PASS／PASS／PASS | 無 |
| Scritch Scratch | IMPORT_PENDING | 未執行 | p24 ordering |
| Alone Against the Flames | IMPORT_PENDING | 未執行 | p3 ordering |

五本 fresh import 共隔離 44 個未解 image review，core image blocker 為 0。這**不表示已證明 44 頁全是 optional／duplicate**；未確認內容不進 gameplay authority。三個核心 reading-order review 仍阻擋 publication。Beacon 兩個 image permutation 不符幾何；Scritch candidate 不符 numeric/dice preservation，native alignment 也有歧義；Alone 核心順序仍未確認。不能據此宣稱原 PDF 本身已損毀。Map／topology 完成度不是 admission 條件；本輪未宣稱任何真實 map verified。

五本首次 publication rate 為 2/5，兩本均 READY_WITH_WARNINGS。Haunting 保持前輪兩個真實 ordinary turn 的 acceptance，本輪重新 reload/use/start，import-time calls=0。Dead Boarder 與 Camp Sunny start 也未觸發 import-time work。

真實 Dead Boarder `/coc scenario reparse` 完成：同一 scenario、canonical source 完全不變、保留 26 個 VERIFIED 頁，upgrade/conflict/downgrade 均為 0。Classification cache replay 沒有新增 classification request。Timeline、角色及資源、checks/luck、combat、地圖位置與面向、route state、KP ownership、assistant continuation identity 均保持。新 operation 消耗的 ledger 保存在 private attempt history，沒有重設舊消耗。

本次沒有自然 improvement；synthetic integration 經 public reparse command 升級 quarantined page 並保留 live state。其他 region／certified map／source-bound pregen integration 覆蓋新增功能。改善、無改善、衝突訊息均有 regression。

所有 fresh imports、start regressions 與真實 reparse：137 個 durable analysis reservation／137 個 transport；另有 9 個 runtime logical dispatch／9 個 transport。143 個 HTTP 200；3 個 transport 未記錄 HTTP response，reservation 仍已消耗。Hidden retry、refund、cap increase、guard rejection 均為 0。既有 report 的 bounded logical retry counter 與 SDK hidden retry 不同。Reparse 消耗 11 個 analysis transport，classification request=0。

Production 成功警告及 pending UX 已核對。Pending 提供 continue/status/cancel，不直接曝露 internal reason code。Clean success、true failure、malformed input、False exit 舊 regression 全 suite 保持通過；三種 reparse outcome 另有 public-command integration。

Verification：pytest failures/errors=0、skip=1；ruff、mypy（142 app files）、compileall、diff-check 全 PASS。Standards／Spec review 無剩餘 actionable findings。已先修正重現的跨角色數值綁定及未驗證武器欄位漏洞。新增 book-specific production rule=0；未改 OCR、map、runtime architecture 或 gameplay 規則。

本 patch merge 建議 READY；production rollout HOLD，直到三個核心 ordering review 得到足夠證據，沒有為了 publication 將它們降級。已測 staging/build 失敗與 commit exception rollback；未測超出既有 publication 機制的 process crash／跨 process durability。舊 merged staged-part cleanup 與無關 review cleanup 本輪 defer。
