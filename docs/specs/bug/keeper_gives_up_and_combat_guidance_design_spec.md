# Four replies from the 2026-10-10 soak runs: giving up with evidence, waiting on nobody, a combat prompt to the wrong player, a lost fight opening

[繁體中文](keeper_gives_up_and_combat_guidance_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `0c53dcb`.

## Problem

Four 100-turn Codex runs on `0c53dcb` (Scritch Scratch, Camp Sunny, The Lightless Beacon, The Haunting; 2026-10-10) finished every turn. Four kinds of reply read wrong to the player:

| Run, turns | What the player read | Cause |
|---|---|---|
| Camp Sunny 50 | 「劇本裡沒有足夠的內容可以據以裁決這個行動」 | The Keeper searched the scenario four times and found the passage (「should one of the investigators become a pesky nuisance, he or she will be added to Billy's menu … they look to capture the investigators」), then returned `incomplete`. No tool had refused, so the retry added for a refusal (`rerun8_player_replies_design_spec`) did not apply, and the provider's own retry applies only before any tool ran. The fight the run was built to test never started. |
| Haunting rerun1 17 | 「還有尚未完成的檢定、Luck 決定或他人的行動；請先完成它」 | The Keeper answered `deferred` with no tool call while nobody had a pending check or Luck decision. The validator rejected the deferral (`deferral_not_verified`), and that code is classed as a pending state, so the player was told to finish a check that did not exist. |
| Lightless 31–36 | 「戰鬥進行中，現在輪到「Julian Price」行動 … 還沒輪到你時，請稍候」, six turns running | The line named an enemy that was never registered (「深潛者混種」; the fight held four Younglings), so the Keeper blocked. The combat guidance names whose turn it is but not who is asking, and the asker was Julian. |
| Haunting rerun1 28 | 「George Finch 的檢定已結算 … 這次行動尚未完整處理」 | The line opened the fight: `initialize_combat` succeeded and a Sanity check was rolled, but the Sanity check's Luck decision left the turn `incomplete`, and an incomplete turn's narration is replaced by the warning. Corbitt rising was never told. The deferral path keeps the narration since `rerun8_player_replies_design_spec`; this path did not. |

## Change

- **A Keeper that found text gets one more ask.** In the Executor's `final_feedback`, a `model_incomplete` final qualifies for the one existing retry when every call this turn was a successful look-up (`INFORMATION_QUERY_TOOLS`) at least one `search_scenario` returned text, and no search marked its text `complete_for_action: false` (nor did the gateway block the turn for incomplete evidence). The note says the search has results and nothing changed, and asks the Keeper to act on them with a tool, or to answer `no_mechanics` citing the passage, keeping `incomplete` only when the text really does not bear on the action. Nothing has changed, so the retry cannot apply anything twice.
- **A wait on nobody is a turn with no action.** `turn_fallback.classify` maps `deferral_not_verified` to `executor_no_action` when the turn ran no tool and nobody has a pending check or Luck decision. That reason is recoverable, so the supervisor retries the Executor once; if that also fails, the player reads the no-action text rather than a claim about a check that does not exist. With a pending item, or after a tool call, the pending reason stays.
- **The combat guidance knows who is asking.** `combat_guidance` takes the acting character; when the current actor is the asker it answers 「戰鬥進行中，現在輪到你（Julian Price）行動。目前的敵人：「Youngling（1）」…。要攻擊的話，請指名上面列出的敵人，並說明用什麼方式；也可以改做其他行動（閃避、逃跑、躲藏、掩護同伴等），說清楚就好。」, listing the enemies still standing and saying that another action (dodging, fleeing, hiding, covering a companion) is a turn too. Anyone else is still told to wait. `enforce_mechanic_check_consistency` passes `turn_resolution.actor_character_id`.
- **A fight opening survives an incomplete turn.** When a call this turn took the battle from inactive to active (`initialize_combat`, `start_combat` or `add_npc_to_combat`, judged by the recorded transition, not the tool name) and the narration is not empty, the reply is the narration, then the confirmed lines and the warning, then whatever is owed (the Luck decision, the pending check). The same rule the deferral path already has. The repair runs before and after the Guard and again after the obligation gate: a reply that already carries the owed lines keeps what stands before them (the narration) and after them (an obligation's summary); one the Guard rewrote keeps its narration and gets the owed lines rebuilt, so a dropped Luck line comes back and nothing is doubled.

## Not changing

- An incomplete turn that rolled or wrote something (Camp Sunny turn 23: a Sanity check, then `incomplete`) gets no extra ask: a retry could apply the roll's consequences twice. The reply already shows the settled roll.
- The Lightless line named an enemy that did not exist; that is the test harness's error. The change makes the refusal useful, not the target valid.
- The Luck prompt that ends many autoroll turns: a player's decision under the rules, and a real table presses the button.

## Verification

`tests/test_keeper_gives_up_and_combat_guidance.py`:
- Successful searches with text qualify for the retry; no events, empty results, a mutation among the calls, or a refusal do not.
- `deferral_not_verified` with no tool and nothing pending classifies as `executor_no_action`; with a pending check elsewhere, or after a tool call, as `unresolved_pending_state` (the parametrised table in `tests/test_turn_fallback.py` is updated for the first case).
- The current actor is told it is their turn and which enemies stand (a defeated one is not listed); another player is told to wait; the reply passes the acting character.
- A fight counts as opened only on an inactive-to-active transition: `add_npc_to_combat` from inactive does, `initialize_combat` while already active does not. An incomplete turn that opened the fight starts with its narration and still carries the Luck instruction; one without keeps the warning only. Run twice, the repair adds nothing; run on a rewrite that kept only the warning, it restores the Luck instruction; a summary appended after the owed lines survives the next pass.
- A search whose text is marked `complete_for_action: false` does not qualify for the retry.
