# 劇本來源與模板版本儲存

[English](scenario_source_store_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`refactor`。狀態：**已實作**。基於 `main_v2` 的 `ee23aa9`；2026-10-05 架構審查的第四項（「隱藏劇本來源的儲存細節」）。

`scenario_templates` 原本會伸進 `scenario_library` 取用私有的路徑驗證與 JSON 讀取，再自己組路徑：來源的 `manifest.json` 與 `scenario.txt`、`.variants/<劇本>/<來源雜湊>/<語系>/<版本>` 目錄樹、一個版本的五個檔案，以及 `exports` 目錄。`scenario_source_review` 也呼叫 `templates._root()` 與 library 的私有頁碼正規表示式。每個呼叫者都得知道儲存結構，改動一處就要改好幾個模組。

## 契約

`scenario_library` 現在提供來源與版本的介面，檔案留在裡面。

| 函式 | 提供 |
| --- | --- |
| `read_source(id)` | manifest 與文字，並以 manifest 的內容雜湊驗證（例外同前：`FileNotFoundError`／`ValueError`） |
| `source_manifest(id)` | 只取 manifest；不存在時為 `{}` |
| `variant_manifests(id)` | 一個劇本所有可讀的版本 manifest |
| `read_variant(id, source_hash, locale, variant_id)` | manifest 與 records；任一缺漏或格式錯誤時 `FileNotFoundError` |
| `variant_exists`、`variant_stamp` | 是否存在；快取索引建立時所依據的檔案識別 |
| `publish_variant(..., VariantDocuments, still_current=)` | 一次建立一個版本；檔案寫完、出現之前會問 `still_current`，所以期間來源被重新解析時不會留下任何東西 |
| `write_variant_manifest` | 原子地替換 manifest（核准） |
| `exports_dir(id)`、`remove_variants(id)` | 外部整備套件；移除所有版本 |

library 會驗證劇本 ID、64 位十六進位來源雜湊，以及單一路徑片段的語系與版本 ID，所以位址無法指到版本目錄之外。`scenario_templates` 保留屬於它的部分：版本 ID 格式、compiler／schema 版本、記錄內容、coverage 與 glossary 文件，以及快取。

## 保持不變的契約

1. 行為、磁碟上的檔名與結構都不變；既有的劇本庫與版本照舊讀取，不需遷移。
2. 錯誤情況維持原本的例外與訊息（劇本不存在、文字與 manifest 不一致、版本過期、衝突或被修改）。
3. 受信任發佈（`trusted_scenario_source`）與遊戲權限都沒有動。
4. 僅有的改名是 `_PAGE_RE` 改為公開的 `PAGE_MARKER_RE`（兩個來源整備模組使用），以及移除 `scenario_templates._root`／`_variant_dir`／`_all_variants`；相關測試改用 library 介面。

## 執行方式

`tests/test_scenario_source_store.py` 測試介面（一致性檢查、原子發佈且不留殘檔、損毀與路徑穿越的位址、stamp、移除）。`tests/test_architecture_scenario_store.py` 在 `scenario_library` 以外的模組讀取它的私有名稱，或 `scenario_templates`、`scenario_source_review`、`help_actions`、`scenario_activation` 寫出 `manifest.json`、`records.json` 等 library 檔名時失敗。

## 未改動

`trusted_scenario_source`、`scenario_source_authoring` 與 library 自己的儲存／載入函式仍知道目錄結構；它們是發佈路徑或 library 本身。讓它們改走更窄的介面是另一個變更。
