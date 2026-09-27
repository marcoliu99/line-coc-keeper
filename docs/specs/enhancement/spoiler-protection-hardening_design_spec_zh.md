# 獨立劇透與隱私政策

[English](spoiler-protection-hardening_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. SPOILER_PROTECTION_ENABLED 與 PRIVACY_ISOLATION_ENABLED 獨立且預設 true；前者控制揭露節奏，後者控制資料所屬／私人目的地。

2. 政策套用劇本章節、索引／預製角色、場景摘要、NPC／敵人資料、handout 與圖片工具；內部檢索可讀不代表公開回覆可帶 KP-only 內容。

3. 保留 public／private 投影與實際 callback 檢查；prompt 是補充，不替代確定性過濾。

4. KP 備團與 OOC 權威不自動授權公開玩家秘密；停用揭露節奏不能連帶停用隱私隔離。

5. 劇透過濾不證明敘事真實，也不機械驗證新世界事實；正典邊界、更正回條與證據交接仍獨立。

## 流程與介面

```text
角色／上下文 -> 章節與資產政策 -> 私人／公開投影 -> 輸出過濾
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/spoiler_policy.py](../../../app/spoiler_policy.py)
- [app/keeper.py](../../../app/keeper.py)
- [app/agents/supervisor.py](../../../app/agents/supervisor.py)
- [app/agents/assistant.py](../../../app/agents/assistant.py)
- [app/scenario_library.py](../../../app/scenario_library.py)
- [tests/test_spoiler_policy.py](../../../tests/test_spoiler_policy.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/spoiler-protection-hardening_design_spec.md)
