# 可重用劇本庫與章節脈絡

[English](scenario_library_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`feature`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 在個別對話狀態之外保存可重用來源、章節、索引、預製角色、頁圖與解析品質產物；重用劇本不必重解 PDF。

2. GroupState.scenario_text 仍是允許 context 視窗的相容快照，不代表可讀整個劇本庫；active chapter／context ID 決定安裝與檢索。

3. 防劇透政策下玩家典型視窗包含目前與下一章；明確重解析／修正保留適用遊戲位置與認領，新劇本重設劇本所屬狀態。

4. 待處理上傳選擇與預製 Luck 阻擋不相容切換；耗時解析在對話鎖臨界區外，安裝前重新查權限／狀態。

5. 圖片／地圖資產帶可見性與劇透中繼資料；搜尋／展示遵守角色與隱私政策，不能只因圖像像地圖或文字提到 map 就公開。

6. 來源／章節變更使不相容中文版失效；啟用回覆顯示後備提示，手動预製資產依來源識別協調，不跨無關劇本默默重用。

## 流程與介面

```text
上傳／暫存 PDF -> 一次解析 -> 劇本庫清單／資產 -> 選擇章節視窗 -> 安裝群組快照 -> 遊戲
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/scenario_library.py](../../../app/scenario_library.py)
- [app/pdf_loader.py](../../../app/pdf_loader.py)
- [app/legacy_commands.py](../../../app/legacy_commands.py)
- [app/commands/handlers/system.py](../../../app/commands/handlers/system.py)
- [app/scene_map.py](../../../app/scene_map.py)
- [tests/test_scenario_library.py](../../../tests/test_scenario_library.py)
- [tests/test_spoiler_policy.py](../../../tests/test_spoiler_policy.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/scenario_library_design_spec.md)
