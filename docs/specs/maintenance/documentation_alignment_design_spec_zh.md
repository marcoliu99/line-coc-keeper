# 雙語規格對齊與分類

[English](documentation_alignment_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`maintenance`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 規格依 bug、enhancement、feature、refactor、maintenance 分類，reference、guide 與原始評估獨立；每份 spec 均有相等的英文與繁中內容。

2. 每份現行版列 baseline、實作／測試依據與狀態；implemented、partial、backlog、superseded、historical 不同，不能只因 PR 合併就認定全做完。

3. 舊提案程式、遷移歷程与過去量測可從不可變來源版本追溯；現行契約不把歷史範例當有效實作。

4. 英文檔名為主，_zh.md 對照版共享章節、流程與驗收依據；更新本地連結與讀文件的測試，不改 runtime 行為。

## 流程與介面

```text
main_v2 基準 -> 程式／測試核對 -> 現行中英規格 -> 分類／連結驗證
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [README.md](../../../README.md)
- [README_zh.md](../../../README_zh.md)
- [docs/README.md](../../README.md)
- [docs/README_zh.md](../../README_zh.md)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/dfd4838/docs/documentation_alignment_design_spec.md)

## 整理驗證（2026-09-27）

- 62 組規格（124 份）：55 項已實作、1 項部分實作、3 項待實作、2 項已被取代、1 項歷史紀錄。
- 索引每項都有英文／繁中配對且條款數相同；所有本地 Markdown 連結與程式／測試依據路徑均有效。
- 修正過時的舊 Keeper 入口、固定 Help 指令數、不存在的範例模組、角色比對條件，以及待裁決更正的暫停語意。批次檢索與三項未合併增強仍明確列為未實作。
- 完整隔離回歸測試：910 通過、1 跳過、33 個子測試通過。本次文件核對未進行真實 API 效能測試。
- Python AST 比較（忽略 docstring）確認執行邏輯沒有變更；唯一可執行的測試修改，是將產生式 Help 參考比對指向搬移後的中文路徑。

行為變更時，請在同一 commit 更新兩種語言與索引。英文模板保留技術識別字與解析器要求的中文字面值。使用 `python3 -m app.help_docs --output docs/references/player_command_reference_zh.md` 產生中文指令參考，再同步更新英文版本。

完整歷史 changelog 保留於 `docs/history/changelog_zh.md`；目前 changelog 提供中英摘要。每份現行規格以不可變 Git 版本連結保留歷史附錄，避免將歷史內容當成現行需求。
