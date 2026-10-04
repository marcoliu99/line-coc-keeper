# 移除 `legacy_commands`

[English](legacy_commands_retirement_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與目標

分類：`refactor`。狀態：**已實作**（[四階段架構重構](architecture_refactor_phases_1_4_design_spec_zh.md)的第 4 階段）。基準為 `main_v2` 的 `2affd06`（2026-10-04），疊在[狀態交易](state_transaction_design_spec_zh.md)、[檢定引擎](check_engine_design_spec_zh.md)與[戰鬥引擎](combat_engine_design_spec_zh.md)之上。

`app/legacy_commands.py` 原本有 2,590 行，同時放著玩家檢定與 Luck、劇本上傳流程、地圖處理、調查員規則與幾個純訊息指令，被 router、六個 handler、Discord 轉接與 18 個測試檔匯入。第 1–3 階段已把它的狀態寫入、檢定與戰鬥處理搬到各自的擁有者；剩下的 1,184 行在本階段搬走，檔案**直接刪除，而不是改名**。

## 各項責任搬去哪裡

| 責任 | 現在的擁有者 |
| --- | --- |
| PDF 上傳流程、「新劇本 vs. 更正」的套用、劇本比較、角色卡上傳、預製角色合併 | `app/services/scenario_ingestion.py` |
| GM 對 PDF 的選擇（權限檢查、鎖、回覆） | `app/commands/handlers/uploads.py`（`resolve_pdf_upload_choice`） |
| 地圖上傳、把玩家的話解析成地圖移動、RAG 房間查找 | `app/services/map_service.py` |
| 認領預製角色、暫離／回歸、治療、就緒名冊、「已經有角色」的防護 | `app/services/character_service.py` |
| 認領預製角色後的 `/coc luck roll` | `app/commands/handlers/character.py`（`handle_pregen_luck_roll`） |
| `/roll`、對不支援訊息類型的回覆 | `app/commands/handlers/messages.py` |
| 玩家檢定與 Luck；回呼型別別名；送出後處理 | `app/checks`、`app/commands/types.py`、`app/services/post_turn.py`（第 2 階段） |
| 戰鬥動作 | `app/services/combat_engine.py`（第 3 階段） |

Handler 負責解析輸入、檢查權限與格式化回覆；規則在 service 裡。函式是**搬移，不是重寫**：被搬走的 29 個定義，其中 27 個在忽略「變成公開名稱時拿掉的開頭底線」之後，與原本完全相同（比對語法樹）；另外兩個是 `build_readiness_roster`（docstring 裡的一個詞）與 `resolve_pdf_upload_choice`（函式內的 `from app.commands import permissions` 原本只因為 package 會匯入這個模組才需要延遲匯入，現在是一般匯入）。

## 保留的契約

1. 指令名稱、別名、說明文字與錯誤用語不變（router 的 diff 只有匯入與改名後的呼叫）。
2. Discord custom id 與按鈕 payload 不變；按鈕 handler 沒有被動到，過期 timeline 的點擊仍被拒絕。
3. 儲存結構不變，所以既有存檔、待處理檢定與戰鬥都能載入並續玩。
4. 上傳流程維持原本的呼叫順序、參數、訊息與警告。`tests/test_ingestion_trace.py` 重播首次上傳、相似劇本重傳、角色卡上傳與地圖上傳，並與最後一個仍有此模組的 commit 上錄下的 trace 比對。
5. 狀態寫入仍走第 1 階段的交易邊界；搬移函式沒有改變它的提交方式。
6. `app.commands` 保留公開名稱（`handle_pdf_upload`、`handle_check_command`…），作為指向各擁有者模組的單純門面，不再延遲解析任何名稱。

## 強制檢查

`tests/test_architecture_legacy.py` 會在下列情況失敗：模組檔案回來或出現名稱相近的 shim；`app/`、`scripts/`、`tests/` 內任何地方匯入它（import 敘述、`from app import legacy_commands`、`importlib.import_module`、`__import__`）；任何 package 的 `__init__` 定義模組層級 `__getattr__`；新的 service 在執行期依賴指令層（只允許型別名稱）；或 `import app.legacy_commands` 不再失敗。每個入口都在全新 process 中匯入一次。

## 未變動的部分

OCR、版面、數值驗證、fallback 順序、劇本匯入門檻、地圖抽取與 provider 選擇都沒有碰；typed extraction result 與 provider 抽象留待後續階段。劇本上傳與地圖程式仍用嚴格的快照提交（`commit_snapshot`），而不是 delta，因為搬移函式不能改變它做的事。

報告：[第 4 階段](../../refactor/phase4-result.md)。
