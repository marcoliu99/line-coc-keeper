# A dead investigator's line is answered as dead

[繁體中文](dead_investigator_reply_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `112a22a`. Found in the 2026-10-12 Haunting rerun3 (Caleb Ward, turns 50 to 98).

## Problem

A blow of twelve points killed a ten-point investigator in a running fight; the table was told 「十二點傷勢足以奪去他的性命」. The combat engine kept `dead` beside `unconscious` in his injury record, with the tags 昏迷 and 倒地. After the fight, his player's next fifty lines were not answered as a dead character's. `unconscious_wake` leaves a dead investigator alone ("Dead is not this rule"), so the line went to the Executor, which read 「HP 0、昏迷、倒地」 and either blocked it (the fixed 「這次行動目前無法繼續」) or had the Narrator write 「Caleb 仍昏迷倒地」 over and over. Nobody was told he was dead, or what the player could do.

## Change

Outside a fight, a player's line from an investigator whose injury says `dead` is answered from state, before any model is called (`unconscious_wake.dead`, `dead_reply`, the first check in `supervisor._prepare`): 「{name} 已經死亡，不能再行動了。想繼續參與的話，請告訴守密人，由他安排新的調查員；其他調查員可以繼續。」 The same check keeps the line from starting a retrieval prefetch and from moving the investigator on the scene map. In a running fight the combat engine still decides. A dying investigator (`dying`) is not dead and still waits for First Aid.

## Not changed

The engine keeps `unconscious` and the 昏迷 tag on a dead investigator. That inconsistency is left alone: the reply no longer depends on it.

## Verification

`tests/test_unconscious_wake.py`: a dead investigator's line gets the dead reply without the Executor and without the word 昏迷; only an injury that says `dead` counts, and a dying one does not.
