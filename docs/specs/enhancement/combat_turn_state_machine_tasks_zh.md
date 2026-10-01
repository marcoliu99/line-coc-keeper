# Combat implementation 工作圖

[Canonical design](combat_turn_state_machine_design_spec.md)。操作者於 2026-10-01 以 `$implement-spec` 授權。Repo 不使用 GitHub Issues；[英文 tasks](combat_turn_state_machine_tasks.md) 是完整 ticket graph／API contract，EN 為 canonical。

T1 Catalog、T2 Dice、T3 Working state 可並行；T4 Combat action runner／injury 依賴 T1–T3；T5 所有 resource／check／tool／router wiring 依賴 T3 並與 T4 API 對齊；T6 在 T4／T5 合併後做完整 persistence／restart／settlement／postcombat integration；T7 做雙軸 review、同一 implementer 修所有 findings、完整 checks 與 PR ready。

各 implementer 以自己的 published branch／worktree 實作，merger 合併至 enhancement/combat-turn-state-machine。Shared state 不分叉 store，重用 keeper.mutate_tool_state 與 repository atomic group-state／mirrors。新模型須 typed closed values、stable IDs、backward-tolerant serialization；legacy active battle 不猜 prebattle baseline，不能丟 pending／history。

T1：reviewed／pinned weapons、scenario override、aliases／ambiguous routing、distance／DB／impale、severity explicit lookup、provenance。T2：bounded composite dice／maximum，保留既有 RollResult／impale API，不 eval。T3：effective resource reads／writes、baseline conflict、append events／receipts、settlement／rollback／correction、postcombat serialization。T4：supported player／NPC runner、明確 waits、injury／timed effects、retry 不重骰。T5：所有 legacy resource paths、既有玩家 controls、high-level tools、bot-only admin gates、queries／prompts。T6：restart、atomic mirrors／absolute commit、duplicate／ownership、conflict、postcombat／next-battle、no half-provisional path。T7：全測與 review findings 清除後才 ready。

狀態同步以英文 ticket table 為準；完工後兩份文件與 design／catalog 一起更新。此次授權包含建立 draft PR 與實作；沒有 merge／deploy 授權。
