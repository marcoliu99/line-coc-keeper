# Spec: advance_combat_turn spammed instead of resolving a pending action

## Changeset Tracking
- **main_v2 start**: origin/main_v2:80f245fc6591a021b40dbbedb9d44d41e6b9ec9f
- **implementation end**: TBD

## Purpose & Scope

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
