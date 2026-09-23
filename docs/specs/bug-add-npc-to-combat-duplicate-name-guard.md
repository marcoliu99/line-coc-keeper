# Spec: add_npc_to_combat duplicate-name guard + forced turn wrap-up

## Changeset Tracking
- **main_v2 start**: origin/main_v2:10972c1a8966f48cd7bbcb0f7c850b1ac8558310
- **implementation end**: bug/add-npc-to-combat-duplicate-name-guard:af38360 — ruff/mypy/compileall/pytest all green (454 passed, 6 subtests)

## Purpose & Scope

Diagnosed from `.runtime/bots/profile-async.log` on the `line-coc-keeper-main-v2`
deployment (session around 2026-09-23T12:54–13:03Z, "The Haunting" scenario).
The player shot at the reanimated corpse of **Walter Corbitt** — the
scenario's single named antagonist — twice, once at 12:57 and once at 13:01.
Both times the Keeper's tool-calling turn searched the scenario for Corbitt's
stats and then called `add_npc_to_combat` for "柯比特"/Corbitt, ending up with
**two (or more) separate combat entries for the same monster**, each with
its own independent HP pool.

Root cause: `add_npc_to_combat` (`app/keeper.py:2496`) → `combat.add_npc`
(`app/combat.py:192-227`, non-ally branch) unconditionally calls
`create_enemy_card` + `add_enemy_card_to_combat` — there is no check for
whether a combatant with this name is already an active (non-defeated)
enemy in the current fight. Every call creates a brand-new `EnemyCombatCard`
and appends a new `Combatant` to `state.combat.order`, so calling it twice
for the same name produces two independently-tracked "monsters" instead of
one.

This was made worse by (and only surfaced to the player because of) a
second, related bug, confirmed against the *live* log after the first
`.env` mitigation was already in place: both of those turns exhausted
`MAX_TOOL_ITERATIONS` doing repeated `search_scenario` + `add_npc_to_combat`
calls, so the Keeper never got a turn left over to narrate what it had just
done — the player only saw "（守密人一時語塞，請再說一次剛才的行動）" with
no indication combat had even started, let alone that a duplicate had just
been created. `MAX_TOOL_ITERATIONS` was raised from 8 to 16 via `.env` as a
first mitigation, but that only makes the iteration-exhaustion case rarer —
it doesn't fix the two bugs it was masking:

1. `add_npc_to_combat` has no duplicate-name guard (this doc's original
   scope, see below).
2. **When the tool-call loop exhausts its iteration budget, real state
   mutations (start_combat, add_npc_to_combat, etc.) have already been
   applied and saved, but the turn returns the generic fallback text with
   zero narration of what just happened.** Confirmed directly in the live
   log: turn `req_1c15bf27c8fe4609b03b4f5cc5be27a7` (13:01:34–13:02:51)
   called `start_combat` and `add_npc_to_combat` ×2 for the scenario's
   Corbitt NPC, hit the iteration cap, and returned only the placeholder.
   The player saw no combat-start narration at all, and the *next* turn's
   narration ("目前沒有任何跡象顯示它正在動") directly contradicted the
   mechanical state that had just been created — the game state and what
   the player was told about it had silently diverged. This is worse than
   the wasted reply itself: player and Keeper are now working from
   different views of reality until something forces a resync.

Both bugs get fixed in this one branch since #2 is the actual root cause of
why #1 became player-visible at all (a normal single-iteration turn would
have let the Keeper simply notice/say "Corbitt is already in this fight").

Scope of fix #1: guard the **enemy** path of `add_npc_to_combat` against
adding a same-named combatant that is already active in the current fight.
The ally path (`is_ally=True`, e.g. a hired guide) is out of scope — no
evidence of the same failure mode there, and ally identity/stacking has
different semantics (out of scope creep to redesign it here).

