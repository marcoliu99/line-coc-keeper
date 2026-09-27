# 角色匯入、協調與字典

[English](character_and_dictionary_system_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`feature`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. Discord 將 role_、dict_、map_ 輸入分送適當解析器，劇本 PDF 走可重用劇本庫；檔名分流不授權玩家覆蓋無關活躍狀態。

2. 角色比對有三個獨立條件，任一成立即可：姓名／別名完全相同或至少兩字的子字串；九項基本屬性中至少七項雙方皆有值且相等；或職業相符且至少三項正規化後的技能名稱與數值相同。不會進行通用音譯。屬性或職業／技能比對成功後，可學習姓名別名供後續使用。合併時保留來源與手動資料依據。

3. 規則允許時推導支援衍生數值與基礎技能；保留任意技能、年齡、開放描述與武器文字，不能全塞數值欄位。

4. Luck 使用最終卡可驗證證據，空白由本人明確擲骰；PDF 未確認核心屬性不能悄悄變建構子預設，手動更正可解除問題。

5. 候選池限劇本範圍，手動資產獨立保存；修正和遊戲轉換須妥善保留活躍調查員與已認領卡。

6. 實際模組為 pregen_extractor.py、character_matcher.py、dictionary.py；舊 dictionary_manager／character_reconciler／game_pipeline 是示意，不是現存 runtime 檔案。

7. 開場檢查就緒，區分整備與一般劇情回合；通用建角不能取代必須認領的劇本預製角色。

## 流程與介面

```text
依檔名上傳 -> 解析欄位 -> 依據／身分比對 -> 合併候選 -> 所有人認領 -> 開局準備檢查
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/pregen_extractor.py](../../../app/pregen_extractor.py)
- [app/character_matcher.py](../../../app/character_matcher.py)
- [app/dictionary.py](../../../app/dictionary.py)
- [app/skill_aliases.py](../../../app/skill_aliases.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_pregen_and_creation.py](../../../tests/test_pregen_and_creation.py)
- [tests/test_pregen_extra_fields.py](../../../tests/test_pregen_extra_fields.py)
- [tests/test_manual_pregen_persistence.py](../../../tests/test_manual_pregen_persistence.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/character_and_dictionary_system_spec.md)
