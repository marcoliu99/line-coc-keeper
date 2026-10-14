# 補上 2026-10-14 稽核發現沒有測試的行為

[English](audit_coverage_tests_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `c6512c4`。只有測試，沒有執行期變更。

稽核發現有些行為是對的，但沒有任何測試鎖住：

- `Hatchet`、`Sickle`、`Hatchet/Sickle`、`手斧`、`鐮刀` 是同一把武器（`i.weapon.hatchet-sickle`），用 `fighting-axe`；字面的 `Club, Large` 解析為大棒，用 `fighting-brawl`（`tests/test_combat_rules.py`）。
- 擲骰結果後續回合的工具白名單提供 `sanity_check`、`apply_resolved_check_damage`、`create_triggered_check`，不提供 `adjust_character`、`skill_check`、`npc_skill_check`、`offer_check_choice`（`tests/test_resolved_check_consequences.py`）。擲骰之後的連鎖檢定與非戰鬥傷害走已授權的後果工具，不走自由格式的工具。

（後備開場 timeline 窗口的測試，放在補上那個窗口的變更裡。）
