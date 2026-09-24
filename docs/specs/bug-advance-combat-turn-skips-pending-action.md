# Spec: advance_combat_turn spammed instead of resolving a pending action

## STATUS: INVALIDATED — see "Correction" below

PR #62 (built from this spec) was closed without merging. The real root
cause of the incident this spec was built around turned out to be
different — see `docs/specs/bug-add-npc-to-combat-armor-schema-crash.md`
for the actual fix. The description change proposed here has been
reverted (never had verified justification once the real cause was
found). Kept in the repo as an honest record of how the misdiagnosis
happened and was caught, not as an active spec.

## Changeset Tracking
- **main_v2 start**: origin/main_v2:80f245fc6591a021b40dbbedb9d44d41e6b9ec9f
- **implementation end**: N/A — reverted, see Correction section

## Correction (found after PR #62 was opened)

A code-review comment on PR #62 pointed out that the verification's
synthetic `advance_combat_turn` result always returned the same
`current_turn` on every call — but the real `combat.advance_turn()`
(`app/combat.py:1148-1176`) unconditionally cycles to the next combatant
every single call; it has no concept of "this combatant's action is
unresolved, don't advance." That claim in the original verification
("matching real `combat.advance_turn`'s actual behavior") was written
without checking the source and was wrong.

Re-running the verification with the REAL state-transition logic (a real
`GroupState`, real `combat.start_combat`/`add_npc`, every tool call routed
through the real `keeper._execute_tool`) — neither the OLD nor the NEW
description reproduced the original 6x-spam pattern at all.

That prompted re-reading the actual production log's full context for
`turn_25ed74e`, not just the tool-call sequence. The real cause:

```
start_combat 成功：Ken、marco 加入戰鬥（沒有敵人）
add_npc_to_combat 失敗：ArmorRule.__init__() got an unexpected keyword argument 'name'
advance_combat_turn 成功 ×6（round 1→4，Ken/marco 正常輪替）
```

`add_npc_to_combat` **crashed** — the model passed an `armor` entry with
a `name` key, but `ArmorRule`'s real fields are `id`/`label`/`value`/
`applies_to`/`bypass_tags`/`public_hint`. The enemy was never added;
combat was left with only the two investigators and no NPC to act
against, and the model spent the rest of the turn cycling `advance_
combat_turn` (correctly, between the two real combatants — not a spam
bug in itself) without ever resolving anything, because there was
nothing to resolve. This is a downstream symptom of a real crash, not
evidence the model didn't know how to handle a blocked pending action.

Root cause of the crash: `add_npc_to_combat`'s `armor` parameter schema
(`app/keeper.py`) documents no field names at all, unlike its `attacks`
and `abilities` siblings which explicitly list their expected keys —
the model had no way to know `name` was wrong. (This exact mistake was
independently made while writing this investigation's own verification
scripts too, using `armor=[{"name": ...}]` before discovering the real
field names — same guess, same reason.)

The `advance_combat_turn` description change this spec originally
proposed has been reverted — it was never validated against anything
real. See `docs/specs/bug-add-npc-to-combat-armor-schema-crash.md` for
the actual fix.

## Purpose & Scope (historical — see Correction above)

Real production log evidence (`/Users/marcoliu/profile-async2.log`, real
`gpt-6-luna`/`none` Executor turn, `turn_25ed74eaf...`, 2026-09-24
16:01:49-16:02:23): a combat turn called `advance_combat_turn` **six
times in a row** with no other tool call in between — no `skill_check`,
no `plan_enemy_turn`, no damage tool, nothing. The turn burned 36.8
seconds and 8 LLM round-trips (`start_combat`, `add_npc_to_combat`, then
6× `advance_combat_turn`) before falling through to the Narrator, which
produced:

> 「Marco 緊跟在 Ken 身後，卻被狹窄的爬行空間卡住了腳步...戰鬥中的撤離尚
> 未判定成功；在 Ken 通過出口前，Marco 不能直接跳過當前行動順序離開。
> Ken，你要先完成逃脫行動的檢定。」

The scenario: a mid-combat evacuation (Ken needs to complete an escape
check through a crawlspace before his turn can be considered resolved).
The model needed to call `skill_check` for Ken's escape roll but never
did — instead it called the pure state-advance tool repeatedly, as if
trying to "skip past" the situation, before eventually giving up and just
narrating an instruction to the player instead of running the actual
mechanic.

Confirmed via `combat.advance_turn`'s real implementation
(`app/combat.py:1148-1176`): it already returns `current_turn` (whose
turn it now is) on every call, so the model isn't calling this
repeatedly because it lacks turn-order information — it's calling it
because it doesn't know what to do when advancing would require someone
to complete a pending action first, and its own tool description doesn't
say what to do in that case.

## Root cause hypothesis (needs real-API confirmation before implementing)

`advance_combat_turn`'s description (`app/keeper.py:579`) only says:

