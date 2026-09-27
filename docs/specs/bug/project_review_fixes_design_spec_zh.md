# 劇本生命週期 review 修正

[English](project_review_fixes_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 預製角色 Luck 或 PDF／劇本選擇待處理時，拒絕切換劇本並保留原狀態，避免將流程遺留在新劇本。

2. 匯入、合併與重解析不能重入同一把不可重入的對話鎖；耗時解析位於短暫安裝臨界區之外。

3. SCENARIO_LIFECYCLE_KP_ONLY 預設 false；啟用時文字命令與 PDF callback 重新驗證目前 KP／Discord Keeper 權限，不能描述成無條件限 KP。

4. 同步狀態讀取移出 Discord event loop；公開錯誤使用固定文案，詳細例外寫入 log。

## 流程與介面

```text
上傳／指令 -> 授權與待處理狀態檢查 -> 鎖外解析 -> 受保護的安裝
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/commands/router.py](../../../app/commands/router.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [app/config.py](../../../app/config.py)
- [tests/test_scenario_library.py](../../../tests/test_scenario_library.py)
- [tests/test_bot_lifecycle_scripts.py](../../../tests/test_bot_lifecycle_scripts.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/project_review_fixes_design_spec.md)
