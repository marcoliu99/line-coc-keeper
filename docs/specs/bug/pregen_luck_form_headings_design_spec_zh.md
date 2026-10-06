# 印在欄位標題下方的幸運值要保留，不是丟掉

[English](pregen_luck_form_headings_design_spec.md)

狀態：**已實作**。基準：`main_v2` 的 `6f7a6d9`。

## 問題

載入《The Haunting Scenario trimmed》時，四位預製調查員都顯示「LUCK 空白，選用後由玩家擲骰」，但印出來的角色卡明明寫著 `Luck 50`。Bot 的日誌對每一位都記下了原因：

```text
dropping unverified PDF pregen Luck for 未填: excerpt_does_not_state_the_value
```

Chaosium 的「1920s Era Investigator」表單在 `Luck` 標籤與放數值的框之間印了小字的欄位標題 `Starting` 與 `Current`，而且角色卡頁面是圖片，文字來自 OCR 的閱讀順序。因此逐字引用幸運欄會是 `Luck Starting Current 50` 或 `Luck Starting 50 Current`。驗證（`pregen_extractor._check_pdf_luck`）只接受標籤後面直接接數字的引用，這些引用被拒絕，正確的數值就被丟掉了。01:22 載入 Dead Boarder 時也是同樣的原因丟掉了幸運值。

防護本身是對的：模型回報的幸運值必須引用自角色卡並且屬於那位調查員，否則由玩家擲骰（`pregen_luck_roll_design_spec.md`）。只是引用的比對規則太窄。

## 變更

- `_LUCK_ON_SHEET` 容許標籤與數值之間出現表單的欄位標題（`starting`、`start`、`current`、`initial`、`maximum`、`max`，以及中文的 `起始`、`目前`、`當前`、`初始`），數量不限。後面的數字仍必須等於回報的數值，引用仍必須出現在所引用的頁面，也仍必須依角色卡自己的屬性、最近的姓名，或只有一位調查員來歸屬於這位調查員。
- 標籤、標題與數值之間也可以用 `|`、`:`、`=` 或破折號分隔，因為被讀成表格的頁面會把這個框引用成 `Luck | Starting: 50`、`Luck — Starting | 60` 或 `Luck | 55`。這是第二次載入 The Haunting 時 #213 仍然丟掉的四個引用。
- 只有標題、沒有數字的引用仍然會被丟掉。
- 丟掉幸運值時的警告現在會附上模型回報的數值、引用的頁碼與引用文字的前 160 個字元，下次不吻合時可以直接從日誌看出，不用猜。

## 測試

`tests/test_pregen_luck_verification.py`：帶兩個標題的引用、一個標題且數值在第二個標題之前、中文標題、有標題但沒有數字的引用仍被丟掉，以及警告內容顯示回報的數值與引用。既有的歸屬案例不變。