Scope of fix #2: when a provider's tool-calling loop is about to exhaust
`max_iterations` still holding unresolved tool calls, execute those last
tool calls as normal, then issue **one additional LLM request with tools
disabled** (forcing plain text, no more mechanics) so the Keeper always gets
a chance to narrate whatever mechanical state it just changed, instead of
silently returning the placeholder. Applies to all three providers
(`openai_provider.py`, `gemini_provider.py`, `anthropic_provider.py`) since
all three share the same fallback string and the same "loop falls off the
end" shape — only `openai_provider.py` is actually configured
(`LLM_PROVIDER=openai` in both live deployments' `.env`), but fixing only
one provider would leave a silent trap for whoever switches later.

## Changes

- `app/combat.py`: add a small helper, e.g. `find_live_enemy(state, name)
  -> Combatant | None`, that looks for a non-defeated `side == "enemy"`
  combatant whose `name`/`display_name` normalized-matches the given name —
  reusing the same normalize + substring-match semantics `_find_combatant`
  already uses elsewhere in this file, just scoped to live enemies only, so
  matching behavior stays consistent with the rest of the module.
- `app/keeper.py`, `add_npc_to_combat` tool handler (`_mutate_add_npc`,
  around line 2496): before calling `combat.add_npc(...)`, check
  `combat.find_live_enemy(target_state, npc_name)`. If a match exists, skip
  creating a new card/combatant entirely and return the existing status with
  a `note` explaining a duplicate was rejected (mirrors the existing
  `index_note` pattern used a few lines above for the HP-mismatch case) —
  something the Keeper (LLM) can read and act on, e.g.: "系統偵測到「X」已
  經在戰鬥中且尚未倒下，沒有重複建立第二份——這隻怪物的血量與狀態沿用原本
  那份，之後不要為同一隻怪物再呼叫一次 add_npc_to_combat。"
- No change to `combat.add_npc`'s own signature/return type — it keeps
  returning `CombatState` as documented at the top of the file ("public API
  deliberately keeps the old entry points"). The duplicate check happens at
  the tool-handler call site, same layer as the existing index-HP
  consistency check.

- `app/providers/openai_provider.py` (`run_conversation`, ~line 356-409):
  turn the `for iteration in range(max_iterations):` loop into a
  `for...else` — the `else` clause (runs only if the loop completed without
  `break`, i.e. every iteration returned tool calls) issues one more
  `_create_response_async` call reusing `active_previous_response_id` and
  the last round's `input_items`, but with the `tools` kwarg *omitted*
  entirely (forces plain text — no tools means nothing to call) and a short
  appended note in `instructions` telling the model its tool budget for this
  turn is used up and it must now narrate the outcome for the player in
  plain text. If that call succeeds and returns non-empty `output_text`, use
  it as `final_text`; if it also fails or comes back empty, keep the
  original placeholder (never worse than today, only better).
- `app/providers/anthropic_provider.py` and `app/providers/gemini_provider.py`:
  same shape, adapted to each SDK's tool-loop structure (Anthropic:
  `tool_choice={"type": "none"}` or omit `tools`; Gemini: omit
  `tool_config`/function declarations on the wrap-up call). Read each file's
  existing loop first — the exact mechanics differ per SDK even though the
  intent is identical.

## Testing Strategy

- `tests/test_combat.py` (or wherever `add_npc`/`add_npc_to_combat` is
  already covered): add a case that calls `add_npc_to_combat` twice with the
  same name and asserts only one enemy combatant/card exists afterward, and
  that the second call's response carries the duplicate `note` instead of
  silently no-op'ing.
- A case confirming a **defeated** same-named enemy does *not* block a new
  add (a monster narratively coming back, or a fresh instance after the
  first was killed, should still work).
- A case confirming two **different**-named enemies are unaffected (no false
  positives from the substring-match reuse).
- For the wrap-up-call fix, per provider: a test that drives the tool loop
  through `max_iterations` iterations that all return tool calls (mock the
  SDK client), and asserts (a) a final wrap-up request was made with no
  tools offered, and (b) its text becomes the returned value instead of the
  placeholder. Also a case where the wrap-up call itself fails/returns
  empty — must still return the placeholder, not raise.
- Run the standard four checks (ruff, mypy, compileall, pytest) before
  calling this done.

## Notes

- This is purely a Discord-side (`line-coc-keeper-main-v2`) live-deployment
  finding, but the fix lands in shared `app/` code used by both live
  deployments — no scenario- or channel-specific special-casing.
- The wrap-up call adds one extra LLM round-trip, but only in the rare case
  where a turn was already about to exhaust its iteration budget — it does
  not add latency to normal turns.
