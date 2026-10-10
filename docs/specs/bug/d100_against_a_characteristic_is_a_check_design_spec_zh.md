# 比對調查員數值的 1D100 是檢定；填錯的對抗欄位會說是哪一格

[English](d100_against_a_characteristic_is_a_check_design_spec.md)

狀態：**已實作**。基底：`main_v2` 的 `a62d605`。

## 問題

2026-10-10 在 `a62d605` 上跑的 200 回合四人《鬼屋》run（OpenAI `gpt-6-luna`）有 46 回合以 fallback 收尾；前一天 Codex 跑 `fac75820` 只有 2 回合。大多數來自兩種型態，第一種 Codex 那場也出現過（第 67 回合）。

1. **劇本把反應綁在特徵上，守密人卻公開擲 1D100。** 鄰里那段寫 "roll 1D100 and compare it with the investigator's APP or Credit Rating"。守密人用 `roll_dice` 公開擲 `1d100`，得到一個沒有任何檢定能結算的數字，回合以 `missing_resolved_effect` 降級（約 16 回合）。玩家看到「骰子已結算：1d100，總值 13。」接著「工具的結果沒有通過核對」。那其實是特徵檢定：用 `skill_check` 擲外貌或信用評級，系統判定等級、玩家自己按鈕。
2. **一般檢定硬填 `opposed`。** 心理學、圖書館使用這種檢定，守密人填了 `skill_check` 的 `opposed` 物件，`opposed_checks.contract` 以「對抗檢定缺少有效的能力、來源或勝敗後果。」拒絕；守密人不知道是哪個欄位、為什麼，再試兩三次，中間還取消自己剛建的檢定（「X 尚未擲骰的檢定已取消。」），最後放棄（`tool_failure`，約 13 回合）。偶爾過關時，玩家看到「結果為「一般成功；對抗勝方=player」」：公開的定案文字裡夾了英文標籤。

## 修改

- `roll_dice` 拒收公開的純百分骰（`1d100`、`d100`），回「1D100 不用這個工具擲：要和調查員的技能或特徵……比對，就用 skill_check 擲那個技能或特徵……守密人自己要暗擲 1D100 才用 roll_dice，並設 secret: true。」暗骰的 1D100、其他表示式（`1d6`、`2d100`、`1d100+5`）和傷害骰都不變。工具說明寫同一件事。
- `opposed_checks.contract` 點名欄位和原因：「對抗檢定的 on_loss 是空的」「對抗檢定的 source 太長（612 字，上限 400 字）」「缺少 on_win、on_loss」「不接受 opponent_roll（對手的骰果由程式擲）」，每句後面接「只有劇本明寫對手與其數值的對抗檢定才填 opposed；一般技能檢定……整個 opposed 不要填。」`opposed` 的 schema 說明寫同一件事。
- 存下來的結果標籤用玩家看得懂的話寫對抗結果（`opposed_checks.public_text`）：「一般成功；對抗結果：你勝出」，不再是「對抗勝方=player」。

## 不改的部分

- 哪些檢定是對抗檢定：仍由守密人依劇本決定；引擎只告訴它送來的物件哪裡不對。
- 每個對抗欄位 400 字的上限。

## 驗證

`tests/test_d100_is_a_check.py`：公開 1D100 的四種寫法都被導向 `skill_check`，暗骰 1D100 和其他表示式照擲；每種 contract 錯誤都點名欄位；標籤是中文。`tests/test_scenario_action_check_handoff.py` 改為斷言新標籤。
