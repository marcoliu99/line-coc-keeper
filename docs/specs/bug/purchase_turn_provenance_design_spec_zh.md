# 原子敘事購買與取得來源

狀態：歷史紀錄；已由 `revert/pr91` 回退。參見[回退規格](revert_pr91_purchase_turn_design_spec_zh.md)。

[English](purchase_turn_provenance_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`bug`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. Executor 確立移動完成、有依據的商業環境與負擔能力。普通商品允許依已知商業環境做有限的 AI 採買裁定，不要求具名商店或劇本逐件列出庫存；不需地圖，Python 檢查次序／識別，不判定到店敘事的語意真實性。

2. 生活水準購買依實際信用評級與記錄的負擔理由，一起結算背包及收據，不宣稱有精確現金扣款。

3. 現金模式先保存精確報價；/coc purchase ID 明確確認商品／價格，原子扣款及取得前重查擁有者、角色、時間線、資金、待檢定與戰鬥。

4. Character.cash_balances 以各幣別整數百分之一單位記帳，不換匯；舊卡不推測資金，限 KP 的 /coc funds 登記或更正已確認餘額。

5. GroupState.commerce 保存持久報價／收據與餘額調整；每 actor 每 Executor 呼叫一購物車，使用伺服器回合識別；重複結算無作用，變更重試商品則失敗。

6. 報價在擁有者下一次 Executor 呼叫過期，有地圖位置時也重查；其他玩家行動本身不使其過期。

7. 識別為購買請求時拒絕 add_carried_item，包含中文單字買／买；此詞彙 guard 保守且非通用意圖分類，混合撿取／購買可能須分開宣告。

8. Narrator 接收本回合購買／背包事件，描述此刻買入而非原有物品；報價不代表付款或持有，只有成功擲骰證據支持禁止重骰指示。

9. 一般生活水準路徑用一個購買工具，不固定增加 LLM 審稿或到店呼叫；現金確認為確定性指令，不追溯扣除舊正式背包款項。

## 流程與介面

### 已同意的事故修正（2026-09-27）

20:44 的中文版購物回合，補查預算為 4,350 token，0.51 秒取得選定紀錄的完整依據。Executor 只搜尋一次就回傳通過驗證的 `blocked`，未呼叫購買或背包工具。整筆請求 12.54 秒，最後 Narrator 輸出被程式換成通用訊息。Log 沒有原始裁決理由，不能確定模型當下的具體原因。角色信用評級 20，沒有待檢定／Luck／戰鬥、購買收據或已確認現金餘額。

允許 AI 在劇本已確立的商業環境裁定普通合法商品採買，包含照明用途的少量油燈與瓶裝煤油。這是正典政策的明確例外，不允許憑空新增具名店家、店主背景、線索、武器、稀有／管制商品或劇情關鍵資源。仍遵守與世隔絕、停業、物資短缺、受阻路途及未完成機制。中文依據已提及商店時，不必只為尋找普通商品目錄而補查英文。沿用收據記錄商業依據、到店與負擔理由；精確價格與現金仍須確認，lifestyle 依實際信用評級裁定，不編造扣款。

新增選填 `TurnResolution.blocker_code`，供 blocked／incomplete 交接：`purchase_source_unconfirmed`、`purchase_arrival_unconfirmed`、`purchase_price_unconfirmed`、`purchase_funds_unconfirmed`。這些是有限的「尚未確認」分類，不證明商店不存在或玩家沒錢。Python 驗證固定集合，log 只記代碼，輸出固定且可接續的說明；不直接公開模型自由理由、隱藏劇情或任意下一步指令。既有待檢定／Luck、部分變更與必要依據閘門優先；舊交接沒帶代碼時沿用安全後備。不新增資料庫 schema 或固定 LLM 階段。

```text
已知商業環境＋普通商品＋可通行路途
  -> AI 裁定到店／供應／負擔能力 -> 既有 purchase_items
條件尚未確認 -> blocked/incomplete＋blocker_code
  -> Python 驗證代碼 -> 保留待處理／部分結算保護
  -> 固定文字說明還需要確認什麼
```

離線測試使用實際 Executor／工具／state 流程與模擬模型回應：信用評級 20 買油燈與兩瓶煤油、無地圖到店、有限阻擋分類與惡意自由理由、未知代碼拒絕、待處理機制、現金確認與防重複結算。提示詞測試核對共同正典及檢索規則的相同例外。測試不代表真實模型必然遵循；不付費重播或修補正式遊戲狀態。

已從 `main_v2` 的 `edd2fd6` 在 `bug/actionable-purchase-blockers` 實作。驗證：1,157 通過、1 略過、41 子測試通過；Ruff、mypy（86 個來源檔案）與 `git diff --check` 通過。不增加固定模型呼叫；商品供應與負擔能力仍由 AI 裁定，不是 Python 收據驗證器可保證的語意判斷。

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
