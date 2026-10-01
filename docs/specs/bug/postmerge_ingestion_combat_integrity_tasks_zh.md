# 合併後完整性修復 task graph

[Canonical design](postmerge_ingestion_combat_integrity_design_spec.md)／[English tasks](postmerge_ingestion_combat_integrity_tasks.md)。全部 tickets 為 planned，待最終實作授權；不使用 GitHub Issues，尚未開始 runtime。

T0 真正整合基準 → T1 傷勢與 T2 版面／資產可並行 → T3 匯入 lifecycle／preflight／identity → T4 immutable versions → T5 transition／history／future obligations 與 T6 graph／card／NPC boundaries → T7 fault／correction → T8 整合驗證 → T9 review／release gate。

| Ticket | 依賴 | 範圍與驗收 |
|---|---|---|
| T0 | 授權 | Pin 真正 main／兩支 PR heads，保留 specs／catalog，逐 finding 證據分類，不假裝已跑合併版本 |
| T1 | T0 | R156-01：closed check purposes、exact retry／restart／transaction failures、原 RNG |
| T2 | T0 | R155-01/02：對稱 geometry、accepted 完整資產、checkpoint／下游失敗恢復 |
| T3 | T1,T2 | R155-03/05/11：副作用前 fresh資格、inert drafts、late worker、一份 offloop identity |
| T4 | T3 | R155-04：完整 immutable version、全部 pinned consumers、雙群交錯；不做舊 migration／pruning |
| T5 | T4 | R155-06：所有入口 guards、切版安全、future義務相容性、rollback lineage、opening一次 |
| T6 | T4 | R155-08/09/10：graph型別／references安全、卡片coverage、malformed aliases／exact lookup |
| T7 | T5,T6 | R156-02 stable-instance fault／clearing／alias；R155-07 whole-claim review、不加fixed judge |
| T8 | T7 | 真正 public/repository整合 tests、全部 checks、現有真實 corpus／CPU smoke、雙語驗證與逐 finding結果 |
| T9 | T8 | 雙軸 review／修正、最終 checks、docs／catalog、乾淨重開前提；不擅自刪除／部署 |

分階段可 review：T1–T3 先封堵；T4–T5 新來源生命週期；T6–T7 領域入口；T8–T9 全面驗證。共用檔案依 dependencies 避免並行修改；可拆 commits，不弱化完整契約。Q12 要求本批全數驗收完成才正式上線。一次性舊資料清理由使用者執行，不能因此放寬正常 runtime combat guards。
