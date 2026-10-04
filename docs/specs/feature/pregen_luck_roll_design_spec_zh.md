# 預製角色幸運值所有權

[English](pregen_luck_roll_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`feature`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 最終合併角色卡有可驗證 Luck 就沿用；空白維持空白，由玩家本人在整備階段 /coc luck roll。

2. 不能用 AI 修復、建構子預設值或他人紀錄補空白 Luck；待處理預製 Luck 依流程阻擋切換／開場。

3. PDF 角色卡上印的 Luck 要「可驗證」才沿用：模型必須回報頁碼並逐字引用含標籤與數值的片段，片段所述數值要與回報相同，且屬於這位調查員。片段的每個出現位置依序用第一個能判定的規則歸屬：(a) 附近印著這張卡自己的屬性（STR/CON/SIZ/DEX/APP/INT/POW/EDU 或中文標籤，至少四項相符，且嚴格多於其他預製角色）——不論名字的順序或寫法；兩張卡近到都在範圍內時視為不確定，不猜；(b) 片段之前（同一頁，或前一頁尾端）最近的調查員名字是這張預製角色；(c) 只有這一位調查員，且片段只出現一次。恰有一處屬於這位預製角色才保留。其他情況一律捨棄（log 會寫出原因），由玩家擲骰。保留與捨棄的案例見 `tests/test_pregen_luck_verification.py`。

4. 正規化支援的技能別名，同時保留任意自訂技能；角色名與翻譯描述本身不足以證明同一角色。

## 流程與介面

```text
匯入／認領 -> 是否有已驗證 Luck？ -> 使用數值／由所有人擲骰 -> 準備完成
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/pregen_extractor.py](../../../app/pregen_extractor.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/skill_aliases.py](../../../app/skill_aliases.py)
- [tests/test_pregen_luck_verification.py](../../../tests/test_pregen_luck_verification.py)
- [tests/test_pregen_and_creation.py](../../../tests/test_pregen_and_creation.py)
- [tests/test_pregen_extra_fields.py](../../../tests/test_pregen_extra_fields.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/pregen_luck_roll_design_spec.md)
