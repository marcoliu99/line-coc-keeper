# 備團與持久化參考

[English](prep_persistence.md)

## 儲存對照

劇本原稿、章節 manifest、索引、圖片與預製角色在可重用劇本庫，選定 context 複製到 `GroupState.scenario_text`。SQLite 經 `app/db.py` 與 repositories 保存群組狀態、角色鏡像、劇本索引、記憶與封存，圖片仍是檔案；舊 `app/state.py`／每群 JSON 描述屬歷史。

## 場景開始前

RAG 關閉時將目前劇本快照依輸入上限放 prompt；開啟時檢索相關依據並補真實缺漏。已核准外部中文記錄加快搜尋，仍有原稿後備；檢索不解鎖章節，也不授權編造場景。

## 戰役記憶

近期 log、戰役摘要、場景摘要與記憶 RAG 用途不同；context 歷史預算不刪權威狀態。Checkpoint 允許隔離時間線的回溯，備份保護持久資料；手動角色卡資產獨立於活躍候選池，跨局保存。

## 維運邊界

不要把 runtime 資料或憑證當備團文件 commit；外部劇本工作簿是綁定來源的匯入產物，不是存檔。人工筆記可補充備團，但改 Markdown 不會修改活躍 SQLite 狀態。
