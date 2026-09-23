# Spec: conversation-lock and tool-loop latency — architecture discussion

**Status: discussion only, nothing implemented yet.** This doc exists to
capture an external architecture proposal, check its claims against the
actual code, and lay out options for the user to pick from. Do not start
implementing any of it without an explicit go-ahead on a specific option.

## Changeset Tracking
- **main_v2 start**: origin/main_v2:10972c1a8966f48cd7bbcb0f7c850b1ac8558310
- **implementation end**: N/A — discussion branch

## Background

Earlier in this session (see `docs/specs/bug-add-npc-to-combat-duplicate-
name-guard.md` and PR #55, not yet merged) we diagnosed, from a live
`line-coc-keeper-main-v2` log + pyinstrument profile:

- 99.2% of wall time is `kqueue.control` (pure network I/O wait on the LLM
  API) — confirmed, no CPU hotspot in `app/` code.
- `get_conversation_lock` (`app/locks.py`) is held for the *entire* message
  handling, including every LLM round-trip in the turn — by design, per
  that module's own docstring, to stop two concurrent messages for the same
  conversation from racing on `load_state -> mutate -> save_state`.
- A turn that spends its whole `MAX_TOOL_ITERATIONS` budget on tool calls
  used to return a hardcoded placeholder with zero narration, silently
  diverging game state from what the player was told. PR #55 fixes this
  (duplicate-NPC guard + forced tools-disabled wrap-up call) and raised
  `MAX_TOOL_ITERATIONS` 8→16 as a first mitigation.

The user then pasted a third-party architecture proposal (reproduced in
full below the fold in the PR discussion / chat history) covering three
angles: narrowing the conversation lock via optimistic concurrency control
(OCC), reducing LLM round-trips per turn, and Discord-side UX compensation
(typing indicator). This doc evaluates each claim against the actual
codebase before any of it becomes a real implementation plan.

## Proposal vs. codebase: what's confirmed, what's off

### Confirmed correct
- **Pure network I/O bottleneck.** Matches the earlier pyinstrument finding
  exactly.
- **No typing indicator today.** Grepped `app/discord_bot.py` — `on_message`
  (line 1202) goes straight into `_handle_message(message)` with no
  `channel.typing()` anywhere in the file. This is a real, free win — see
  below.
- **Multi-round tool-call loops are real and costly.** Already established
  from the live log (up to 8, now up to 16, sequential round-trips in one
  turn).

### Needs correction: the state-locking picture is more nuanced than the
proposal assumes

The proposal's "Solution 1" claims the codebase lacks fine-grained state
locking and proposes adding a `StateManager` with per-write locking. **This
already exists.** `app/locks.py`'s `get_state_lock(conversation_id)` is a
`threading.RLock` used by `_mutate_and_save_state` (`app/keeper.py:1211`)
and by every other state-mutating helper (`app/checkpoints.py`,
`app/legacy_commands.py`, `app/scene_digest.py` — 15+ call sites). Every
individual tool call already does load-fresh → mutate → save under this
lock, not against a stale in-memory copy. So the specific failure mode the
proposal's `StateMutationConflictError`/revision-check sketch is designed
to prevent (two full-object load-mutate-save cycles racing on a stale
snapshot) is **already prevented** at the per-write level, independent of
the outer `conversation_lock`.

That means narrowing `conversation_lock` isn't "add the locking that's
missing" — it's "remove a *redundant* layer of locking, and find out what
else it was silently also protecting." Two things it's plausibly protecting
that the proposal's sketch doesn't address:

1. **Cross-tool-call dependency within one turn.** A single turn routinely
   does `start_combat` → `add_npc_to_combat` → `roll_dice`, each depending
   on the previous one's effect being visible. Each call is individually
   atomic, but if a *different* concurrent turn's tool call (e.g. another
   player's `damage_combatant`) interleaves between them, this turn's LLM
   is now reasoning from tool-result text that's already stale by the time
   it makes its next call — not data corruption, but the Keeper narrating
   from a wrong picture of the fight. This is the kind of bug that only
   shows up in actual multi-player play, not in a solo test session — worth
   flagging since the diagnosis session so far has been single-player.
2. **Conversation history ordering.** `run_conversation`'s `history` param
   is a snapshot of `state.log` captured once at the start of the turn. If
   two turns for the same conversation actually run concurrently, the
   second one's LLM call may never see the first turn's narration in its
   own conversation history at all (it captured `history` before the first
   turn's log entries existed) — a correctness gap for the actual
   scene-continuity experience, separate from HP/SAN data loss.

Neither of these is necessarily disqualifying, but the proposal's code
sample (a bare `mutate_state(conversation_id, mutation_fn)` wrapper) doesn't
solve either — it solves a problem this codebase doesn't currently have,
while leaving the two problems above unaddressed. Any real narrowing of the
lock needs a design for these two, not just an OCC write path.

### `parallel_tool_calls` — unverified, needs a real check before assuming either way
`app/providers/openai_provider.py`'s `request_kwargs` never sets
`parallel_tool_calls` at all, so it's whatever the Responses API defaults
to. Two open questions before treating this as a free win: (1) does the
currently configured model (`gpt-5.6-luna`, reasoning-enabled via
`KEEPER_REASONING_EFFORT`) actually support parallel tool calls — reasoning
models on other providers often don't, similar to `temperature` already
being silently dropped for this model per `openai_provider.py`'s existing
`_unsupported_params` fallback; and (2) even if supported, does the
`google-genai`/Anthropic equivalents (`gemini_provider.py`,
`anthropic_provider.py`) have the same default, since a Discord-side change
shouldn't only work for one of the three configured providers. Needs
testing against the real API, not just reading docs, before writing code.

### `MAX_TOOL_ITERATIONS` 8→3-4 + hard fallback — reconsider now that PR #55 changes the tradeoff
The proposal's "hard fallback" (`else: forcibly skip to narration`) is
*exactly* what PR #55 just added — but PR #55's version still lets the tool
calls that already happened execute normally, then adds one more
tools-disabled call to narrate them, rather than truncating and handing a
partial tool-call transcript to a separate "Narrator" step as the proposal
sketches. Given that fix, lowering `MAX_TOOL_ITERATIONS` back down from 16
is now a *safer* trade than it was before this session started (worst case
is a wrap-up narration instead of a bare placeholder either way) — but it's
a real UX tradeoff either direction: lower = faster individual turns,  more
turns that get cut short mid-plan (e.g. "add 3 monsters" now needing more
than one turn to fully set up); higher = slower worst-case turns, fewer
turns cut short. This is a product call, not a correctness one — see open
questions below.

### Macro tools (e.g. `initialize_encounter(enemies=[...], initial_checks=[...])`)
Directionally reasonable — collapsing `start_combat` + N×`add_npc_to_combat`
+ initial rolls into one call would cut real round-trips for the most
common multi-round pattern we've actually observed in the live log. But
this is a prompt/tool-schema redesign in the same risk class as the melee-
tie/ranged-combat rules work done earlier this session (needs careful
COC7e-correctness attention, a spec of its own, and real playtesting) — not
a quick win, and out of scope for a "narrow the lock" change.

### Typing indicator — do this, low risk
`message.channel.typing()` as an `async with` block auto-repeats Discord's
~10s typing signal for as long as the block is open, so wrapping the whole
`await _handle_message(message)` call in `on_message` (`app/discord_bot.py:
1222`) — started before any lock is acquired, so it fires immediately on
receipt regardless of queue position — directly answers the "language feels
stuck" complaint that kicked off this whole investigation, independent of
whatever happens with the lock/iteration questions. This one seems safe to
just do without a big design discussion.

## Open questions for the user

1. **Typing indicator** — implement now as its own small PR (low risk,
   independent of everything else here)? Recommended yes.
2. **`parallel_tool_calls`** — worth spending time verifying against the
   real OpenAI API whether the configured model supports it before writing
   code, or shelve this angle?
3. **Lock narrowing (OCC-style)** — given it isn't "adding missing safety"
   but "removing a layer while designing around two real gaps it currently
   also covers" (cross-tool-call staleness + history-ordering), is this
   still worth pursuing, and at what priority relative to the other items?
4. **`MAX_TOOL_ITERATIONS`** — keep at 16 (current, PR #55) and revisit
   later with real multi-NPC-turn data, or lower it now that the wrap-up
   fix makes a lower cap safer?
5. **Macro tools** (`initialize_encounter` etc.) — worth a dedicated spec
   of its own later, or not a priority right now?

## Notes
- Nothing in this branch should be implemented until the user picks which
  of the above to actually pursue — this doc is the discussion artifact,
  not a plan.
