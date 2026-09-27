# 可執行的 Help 控制項

[English](actionable_help_buttons_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `afe8ace`（2026-09-27）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 每個已登記 Help 項目都對應可執行動作，不只是顯示命令字串；覆蓋率應由 registry 計算，歷史 52/52 不再是現行契約。

2. 角色名稱、職業與自由文字用表單；劇本、預製角色、章節、模板檔案／版本等伺服器已知選項使用清單。

3. 設定為需確認的重要動作必須經確認；選項值使用伺服器保存索引，不能直接接受客戶端任意命令片段。

4. 分派時重新檢查擁有者／頻道、權限、狀態版本與可用選項，再呼叫既有 router；過期或消失資源不能執行。

5. 取消 PDF 重解析時保留暫存資料供重試；購買、更正與模板的新動作都納入覆蓋。

6. 模板匯入選擇 IMPORT_DIR 中既有檔案；此功能不包含 Discord 模板附件上傳。

## 流程與介面

```text
Help 分類 -> 操作 -> 輸入／選擇 -> 必要時確認 -> 最新狀態檢查 -> 指令路由
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/help_actions.py](../../../app/help_actions.py)
- [app/help_registration.py](../../../app/help_registration.py)
- [app/help_service.py](../../../app/help_service.py)
- [app/help_docs.py](../../../app/help_docs.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [tests/test_help_actions.py](../../../tests/test_help_actions.py)
- [tests/test_help_reparse_recovery.py](../../../tests/test_help_reparse_recovery.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/actionable_help_buttons_design_spec.md)
