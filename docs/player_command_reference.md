# COC7e 玩家指令參考

這份文件列出 Discord 版 Bot 的完整手動輸入指令。`顯示條件` 只表示 Help 按鈕何時出現；即使暫時隱藏，仍可在符合條件後手動輸入。

## 角色

### 分配技能點數

在互動式建角流程中分配技能點數。

用法：
- `/coc alloc occ|int 技能名 點數`

### 列出我的角色

查看自己擁有的角色與目前正在使用的角色。

用法：
- `/coc characters`

### 互動式建立調查員

先擲屬性，再分配職業與興趣技能點數。

用法：
- `/coc create 角色名 [職業]`
- `/coc alloc occ|int 技能名 點數`
- `/coc create status|done|cancel`

範例：
- `/coc create 小明 記者`

顯示條件：只有劇本沒有預設角色時顯示

### 快速建立調查員

建立一位自訂調查員。

用法：
- `/coc pc 角色名 [職業]`

範例：
- `/coc pc 小明 記者`

顯示條件：只有劇本沒有預設角色時顯示

### 查看預設角色詳情

查看某位預製調查員的完整能力。

用法：
- `/coc pregen 編號`

範例：
- `/coc pregen 1`

顯示條件：只有劇本有預設角色時顯示

### 查看預設角色

查看劇本附帶的預製調查員。

用法：
- `/coc pregens`

範例：
- `/coc pregens`

顯示條件：只有已載入劇本時顯示

### 退出目前角色

解除目前角色的 active binding，但保留角色歷史資料。

用法：
- `/coc retire [角色名]`

範例：
- `/coc retire 小明`

### 設定關鍵背景連結

設定角色最重要的人、地或物。

用法：
- `/coc setconnection 角色名 敘述`

### 修改技能

手動修正自己角色的技能值。

用法：
- `/coc setskill 角色名 技能名 數值`

### 查看角色卡

查看自己的調查員角色卡。

用法：
- `/coc sheet`

範例：
- `/coc sheet`

### 切換目前角色

在自己擁有的多個角色之間切換。

用法：
- `/coc switch 角色名`

範例：
- `/coc switch 小明`

### 使用預設角色

選擇一位劇本附帶的預製調查員。

用法：
- `/coc usepregen 編號 [自訂名稱]`

範例：
- `/coc usepregen 1`

顯示條件：只有劇本有預設角色時顯示

## 檢定

### 切換自動擲骰

預設關閉；所有玩家都可以開啟或關閉本群組設定。

用法：
- `/coc autoroll on|off`
- `/coc autoroll`

範例：
- `/coc autoroll off`

### 技能或理智檢定

預設由玩家用 /coc check 或按鈕擲骰；若群組開啟 autoroll，才由 Keeper/system 自動處理。

用法：
- `/coc check [技能或選項名稱]`

範例：
- `/coc check 閃避`

### Luck 擲骰與結果選擇

選角後由玩家擲 LUCK；一般檢定只要有付得起的升級選項，就可以選擇是否花費 Luck 買到更好的結果。

用法：
- `/coc luck roll`
- `/coc luck skip|regular|hard|extreme`

範例：
- `/coc luck roll`

## 戰鬥

### 加入友方 NPC

將站在我方的 NPC 隊友加入戰鬥。

用法：
- `/coc combat addally 名稱 DEX HP`

### 加入敵人

將 NPC／敵人加入目前戰鬥。

用法：
- `/coc combat addnpc 名稱 DEX HP`

### 調整戰鬥 HP

對戰鬥中的角色或 NPC 套用 HP 增減。

用法：
- `/coc combat damage 名稱 增減量`

範例：
- `/coc combat damage 深潛者 -4`

### 結束戰鬥

結束目前的戰鬥並清除戰鬥狀態。

用法：
- `/coc combat end`

### 推進回合

推進到下一位的回合。

用法：
- `/coc combat next`

### 開始戰鬥

依 DEX 建立戰鬥先攻順位。

用法：
- `/coc combat start`

### 查看戰鬥狀態

查看回合與先攻順位。

用法：
- `/coc combat status`

## 地圖

### 進入地圖

手動進入某一頁的平面圖。

用法：
- `/coc enter 頁碼`

### 離開地圖追蹤

離開目前地圖，移動改回由守密人判斷。

用法：
- `/coc leavemap`

### 查看劇本頁面

查看劇本某一頁的圖片。

用法：
- `/coc showpage 頁碼`

範例：
- `/coc showpage 16`

### 查看所在位置

查看地圖引擎追蹤的房間與出口。

用法：
- `/coc where`

## 劇本

### 暫離遊戲

標記自己暫時離開；戰鬥中會跳過你的回合。

用法：
- `/coc away`

### 回到遊戲

取消暫離狀態並恢復正常參與。

用法：
- `/coc back`

### 取消劇本處理

放棄目前等待處理的相似劇本 PDF。

用法：
- `/coc scenario cancel`

