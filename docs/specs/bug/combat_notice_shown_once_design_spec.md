# The same combat notice is shown once

## Problem

Each combat tool a turn calls leaves one public line, "戰鬥機制操作已記錄；後續以目前戰鬥狀態為準。" In a Haunting run the player saw that sentence three times in a row (turns 29 and 45) and four times on turn 51. When some of the calls carried the "【戰鬥暫定；尚未結算】" mark and some did not, the "same" line also differed, so even an exact-match de-duplication would have kept both.

## Change

`turn_delivery.distinct_lines` says each line once, treating a line with and without the provisional mark as one line and keeping the marked version. It is used where a turn's outcome lines are put in front of the player: the fallback notice (`prompt_config.enforce_mechanic_check_consistency`) and the delivery envelope's projected text.

## Not done

The wording of the notice and the rest of what a turn shows are unchanged.

## Tests

`tests/test_turn_fallback.py`: three outcomes with the same notice, one of them marked provisional, give the notice once with the mark.
