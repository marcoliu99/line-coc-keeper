# Spec: duplicate check/Luck-decision button prompts under overlapping requests

## Changeset Tracking
- **main_v2 start**: origin/main_v2:f2c37d5aa10b21c991f39af415321e1b31a6b1b0
- **implementation end**: bug/duplicate-luck-button-prompt — ruff/mypy/compileall/pytest all green

## Purpose & Scope

Real production incident (user-reported Discord transcript, 2026-09-25
~16:20): Marco's Dodge check against an opposed attack resolved (rolled
21 → regular success, offered a Luck buy-up), and the "🍀 Marco，要花
Luck 買到更好的結果嗎？" button prompt was posted **twice**, back to
back, for what should be a single Luck decision. A "👉 ...請選擇要採取
的防守／行動方式，並由你觸發擲骰：" (choice-check) prompt also appeared
in the same window.

Confirmed in `/Users/marcoliu/workspace/line-coc-keeper-main-v2/.runtime/
bots/profile-async.log`: the `discord_reply` (LLM-narration) event log
shows only the roll-result narration and the final "spent 6 Luck..."
resolution — the button-prompt text itself isn't in the structured log at
all (`app/discord_bot.py`'s `_post_check_buttons`/`_post_luck_buttons`
send fixed-template text directly via `_send_direct_message`, not through
`observability.event`), so this had to be traced through code, not log
search.

## Root cause

`app/discord_bot.py` has exactly three call sites of `_post_pending_buttons`
(line 738 in `CheckButton.callback`'s `finally`, line 928 in
`LuckSpendButton.callback`'s `finally`, line 1432 in `on_message`'s
`finally`) — every one of them:
1. Captures a **local** `before_pending`/`before_luck_pending` snapshot
   at the start of its own request handling.
2. Later (after its own command/turn processing finishes — which can
   involve a Keeper/LLM call taking several seconds), reloads state fresh
   and calls `_post_check_buttons`/`_post_luck_buttons`, which post a
   button for every `pending_checks`/`pending_luck_decisions` entry that's
   different from **that call's own** `before_*` snapshot.

There is no cross-call, durable "has a button already been posted for
this exact decision" marker anywhere — only each call's own point-in-time
local comparison. If two of these flows overlap in time for the same
conversation (e.g., Marco clicks his Luck button while Ken's `on_message`
turn is still in flight, or two people act close together), each one's
`before_*` snapshot predates the same new decision, each one's fresh
reload confirms the decision is still current, and **both** post a
button for it — a real, structural race condition, not a logic typo.

Confirmed by direct code reading of `_post_check_buttons`
(`app/discord_bot.py:749-`) and `_post_luck_buttons` (`:939-`): both do
`if before_pending.get(owner_id) == check: continue` (skip only if
identical to *this call's* stale snapshot) then `if current_state.
pending_checks.get(owner_id) != check: continue` (skip only if the entry
has since changed/resolved) — neither check can detect "another
concurrent call already posted this."

## Fix (implemented)

Add a durable, in-state "already posted" marker to each `pending_checks`/
`pending_luck_decisions` entry (e.g. a `"_buttons_posted": True` key,
consistent with these already being plain dicts, not dataclasses) that
gets checked-and-set atomically under `locks.get_conversation_lock`
*before* the actual Discord send — not just diffed against a local
snapshot. The critical section is short (reload, check flag, set flag,
save); the actual `channel.send()`/interaction call stays outside the
lock, same as today, so Discord network latency never blocks other
conversation activity.

This changes the skip condition in both `_post_check_buttons` and
`_post_luck_buttons` from "differs from my local snapshot" to "differs
from my local snapshot AND hasn't been marked posted by anyone yet,"
closing the race regardless of which two call sites actually overlap in
a given incident — the fix doesn't need to identify the exact two flows
that raced in the reported incident, since the structural gap is the
same regardless of which pair of the three call sites collide.

## Real verification

Application-logic/concurrency bug, not model behavior — verified by
direct function-level testing (calling `_post_luck_buttons` twice with
two independently-stale `before_pending` snapshots against the same
underlying decision, confirming both calls send a message today), not
real-API trials.

## Testing Strategy

- Regression test proving the race today: two calls to
  `_post_luck_buttons` (or `_post_check_buttons`), each with its own
  `before_*` snapshot taken before the same decision existed, both send a
  message — must be reduced to one after the fix.
- Test that a single call still posts normally (no regression for the
  common, non-overlapping case).
- Test that a genuinely new, different decision for the same owner_id
  (posted after the first was resolved) still gets its own button post —
  the "already posted" marker must be per-decision-identity, not a
  blanket "this owner_id already got one ever."
- Standard four checks (ruff, mypy, compileall, pytest).

## Notes
- This is a different bug from `docs/specs/bug-self-corrected-check-
  leaves-stale-pending.md` (which is about the model narrating a
  correction without clearing mechanical state) — this one is pure
  Discord-layer concurrency, no LLM involved at all.
- The "👉 ...請選擇要採取的防守／行動方式" choice-check prompt appearing
  in the same window as the duplicate Luck prompt is very plausibly a
  second, unrelated new attack's defense-choice being posted by the same
  overlapping-call mechanism, not a separate bug — the fix above covers
  both `_post_check_buttons` and `_post_luck_buttons` symmetrically.

## Post-implementation review fixes

Two real gaps found by review, both fixed before merge:

1. **The `_buttons_posted` marker broke legacy button identities.**
   `check_identity.py`'s `_legacy_id` derives an entry's identity by
   hashing its *entire* dict (minus `check_id`/`decision_id`) when no
   explicit id is set — true for persisted pre-identity checks and for
   the opening-scene checks `app/commands/handlers/system.py:627-641`
   registers without one. The button's identity token was computed from
   the entry *before* it was marked posted; a callback later re-derives
   the same identity from the *persisted* (now-marked) entry to verify
   the click. Since `_buttons_posted` wasn't excluded from that hash, the
   two tokens differed — every such button would be rejected as expired
   on the very first click. Fixed by excluding `_buttons_posted` from
   `_legacy_id`'s hashed value, the same way `check_id`/`decision_id`
   already are.
2. **A failed send left the claim permanently stranded.** If
   `_send_direct_message` raised *after* `_buttons_posted` was already
   saved (an exhausted rate-limit/network retry, or the process exiting
   between the save and the send), the entry stayed marked posted forever
   — every future `_post_check_buttons`/`_post_luck_buttons` call would
   skip it, permanently stranding a genuinely still-pending check/
   decision with no button ever shown again. Fixed by tracking whether
   the claim was actually saved this call (`claimed`), and on any
   exception after that point, releasing it via
   `_release_stranded_posting_claim` — which re-verifies under the
   conversation lock that the persisted entry (ignoring the marker
   itself) still matches what was claimed before clearing it, so it
   never clobbers a different entry that may have replaced it in the
   meantime.
