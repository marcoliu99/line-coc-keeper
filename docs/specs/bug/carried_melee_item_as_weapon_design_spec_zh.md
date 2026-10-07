# 隨身攜帶的近戰物品可以在戰鬥中當武器用

[English](carried_melee_item_as_weapon_design_spec.md) | [文件索引](../../README.md)

類別：`bug`。狀態：**已實作**。基準：`main_v2` 的 `0c2b022`。

## 問題

在一次《The Haunting》實跑中，調查員承翰帶著警棍，輪到他時用警棍攻擊敵人，回覆卻是「這個行動無法進行」。戰鬥引擎有兩個原因：

- 已核對的武器表裡沒有這個名稱。對應的條目是「Club, Small」（警棍／night stick），但它原本只有「small club」這個別名。
- `declare_action` 對徒手以外的武器，要求角色有已登記的持有紀錄（`weapons` 或武器實例）。`carried_items` 裡的物品不算，所以角色實際拿著的警棍被當成「沒有持有」。

## 修改

- `app/data/combat_weapons.json`：「Club, Small」加上別名 `nightstick` 和 `警棍`。
- `app/combat_flow.py`（`_weapon_actor_evidence`）：不使用彈藥的近戰武器，只要調查員有一件隨身物品的名稱，跟武器名稱、宣告時用的名稱或它的別名之一完全相同，就視為持有（要整個名稱相同，所以「棒球手套」不會被當成球棒）。

## 不做

槍械和任何用彈藥的武器，仍然需要有彈藥的持有紀錄。其他隨身物品（手杖、椅子）仍然沒有武器定義，仍須守密人裁定。除了這個別名，不新增武器表條目。

## 測試

`tests/test_combat_engine.py`：隨身帶著警棍的調查員用它宣告攻擊，會得到「Club, Small」的定義；沒有持有判定的修改時，這個測試會失敗。