> 「把戰鬥推進到下一位戰鬥員的回合（已倒下的會自動跳過）。每次處理完一位
> 戰鬥員的行動後都必須呼叫這個工具，不可以自己心裡默默跳過。」

This tells the model to call it *after* resolving each combatant's
action, and not to silently skip anyone mentally — but it gives no
guidance for the case that actually happened here: a combatant's turn is
**blocked on an unresolved player decision/check** (an escape attempt,
a Dive for Cover, anything requiring a roll before the round can be
considered "handled"). Nothing tells the model "if the current
combatant's turn requires a check first, call `skill_check` (or the
relevant mechanic tool) before advancing — don't advance past someone
whose action hasn't actually been resolved."

Hypothesis: adding an explicit rule for this case — call the relevant
resolution tool (`skill_check`, `npc_skill_check`,
`offer_npc_attack_defense_choice`, etc.) for the *current* combatant
before calling `advance_combat_turn`, and don't call `advance_combat_turn`
again just because the previous call didn't produce the outcome you
expected — would prevent the spam pattern. Needs real-API verification
(same methodology as the other tiering/wording specs this branch's
history builds on) before landing, since this is a prompt-wording
change whose effect can only be confirmed empirically, not reasoned out.

## Changes (pending verification)

- Likely: extend `advance_combat_turn`'s description in `app/keeper.py`
  with a rule covering the "current combatant's turn is blocked on an
  unresolved action" case.
- Possibly: revisit whether `advance_combat_turn`'s return value itself
  should more strongly signal "this combatant hasn't acted yet" vs. just
  returning name/HP/side — TBD pending what the real-API trials show
  about whether wording alone is sufficient or whether the tool's own
  output needs to change too.
- No changes planned to `combat.advance_turn`'s actual state-transition
  logic — this is a guidance/description problem, not a bug in what the
  function itself does.

## Real-API verification

Reconstructed the real incident: combat active, Ken's turn, user message
"Ken 拉著 Marco 想從狹窄的爬行空間逃離火場，兩人都還沒逃出去" (matching
the real escape-through-crawlspace scenario), `get_combat_status`/
`skill_check`/`damage_combatant`/`advance_combat_turn` offered, real
multi-round tool-calling loop against `gpt-6-luna`/`none` (this
deployment's real Executor config). `advance_combat_turn`'s synthetic
tool result always returns `current_turn: "Ken"` (matching the real
`combat.advance_turn`'s actual behavior — it won't skip a combatant just
because it's called again; Ken's turn genuinely isn't resolved).

| Config | Tool sequence | `advance_combat_turn` spam count |
|---|---|---|
| OLD (current) | `get_combat_status → advance_combat_turn → advance_combat_turn → get_combat_status → advance_combat_turn → get_combat_status → advance_combat_turn → advance_combat_turn` | 5, hit max_rounds (8) without ever calling `skill_check` |
| NEW (drafted) | `get_combat_status → skill_check` | 0 — converged in 2 rounds |

**This is a clean, direct reproduction and fix of the real incident**: OLD
exactly matches the production log's pattern (repeated `advance_combat_
turn`/`get_combat_status` calls, never reaching `skill_check`, exhausting
the iteration budget). NEW converges immediately once the description
tells it what to do when the current combatant's turn is blocked on an
unresolved check.

## Testing Strategy

- Real-API verification first (per explicit instruction): construct a
  scenario mirroring the real incident (an investigator whose turn
  requires completing an escape/flee check before combat can move on)
  and compare tool-call sequences under the current description vs. a
  revised one, the same 2-turn/multi-round simulation methodology used
  in `docs/specs/bug-search-scenario-fragmented-queries.md`'s rounds.
- Once a fix is confirmed empirically, add a regression test (mocked, at
  the `app/keeper.py`/`app/combat.py` level) asserting the description
  text change, mirroring how other tool-description fixes in this
  project were tested (wording-only changes can't be usefully unit
  tested for the underlying model-behavior effect, only for the string
  itself, and for confirming no test currently depends on the old text).

## Notes
- Distinct root cause from `docs/specs/bug-search-scenario-fragmented-
  queries.md` (fragmented `search_scenario` queries) even though both
  surfaced from the same production log review — that one is about
  under-specified query-scoping guidance; this one is about a missing
  rule for a specific combat edge case (blocked-on-pending-action). Kept
  as a separate branch per explicit instruction, since the fixes and
  their verification are independent.
- Also found in the same log review, tracked separately: real Luck-stat
  corruption where continuing-fire-damage dice rolls got misapplied via
  `adjust_character(field=luck)` onto the wrong characters
  (`docs/specs/bug-continuing-damage-rolls-corrupt-luck-stat.md` — see
  that spec for the separate investigation).
- UX constraint carried over from discussion: whatever fix lands here
  must not come at the cost of the existing forced-wrap-up fallback (PR
  #55) that keeps a stuck turn from leaving the player with no reply at
  all — the goal is fewer wasted iterations before a *correct* mechanic
  runs, not a harder cutoff that could leave a turn without any
  narration.
