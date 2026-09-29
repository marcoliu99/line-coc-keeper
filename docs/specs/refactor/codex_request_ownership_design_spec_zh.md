# Codex 請求擁有權

[English](codex_request_ownership_design_spec.md)

狀態：已實作。基準：`main_v2` 的 `07d55a7`（包含 PR #140）。

## 問題

對話使用事件迴圈專屬 semaphore，文字分析另用執行緒 semaphore。兩條路徑同時執行時，CLI 請求總數可能超過 `CODEX_MAX_CONCURRENCY`，排隊逾時與取消也分散處理。transport 把遊戲工具指示放進文件分析請求。

## 介面

`app/providers/codex_request_owner.py` 統一管理跨執行緒、跨事件迴圈的 FIFO 准入、期限、執行中任務與關閉。兩個入口在建立或使用 transport 前取得租約；完成、失敗或取消時釋放，排隊取消不得釋放別人的名額。對話回合期限與 `CODEX_TIMEOUT` 同時限制排隊與請求；文字分析在工作執行緒內執行非同步請求並使用同一管理者。關閉時停止新請求准入，取消跨迴圈的排隊與執行中請求，並等待每個已准入請求關閉 transport、釋放租約後的跨執行緒完成確認；此後 provider 才安裝新 owner。

transport 由呼叫端提供任務指示：對話使用遊戲協定，結構化文字分析使用分析指示。exec 和 app-server 繼續作為 transport。PDF、OCR、地圖、預製角色卡仍由 `ANALYSIS_PROVIDER` 處理。

## 測試

用受控 transport 驗證對話與分析共用一個名額、排隊取消、跨迴圈關閉、排隊時間計入期限，以及分析提示隔離。保留 PR #140 的 reasoning-effort 日誌。執行完整 CI 檢查。