### 清理劇本庫

刪除沒有被任何群組使用的劇本庫項目。

用法：
- `/coc scenario clean 劇本ID`

### 結束遊戲

結束目前這局遊戲。

用法：
- `/coc end`

### 設定年代

設定 1920 年代或現代背景。

用法：
- `/coc era 1920|modern`

### 匯入伺服器 PDF **[KP-only]**

從設定的 IMPORT_DIR 匯入大型 PDF；只有目前 KP Assistant 可以使用。

用法：
- `/coc scenario import 檔名.pdf`

### 重建劇本索引

手動重建 NPC／怪物與地點索引。

用法：
- `/coc index`

顯示條件：只有已載入劇本時顯示

### 列出劇本庫

查看可用劇本與目前使用中的劇本。

用法：
- `/coc scenario list`

範例：
- `/coc scenario list`

### 合併 PDF parts **[KP-only]**

依指定順序合併已暫存的 Discord PDF parts；只有目前 KP Assistant 可以使用。

用法：
- `/coc scenario merge 暫存ID1 暫存ID2 ...`
- `/coc scenario merge list`

### 開始新遊戲

重置群組狀態並開始新的一局。

用法：
- `/coc newgame`

### 處理劇本 PDF

決定上傳的 PDF 是新劇本或修正目前劇本。

用法：
- `/coc pdf new|fix`

### 重新解析劇本

重新處理等待中的相似劇本 PDF。

用法：
- `/coc scenario reparse`

### 設定守密人風格

自訂或重設守密人的敘事風格。

用法：
- `/coc setpersona 文字`
- `/coc setpersona reset`

### 開始劇情

角色準備好後，產生劇本開場白。

用法：
- `/coc start`

範例：
- `/coc start`

顯示條件：只有已載入劇本時顯示

### 查看遊戲狀態

查看目前劇本與角色狀態。

用法：
- `/coc status`

### 核准中文模板 **[KP-only]**

檢查來源與規則後核准模板供 RAG 使用。

用法：
- `/coc scenario template approve 劇本ID 模板版本`

### 匯入中文模板 **[KP-only]**

從 IMPORT_DIR 匯入校對後的 Markdown 模板。

用法：
- `/coc scenario template import 劇本ID 檔名.md`

### 預覽中文模板 **[KP-only]**

將模板的 KP 校對預覽傳送私訊。

用法：
- `/coc scenario template preview 劇本ID 模板版本`

### 查看中文模板 **[KP-only]**

查看背景處理、版本與待核對項目。

用法：
- `/coc scenario template status 劇本ID`

### 選用劇本 **[KP-only]**

從劇本庫選擇劇本與已校對的中文模板版本。

用法：
- `/coc scenario use 劇本ID [模板版本]`

範例：
- `/coc scenario use abc123 zh-TW-123456789abc`

注意：
- 省略版本時沿用此群組對該劇本的選擇。

## KP 助手

### 建立回溯節點 **[KP-only]**

保存目前完整遊戲狀態，供 KP 之後回溯。

用法：
- `/coc checkpoint [名稱]`
- `/coc checkpoint clean ID`

注意：
- 需要目前 KP Assistant 或 Discord Keeper role。

### 查看回溯節點 **[KP-only]**

列出目前群組可用的回溯節點。

用法：
- `/coc checkpoints`

注意：
- 需要目前 KP Assistant 或 Discord Keeper role。

### 查看場景摘要 **[KP-only]**

查看目前或指定的場景摘要。

用法：
- `/coc digest`
- `/coc digest 摘要ID`
- `/coc digest clean 摘要ID`

注意：
- 需要目前 KP Assistant 或 Discord Keeper role。

### 列出場景摘要 **[KP-only]**

列出目前群組的場景摘要歷史。

用法：
- `/coc digests`

注意：
- 需要目前 KP Assistant 或 Discord Keeper role。

### 登記 KP Assistant

登記或解除本局的 KP Assistant 身分。

用法：
- `/coc kp`
- `/coc kp quit`

### 回溯遊戲狀態 **[KP-only]**

將群組狀態恢復到指定回溯節點。

用法：
- `/coc rollback 節點ID或唯一名稱`

注意：
- 需要目前 KP Assistant 或 Discord Keeper role。

### 代玩家操作 **[KP-only]**

玩家突然離線時，由已脫離自己角色的 KP Assistant 代替指定玩家執行允許的 player command。

用法：
- `/coc sudo <@玩家> <command> [參數...]`
- `/coc sudo <@玩家> away`
- `/coc sudo <@玩家> retire [角色名]`

注意：
- KP-only：需要目前 KP Assistant 或 Discord Keeper role；不能代替玩家擲 LUCK、建立或認領角色。
- actor 必須先脫離自己的玩家角色／建角流程。

## 其他

### 單純擲骰

不經過守密人，直接擲骰。

用法：
- `/roll 1d100`
- `/roll 3d6+2`

範例：
- `/roll 1d100`
