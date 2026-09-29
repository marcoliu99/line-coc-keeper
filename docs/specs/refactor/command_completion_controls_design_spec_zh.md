# 指令完成與玩家控制項

[English](command_completion_controls_design_spec.md)

狀態：**implemented**。基準：`main_v2` 的 `07d55a7`。

## 現況核對

三階段 router 重構已接手按鈕／上傳分派、權限，以及鎖內 pending 按鈕認領。原候選的這些部分已完成。剩下的是控制項發布邊界：`discord_bot` 仍有第二套檢定／Luck 認領與發送流程、認領復原邏輯，以及兩個呼叫端各自的認領／發送／後備編排。已認領的發送器會在等待後重查 Luck，卻沒有在發送已被取代的檢定前同樣重查。

## 介面

深化 `app/services/pending_buttons.py`，讓它負責控制項完成。一個完成物件記住執行前快照，在 router 的鎖內認領，再透過 transport callback 在解鎖後發布。每個已認領的檢定與 Luck 決定都要對照最新持久化身分；取代後的舊選項不發送。發送失敗或取消時，只釋放自己仍相符的認領。鎖內認領失敗則透過同一服務復原。Discord 保留呈現、傳送、互動身分與權限判斷。

Router 保留現有鎖與授權政策。一般回合不新增 provider 或 Discord API 呼叫。服務的持久化 state 讀取走工作執行緒，實際發送仍在 conversation lock 外。

## 驗證

從同一介面測文字、按鈕、Help 與復原路徑，涵蓋 mutation 後傳送失敗、延遲傳送時被取代、取消、過期身分及重疊認領。保留既有延遲與 logging 測試，再執行完整 pytest、Ruff 0.16.8、mypy、compileall。
