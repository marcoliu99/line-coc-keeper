# 原子敘事購買與取得來源

[English](purchase_turn_provenance_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. Executor 確立移動完成、劇本支持的商店／庫存與負擔能力；不需地圖，Python 檢查次序／識別，不判定到店敘事的語意真實性。

2. 生活水準購買依實際信用評級與記錄的負擔理由，一起結算背包及收據，不宣稱有精確現金扣款。

3. 現金模式先保存精確報價；/coc purchase ID 明確確認商品／價格，原子扣款及取得前重查擁有者、角色、時間線、資金、待檢定與戰鬥。

4. Character.cash_balances 以各幣別整數百分之一單位記帳，不換匯；舊卡不推測資金，限 KP 的 /coc funds 登記或更正已確認餘額。

5. GroupState.commerce 保存持久報價／收據與餘額調整；每 actor 每 Executor 呼叫一購物車，使用伺服器回合識別；重複結算無作用，變更重試商品則失敗。

6. 報價在擁有者下一次 Executor 呼叫過期，有地圖位置時也重查；其他玩家行動本身不使其過期。

7. 識別為購買請求時拒絕 add_carried_item，包含中文單字買／买；此詞彙 guard 保守且非通用意圖分類，混合撿取／購買可能須分開宣告。

8. Narrator 接收本回合購買／背包事件，描述此刻買入而非原有物品；報價不代表付款或持有，只有成功擲骰證據支持禁止重骰指示。

9. 一般生活水準路徑用一個購買工具，不固定增加 LLM 審稿或到店呼叫；現金確認為確定性指令，不追溯扣除舊正式背包款項。

## 流程與介面

```text
到店／庫存裁決 -> purchase_items
  生活水準 -> 負擔能力裁決 -> 原子結算物品＋收據
  現金 -> 保存報價 -> /coc purchase ID -> 再次驗證 -> 原子扣款＋物品＋收據
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/services/purchases.py](../../../app/services/purchases.py)
- [app/commands/handlers/purchase.py](../../../app/commands/handlers/purchase.py)
- [app/agents/executor.py](../../../app/agents/executor.py)
- [app/services/turn_resolution.py](../../../app/services/turn_resolution.py)
- [app/models.py](../../../app/models.py)
- [tests/test_purchase_flow.py](../../../tests/test_purchase_flow.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/purchase_turn_provenance_design_spec.md)
