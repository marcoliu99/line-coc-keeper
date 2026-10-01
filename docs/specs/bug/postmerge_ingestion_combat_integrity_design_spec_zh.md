# 合併後匯入與戰鬥完整性修復

[English](postmerge_ingestion_combat_integrity_design_spec.md)

狀態：backlog，訪談進行中。這些答案尚未授權 runtime 實作。討論前提：PR155、PR156 均已整合進主線。文件實際 base 是 main_v2 6024adf（PR156 已合併）；查核時 PR155 仍 open、head 1c93712。未宣稱已執行完整兩支合併版本。

## 來源與證據

使用者 review：/Users/marcoliu/Downloads/pr155_pr156_full_code_review.md，2026-10-01，固定 PR155 cf62607、PR156 897409d。必須先核對目前 heads，再視為 regression。PR156 1f89f6b 的隔離 probes 確認 R156-01（重試使戰後重傷 CON 失效）及 R156-02（已故障且已持有的槍仍進 PLAYER_ROLL）；後者僅重現宣告，未完整跑故障／維修流程。PR155 1c93712 已靜態核對：後續 native-anchor 修正沒有變更 R155-01～11 路徑；R155-06 已由 PR156 replacement guards 部分改善。未宣稱新增動態或兩支合併驗證。舊 merge conflict 是整合歷史，不當成合併後 runtime 缺陷。

## 已確認決策

- Q1 A：分階段交付。先修檢定生命週期、accepted 資產保留、過期發布、deterministic reading order；再完成來源版本／生命週期遷移、其他領域入口及發行驗證。每階段有可執行驗收與 remaining findings，不把部分修復描述為全數完成。
- Q2 A：遊戲固定明確選用的劇本版本；另一群組重新解析不會自動更新它。切版必須明確且一致處理文字、圖片、地圖與 NPC／角色來源證據。

- Q3 已取代：使用者會在上線前刪除舊劇本，故歷史劇本／來源版本 migration 移出範圍。Q7 已確認一併清理相關遊戲進度、草稿、checkpoints 與資產。Agent 未執行任何刪除。新建立資料仍須 immutable refs、安全重試與生命週期失效。
- Q4 A：切版前先完成目前戰鬥、玩家檢定與 Luck。未解持續義務須明確處理相容性，不得清空；future obligations 的詳細政策為下一個決策。
- Q5 A：newgame／切新劇本使舊 worker 與其啟用 binding 失效，保留草稿、accepted assets 與診斷。重用必須在新 context 明確重新匯入／admission，不能自動重綁或過期發布。
- Q6：依使用者要求排除。不納入 Python 相容修正、版本要求變更或額外 runtime matrix，維持既有驗證環境。

- Q7 A：使用者會在上線前清理舊劇本及相關遊戲進度、草稿、checkpoints、圖片／地圖，乾淨重開。排除歷史劇本／版本及舊局恢復 migration。Agent 未執行清理或刪除；上線前须確認全新資料前提成立。新資料仍需 durable restart／retry 契約。

## 保留契約

保留 ADR0003 整場 provisional、bot Keeper 結算／回滾、原骰收據、due ownership 與 future obligations；保留 ADR0002 presentation／authority 分離。不新增固定每回合 judge、不自動清空戰鬥、不猜歷史來源、不把解析／來源發布成功混成遊戲已啟用。OCR 留在 import-time，遊戲使用已驗證匯入證據。

## 待細化設計與驗收

Logical scenario 保留穩定身分，published versions 不可變；runtime／rollback 固定選用版本，不讀會移動的 latest pointer。依更新後 Q3 排除初次歷史來源 migration；首次清理依 Q7；未來版本保留仍待確認。共用來源副作用前先核對發布資格；state commit 成功與 derived-image refresh 失敗分開回報。

驗收覆蓋 restart／retry／failed-save 與真實 repository boundaries。Review 狀態區分 confirmed、already_fixed、not_reproduced、blocked。外部 review 的測試數字是歷史資料，不是這次未實作工作的驗證結果。

## 尚待確認

同劇本更新時的 future obligations、追溯日常物品更正、故障／維修支援範圍、來源保留／cleanup。後續恢復與發布 gates 必須遵循已確認決策；runtime 變更前需要最終共同理解確認。
