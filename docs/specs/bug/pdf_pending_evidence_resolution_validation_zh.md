# 待處理證據驗證

Baseline ec4554020808c0e2d5ec020597876157aa4c8cbe；implementation accb5a2c78101467482375059d1e08db4e22e413。六本 SHA256 均與原 corpus 一致。沿用 saved drafts，透過 public command router → production handle_pdf_upload sequential Continue；draft IDs/identities、消耗額度保持。

| Book | Image pending | Ordering pending | Mechanics | Final UX | Publication/start |
|---|---:|---:|---:|---|---|
| 02_Dead_Boarder.pdf | 6 | 0 | 0 | IMPORT_PENDING | Not executed |
| 03_The_Lightless_Beacon.pdf | 16 | 1 | 0 | IMPORT_PENDING | Not executed |
| 04_Camp_Sunny.pdf | 6 | 0 | 0 | IMPORT_PENDING | Not executed |
| 05_Scritch_Scratch.pdf | 14 | 1 | 0 | IMPORT_PENDING | Not executed |
| 06_Alone_Against_the_Flames.pdf | 2 | 1 | 0 | IMPORT_PENDING | Not executed |

45 頁 cache replay 已 audit，無新 classification dispatch／ledger mutation。1 頁 source-bound asset-only pregen 降為 soft；44 頁必要性證據仍不足，不能稱為已證實唯一必要 source。Partial fragment match 不算 duplicate；blank-sheet/NPC/ownership 不明不算 permission。

Ordering 9/12 經 footer geometry recovery accepted；全部 immutable blocks/text 與 numeric/mechanics tokens 保留。Beacon p13、Scritch p24、Alone p3 未解；Scritch p24 獨立 canonical alignment/source gate 保留。Scritch p23 mechanics 不重開。

新增 reservation/transport 1/1、HTTP200，使用原有最後一筆 semantic-source allowance；classification 新 request 0。累計125/125；124 HTTP200、1筆上一輪缺 HTTP outcome。Retry/refund/cap increase皆0；未修改 source discovery，也未聲稱其解除 admission。

Haunting persisted library reload、game_started 仍正常，沿用先前真實 start／兩回合 acceptance，無新 provider calls。其餘五本因 evidence pending，未執行 publication/reload/activation/start。不能誠實強迫使用者要求的 final playability category：尚無證據把 image uncertainty 稱為確定必要 source 或 importer bug。

完整 suite：2304 passed、1 skipped、152 subtests。Ruff PASS；mypy PASS140 files；compileall PASS；diff-check PASS。四種 import UX 由既有 tests 驗證；五本 actual pending UX 與 saved draft 一致。Standards／Spec 經 counterexample regression 修正後無剩餘 actionable code finding。無新增 whole-scenario blocker、無書籍特例。

MERGE／ROLLOUT HOLD：44 image necessity、3 ordering evidence 尚未完成；無新五本 start acceptance。JSON 包含45 image＋12 ordering rows，原始 evidence 保持 private。

執行歸因：review 已拒絕的中間 permission pattern 曾產生一筆 provisional optional checkpoint；最終 production Continue 前已恢復原始 unresolved source evidence，最終該頁仍 IMPORT_PENDING。原先 safe pages、reservation、refund、cap、draft identity 均未改。
