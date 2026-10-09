# 劇本正典邊界與持久更正

[English](narrative_boundaries_design_spec.md) | [文件索引](../../README_zh.md)

## 狀態與範圍

分類：`enhancement`。狀態：**已實作**。對照基準：`main_v2` 的 `0ae449a`（2026-10-09）。

此版本描述現行契約，提案工作均明確標示；歷史來源文字連結列於下方。

## 現行契約

1. 劇本資料與明確成立的正典事件決定世界事實；玩家假設或先前無依據 AI 敘事不能創造房間、敵人、線索或重要物品。

2. 允許合理日常隨身小物；具劇情或機制影響的新內容須有依據，檢索未中代表未確認，不代表不存在或可編造。

3. 專用 /coc correct 是遊戲外提報；目標須對上本頻道／時間線 Keeper 回條，玩家說法不自動變正典或機制暫停。

4. 原始提報文字不得放系統 prompt；受限 user-message 投影區分待核對說法、已核准更正與 KP 定義 hold_scope。

5. 每人最多 3 筆、每群最多 12 筆待核對；JSON 投影最多 6000 字元，修剪／封存已結案，不能為預算悄悄丟棄有效核准決定。

6. approve／reject／hold／supersede 是需授權裁定操作，提報者可撤回自己的提報；範圍 hold 阻擋相關行動，無關行動仍可繼續。

7. 權威決定超出投影容量時要求明確整併，不能默默忘記正典；回溯／劇本切換隔離更正時間線。

8. 不新增每回合固定 LLM 審稿；確定性狀態／回條驗證本身不能保證提示詞遵循或語意完整。

9. 給玩家看的敘事一律用故事裡的聲音。每一份敘事提示（`prompt_config.PLAYER_VOICE_RULES`，放在 `NARRATOR_INSTRUCTION` 與兩種可用工具的敘事指示中）都禁止紀錄、收據、權威、狀態、驗證、核實這類系統用語，也禁止解釋為什麼某件事還不能寫：還沒發生的事就不寫，用懸念把行動交回玩家（「Corbitt 的爪子已逼到你面前——你要閃避，還是反擊？」）。待處理檢定的事實區塊改成要求用故事口吻請玩家擲下一個檢定，不再寫「檢定已建立」。2026-10-09 的《鬼屋》實測曾對玩家說「現有紀錄沒有驗證他已起身」、「沒有可核實的戰鬥收據」，是守密人規則本身的用語漏進故事。引擎在敘事後附加的擲骰提示，以及觸發檢定的公開訊息，改成直接點名要擲什麼，不再說「已建立」（「請按檢定按鈕或輸入 /coc check，擲 Evelyn 的偵查。」）。其他固定退回訊息不是敘事，不在此範圍。

## 流程與介面

### 普通採買例外（2026-09-27）

歷史設計：玩家先前同意普通合法商品可依已確立的商業環境裁定購買，不要求具名店家或劇本逐件列商品。`purchase_items` 結算及具體阻擋交接後來隨 PR #91 回退。目前遊戲使用既有守密人負擔能力與持物指引；本節不再描述啟用中的交易流程。參見[回退規格](../bug/revert_pr91_purchase_turn_design_spec_zh.md)。

```text
玩家行動／假設 -> 劇本依據 -> 機制 -> 敘事
/coc correct -> 驗證訊息憑據 -> 待核對異議 -> KP 裁決 -> 持久化投影
```

## 實作與驗證

連結的實作與既有回歸測試為稽核依據；實作細節以來源中的測試案例與精確 payload schema 核對。歷史測試數及 API 試驗不代表目前效能保證。

- [app/keeper.py](../../../app/keeper.py)
- [app/services/narrative_corrections.py](../../../app/services/narrative_corrections.py)
- [app/commands/handlers/correct.py](../../../app/commands/handlers/correct.py)
- [app/agents/tool_gateway.py](../../../app/agents/tool_gateway.py)
- [tests/test_narrative_boundary_prompts.py](../../../tests/test_narrative_boundary_prompts.py)
- [tests/test_narrative_correction_lifecycle.py](../../../tests/test_narrative_correction_lifecycle.py)
- [tests/test_narrative_correction_command.py](../../../tests/test_narrative_correction_command.py)

## 歷史依據

不可變原版本保留事故敘述、遷移歷程、草稿範例與歷史量測；其中可能描述已取代行為，現況以本文件上方契約為準。

[Original source / 原始版本](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/narrative_boundaries_design_spec.md)
