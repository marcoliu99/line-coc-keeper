# A 1D100 against an investigator's number is a check, and an opposed field filled wrong says which

[繁體中文](d100_against_a_characteristic_is_a_check_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `a62d605`.

## Problem

In the 200-turn four-investigator Haunting run of 2026-10-10 on `a62d605` (OpenAI `gpt-6-luna`), 46 of 200 turns ended in a fallback. The Codex run of the day before on `fac75820` had 2. Two patterns account for most of them, and the Codex run shows the first one too (turn 67).

1. **A public 1D100 for a reaction the scenario pegs to a characteristic.** The Neighborhood says "roll 1D100 and compare it with the investigator's APP or Credit Rating". The Keeper called `roll_dice` with `1d100` in public, got a number no check could settle, and the turn fell back with `missing_resolved_effect` (about 16 turns). The player read 「骰子已結算：1d100，總值 13。」 and then 「工具的結果沒有通過核對」. That roll is a characteristic check: `skill_check` on 外貌 or 信用評級 rolls it, grades the tier, and gives the player the button.
2. **`opposed` filled on a plain check.** For Psychology or Library Use the Keeper filled `skill_check`'s `opposed` object, `opposed_checks.contract` refused it with 「對抗檢定缺少有效的能力、來源或勝敗後果。」, and the Keeper, told neither which field nor why, tried two or three more times, cancelled its own pending check in between (「X 尚未擲骰的檢定已取消。」) and gave up (`tool_failure`, about 13 turns). When one got through, the player read 「結果為「一般成功；對抗勝方=player」」: an English label in the public finalize line.

## Change

- `roll_dice` refuses a public bare percentile (`1d100`, `d100`) with 「1D100 不用這個工具擲：要和調查員的技能或特徵……比對，就用 skill_check 擲那個技能或特徵……守密人自己要暗擲 1D100 才用 roll_dice，並設 secret: true。」 A secret 1D100, any other expression (`1d6`, `2d100`, `1d100+5`) and damage rolls are unchanged, and so is the KP Assistant's roll (`speaker_role` of the turn, never a field in the model's input), which rolls for a human Keeper who asked for that number, a random table or a private pick included. The tool description says the same.
- `opposed_checks.contract` names the field and the reason: 「對抗檢定的 on_loss 是空的」, 「對抗檢定的 source 太長（612 字，上限 400 字）」, 「缺少 on_win、on_loss」, 「不接受 opponent_roll（對手的骰果由程式擲）」, each followed by 「只有劇本明寫對手與其數值的對抗檢定才填 opposed；一般技能檢定……整個 opposed 不要填。」 The `opposed` schema description says the same.
- The stored outcome label says the opposed result in the table's words (`opposed_checks.public_text`): 「一般成功；對抗結果：你勝出」 instead of 「對抗勝方=player」.

## Not changing

- Which checks are opposed: the Keeper still decides from the scenario; the engine only tells it what is wrong with the object it sent.
- The 400-character limit per opposed field.

## Verification

`tests/test_d100_is_a_check.py`: the four spellings of a public 1D100 are refused toward `skill_check`, a secret 1D100 and other expressions roll; each contract error names its field; the label is Chinese. `tests/test_scenario_action_check_handoff.py` asserts the new label.
