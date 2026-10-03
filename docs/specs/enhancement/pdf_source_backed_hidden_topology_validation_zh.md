# Source-backed hidden／conditional topology 驗證

分支 enhancement/pdf-multicolumn-ingestion；review baseline 1347c3e；implementation 49b7e9b；blocking review fix df5ec9c；日期 2026-10-02。

## 修改檔案

- app/pdf_source_topology.py
- app/map_routes.py
- app/pdf_map_analysis.py
- app/pdf_map_evidence.py
- app/pdf_loader.py
- app/scene_map.py
- app/models.py
- app/scenario_library.py
- app/commands/handlers/map_handler.py
- app/commands/router.py
- app/legacy_commands.py
- app/keeper.py
- tests/test_pdf_hidden_topology.py

中英 spec、catalog 與本 sanitized validation evidence 一併更新。

## 已驗證 production behavior

25 個新增 regression 經 production source extraction、visual graph build/audit/certificate replay、PDF loader、publication/library read、private draft、GroupState persistence、真實 command routing、map runtime。Image inference 與後續 AI turn 使用 stub；這些是 regression evidence，不代表真實 PDF vision 驗證。

- Hidden passage／secret door 與 breakable wall／blocked passage／sealed door／collapsible barrier／explicit conditional route 分開。Hidden 預設 hidden/undiscovered；barrier 預設 blocked。
- Source authority 綁 final canonical source、精確 inventory endpoints、physical page/spans、完整 source/span hashes。Private challenger prose 與共享牆不授權連線。Vision schema 保持 visual-only，wall 與 source-only traversal kind 被拒絕。
- Condition 保存明確來源文字及 route-scoped world-state key；不自行新增難度、HP、armor、工具門檻。Fail-forward 需來源明確 necessary-for-progress，失敗仍可重試。
- Synthetic Corbitt-named route 在 image audit 回報實牆時保留，因 audit 只查 visual graph。初始仍不可通行；明確 two-way barrier 共用 condition，開通後可雙向。
- 普通 visible exits 與 /coc where 不顯示未發現連線；KP discovery 後才 available。Named/directional movement 不穿 blocked barrier。Narration 說牆破了不會改 world state。
- /coc route page route-id opened|discovered|failed [已裁定 consequence] 只允許 KP，重新查完整 library source/map certificate，持久化 timeline/graph-bound outcome。它接在正常 action/check/damage workflow 後，不因任意 roll 自動 transition。Static topology/certificate 不被 gameplay availability 修改。
- Certificate extension 綁 visual/source topology/condition metadata/canonical source/merged hashes。Deterministic replay 拒絕改證據後重算 hashes 的偽造；library source 改變時 map quarantine，但 scenario source 仍可載入。
- Resume 丟棄 source overlay，只 reuse visual proof，待全書 final source 選定後重建，新增 provider requests 為 0。已辨識但端點不明的敘述只使 map incomplete，scenario 仍 READY_WITH_WARNINGS。
- Beacon-named Service Room／Lamp Room／Lantern Gallery fixture 的正常 stairs/door movement 與 visual-only certificate 不變。

## Review

Standards：0 documented breaches；certificate TypedDict 為可 defer P3。Spec review 找到普通 visible door 繞過 matching source-sealed barrier 的 P1；df5ec9c 修正 visual projection、named/directional gates，並測 failed/opened persistence。最終 Spec：0 remaining actionable findings。不同方向的 visible route 不能授權未發現的 hidden route。

## Checks

pytest：2147 passed、2 skipped、10 dependency deprecation warnings、152 subtests passed，29.98 秒。Ruff PASS；mypy app PASS（137 source files，保留既有 annotation-unchecked note）；compileall app tests PASS；git diff --check PASS。最新 origin/main_v2 為 ancestor，無 unresolved conflicts。

## 限制與 rollout

本輪 provider image requests=0，未重跑 Haunting p7／Beacon p16，synthetic regression 不能宣稱 real maps verified。Production rollout 維持 HOLD，待 real map/source evidence 與既有 rollout gates。Extractor 僅支援保守英文 grammar、精確已知 inventory endpoints，不保證任意散文／語言 coverage，不建立 invisible room。Raw PDF images、provider outputs、actual candidate graphs 不加入 repository。


## Availability 後續修正（1723a31；review base 7fa3db6）

重現另一個 P1：activated source route 可繞過同一連線上另一個 blocked barrier。現在 activated source exits 與 named movement 也共用既有 barrier gate。方向不明不能據此認定有繞過已知障礙的替代路線。新增 4 個 regression：只解除任一障礙與全部解除、實際 library/KP persistence 的 failed/opened、unknown compass、明確不同方向的 available route。最終 Standards／Spec follow-up review 均無新增 actionable findings。上方 full checks 已更新為本次結果；未增加 provider requests。
