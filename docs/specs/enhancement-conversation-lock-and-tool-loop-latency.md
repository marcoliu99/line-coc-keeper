# Spec: conversation-lock and tool-loop latency — architecture discussion

**Status: first batch implemented** (typing indicator, queue-ack,
`MAX_TOOL_ITERATIONS`/watermark — see Changeset Tracking below). This doc
also captures the external architecture proposals that led here, checked
against the actual code before anything became a real implementation
plan; items that came off the backlog live in the split-out mini-specs
linked from the status table.

## Status at a glance

| # | Item | Status |
|---|---|---|
| 1 | Typing indicator (`channel.typing()`) | **Decided** — ship |
| 2 | Queue-ack on `conversation_lock` contention (delayed-notice, ~10s) | **Decided** — build (no OCC) |
| 3 | `MAX_TOOL_ITERATIONS` → 5, `HIGH_ITERATION_WATERMARK` = 4 | **Decided** |
| 4 | `parallel_tool_calls` real-API verification | **Answered: already on by default, no code change needed.** Verified against the real API — see "`parallel_tool_calls`" below |
| 5 | Macro tools (`initialize_encounter` etc.) | **Decided: backlog — split into its own doc**, `docs/specs/enhancement-macro-combat-initialization-tool.md` |
| 6 | Prompt caching / `cached_input_tokens` | **Answered: works, no action needed.** Verified against the real API with the real production prompt — 99.9% cache hit on repeat calls. See "Prompt caching" below |
| 7 | Streaming | **Open, backlog** — needs its own design (edit-rate-limit batching) |
| 8 | Executor model tiering, `reasoning_effort=none`, output-token capping, dynamic tool scoping, and a real bug found along the way | **Split into its own doc** — `docs/specs/enhancement-executor-model-tiering-and-tool-scoping.md`. Needed more real-API trials than made sense to keep bundled here; three rounds of verification done there already |

Items 1-5 are decided and make up this branch's actual implementation
plan below. Item 6 is answered and closed. Item 8's whole cluster lives in
the split-out mini-spec — see that doc for the model-tiering/tool-scoping
discussion and all the real-API verification data gathered for it.

## Changeset Tracking
- **main_v2 start**: origin/main_v2:10972c1a8966f48cd7bbcb0f7c850b1ac8558310
- **implementation end**: enhancement/conversation-lock-and-tool-loop-latency:0d22fb2 — ruff/mypy/compileall/pytest all green (487 passed, 6 subtests)

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

The user then pasted a third-party architecture proposal covering three
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

### Needs correction: the state-locking picture is more nuanced than the proposal assumes

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
a real UX tradeoff either direction: lower = faster individual turns, more
turns that get cut short mid-plan (e.g. "add 3 monsters" now needing more
than one turn to fully set up); higher = slower worst-case turns, fewer
turns cut short. This is a product call, not a correctness one — see
decisions below.

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
typing signal for as long as the block is open — **verified directly
against the installed `discord.py` 2.7.1 source**
(`discord.context_managers.Typing.__aenter__` spawns a background task
that does `while True: await asyncio.sleep(5); await typing(channel.id)`,
cancelled cleanly on exit), not just the docstring. So wrapping the whole
`await _handle_message(message)` call in `on_message`
(`app/discord_bot.py:1222`) — started before any lock is acquired, so it
fires immediately on receipt regardless of queue position — is already
sufficient. (A code-level review by Google Gemini separately suggested a
manual heartbeat-resending wrapper for this, on the premise that
`channel.typing()` only lasts ~10s once — that premise is wrong for this
installed version, confirmed directly rather than taken on faith; not
adopting that part.)

## Decisions — first batch (user, after reviewing the above)

1. **Lock**: do **not** narrow it into OCC / let two LLM turns for the same
   conversation actually run concurrently — correctly rejected on the same
   grounds this doc raised (TRPG turns are a strongly-ordered event chain;
   two players' actions resolving in parallel risks genuine narrative
   contradictions, not just a data race). Instead: keep the conversation
   fully serialized, but stop the *silent* wait — when a message arrives
   and the conversation lock is already held, send an acknowledgement
   ("守密人正在處理上一位調查員的行動，你的動作已排入佇列，請稍候……")
   *only if the wait is still ongoing after ~10 seconds* (not immediately on
   contention — the delayed-notice idea itself was a refinement adopted
   from a later review pass, see below; the 10s threshold is the user's
   final call, up from that review's original 2s suggestion — a typing
   indicator is already running the whole time regardless, so the queue
   notice only needs to catch genuinely long waits, not add a second
   "something's happening" signal on top of typing() for short ones),
   so the player gets feedback instead of tens of seconds of nothing that
   reads as the bot being dead, without spamming a notice for waits that
   resolve almost instantly. This preserves both the multi-step tool-call
   dependency chain and conversation-history ordering (the actual
   correctness gaps this doc raised against OCC) while fixing the "looks
   like it crashed" UX problem.
2. **Typing indicator**: ship immediately (independent, low-risk win).
3. **`MAX_TOOL_ITERATIONS`**: final number is **5** (from 16) — low enough
   to cut off the worst 80+-second chains, relying on PR #55's forced
   wrap-up so "cut off early" still means real narration, not silence.
   `HIGH_ITERATION_WATERMARK` = **4** (cap − 1, keeping the "one iteration
   before actually hitting the wall" gap — a watermark equal to the cap
   would only ever fire in lockstep with hitting it, which PR #55's
   wrap-up path already logs on its own and adds no earlier signal).
4. **`parallel_tool_calls`**: verified against the real API (see
   "`parallel_tool_calls`" section below) — already on by default for the
   configured model, `parallel_tool_calls=False` is the only setting that
   visibly changes anything. No provider/prompt change needed; nothing to
   implement for this item.
5. **Macro tools** (`initialize_encounter` etc.): backlog. Not part of this
   latency hotfix — revisit once the front-line UX fixes (typing + queue
   ack) are stable, since it's a combat-state-machine + tool-schema
   redesign with real regression-testing cost, same risk class as the
   melee-tie/ranged-combat rules work done earlier this session.

Execution order: typing indicator + iteration tuning first (fast,
independent wins), then the queue-ack mechanism (the actual fix for the
"語塞" complaint that started this whole investigation). `parallel_tool_
calls` verification (item 4) is already done — see below — and needs no
implementation work either way.

## Implementation plan (this branch)

- `app/discord_bot.py`: wrap `on_message`'s `await _handle_message(message)`
  in `async with message.channel.typing():`, started before any lock
  acquisition so it fires immediately on receipt.
- `app/config.py`: `MAX_TOOL_ITERATIONS` default 8→5 (the earlier `.env`
  override on `line-coc-keeper-main-v2` set it to 16 as a stopgap before
  this decision; that override gets removed/updated to match once this
  lands and the bot is restarted). `HIGH_ITERATION_WATERMARK` default 4.
- `app/providers/{openai,anthropic,gemini}_provider.py`: track how many
  iterations a turn actually used; if it reaches `HIGH_ITERATION_
  WATERMARK` (4, separate from `MAX_TOOL_ITERATIONS` itself so the alarm
  threshold can move independently of the hard cap), emit an
  `observability.event("llm.turn.high_iteration_count", level=WARNING,
  iteration_count=..., watermark=4)` — greppable/alertable without needing
  to re-derive it from the per-request `iteration` field already logged on
  every `llm.request` span.
- `app/commands/router.py`: a helper (e.g. `_acquire_conversation_lock_
  with_notice(conversation_id, reply)`) replacing the bare `async with
  locks.get_conversation_lock(conversation_id):` at each of `_handle_
  text_message_impl`'s call sites. If the lock isn't immediately available,
  start a background task that waits ~10.0s and, only if the lock is
  *still* not acquired by then, sends the queued-notice reply — cancelled
  the moment the real `lock.acquire()` succeeds. This avoids sending a
  notice for waits that resolve almost immediately, unlike a plain
  `lock.locked()` check done once up front (this refinement came from
  reviewing Google Gemini's own take on this same design — see the
  "Prompt caching" section below for the parts of that review that didn't
  hold up).

## Testing Strategy
- Typing indicator: a test on `on_message` confirming `channel.typing()` is
  entered around `_handle_message`.
- `MAX_TOOL_ITERATIONS` default: a config test asserting the new default
  value (mirroring however existing config defaults are tested, if at all).
- High-iteration observability event: extend `tests/test_llm_turn_wrapup.py`
  style mocking — a turn using more than 4 iterations must emit the event
  with the right count; a turn using 4 or fewer must not.
- Queue-ack helper: a router-level test with the conversation lock
  pre-acquired (simulating an in-flight turn), asserting a second message
  triggers the queued-notice reply before it blocks on the lock; and a
  case with the lock free asserting no notice is sent.
- Standard four checks (ruff, mypy, compileall, pytest).

## Prompt caching — verified working, closed

Two external reviews (the original proposal and a later Google Gemini
code-level pass) both claimed prompt caching was broken for the real
Keeper turn loop, citing `cached_input_tokens: 0` from the live log. Both
readings turned out to be built on the same misattributed evidence: the
only log lines that ever carry `input_tokens`/`cached_input_tokens` come
from `openai_provider._create_response` (the *synchronous* helper behind
`analyze_image`/`analyze_text`/the rolling conversation-summarization call
at `app/keeper.py:3188`), not the real turn loop
(`_create_response_async`, used by every Keeper/Executor/Narrator call),
which never logs usage fields at all — confirmed by the `iteration: None`
field on those specific log lines (every genuine turn-loop request always
carries a real iteration number) and by tracing the call to its actual
caller.

Settled directly with a real-API test instead of more log inference: a
throwaway script (`verify_prompt_caching.py`, not committed) built
`instructions` exactly the way `app/agents/executor.py` does, from the
real live campaign state, and fired 3 consecutive Responses API calls with
that identical prefix:

| Call | input_tokens | cached_input_tokens | duration |
|---|---|---|---|
| 1 (cold) | 17469 | 0 | 5087ms |
| 2 (same prefix) | 17470 | **17448** (99.9%) | 2901ms |
| 3 (same prefix, +2s) | 17468 | **17448** (99.9%) | 3437ms |

**Caching already works** — 99.9% hit rate on repeat calls, ~30-40%
latency reduction. No prompt restructuring needed. This closes item 6;
adding ongoing usage/cache-hit logging to the real loop for production
visibility is a nice-to-have, not gating anything, and isn't part of this
branch's plan.

## `parallel_tool_calls` — verified already on by default, closed

Note while reading this: the live `.env` has since been updated to
`OPENAI_MODEL=gpt-6-luna` (was `gpt-5.6-luna` throughout the rest of this
doc's earlier verification rounds) — this test ran against whatever model
is actually configured, not a hardcoded one.

`verify_parallel_tool_calls.py` (throwaway, scratchpad-only) sent the same
two-independent-skill-check prompt three ways: `parallel_tool_calls` left
unset (exactly how `openai_provider.py` calls it today), explicitly `True`,
and explicitly `False`. Used the real configured `KEEPER_TEMPERATURE` (0.6,
not a hardcoded "safe" value like earlier test rounds used) specifically so
the already-known `temperature`-rejected-by-reasoning-models 400 would
actually reproduce, then retried without it — mirroring
`openai_provider.py`'s real `_unsupported_params` fallback inline instead
of hardcoding around the issue:

| Config | Result |
|---|---|
| unset (today's actual behavior) | 400 on `temperature` (expected, self-healed same as production), then **2 tool calls in one response** |
| `parallel_tool_calls=True` | Same 400/retry, then **2 tool calls in one response** |
| `parallel_tool_calls=False` | Same 400/retry, then **only 1 tool call** — the model held the second `skill_check` back |

**Conclusion: parallel tool calling is already on by default for the
configured model** (`unset` behaves identically to explicit `True`, and
`False` is the only setting that visibly changes anything, by *reducing*
batching). No code change needed — adding `"parallel_tool_calls": True` to
`request_kwargs` would be a no-op given the default already matches it.
This closes item 4 the same way item 6 (caching) closed: measured directly
against the real API instead of assumed from the original proposal's
"confirm the default isn't disabled" framing.

The `temperature` 400 itself is not a new finding — `openai_provider.py`
already self-heals it in production via `_unsupported_params` — this test
just deliberately reproduced it with the real configured value (rather than
earlier verification rounds' hardcoded `temperature: 1`, which happened to
dodge the issue) to confirm the fallback still engages correctly for
whatever model ends up configured, including the now-current `gpt-6-luna`.

## Fourth batch: "four-layer fix" for MAX_TOOL_ITERATIONS (another external proposal)

Same treatment as the earlier batches — check each layer against what's
already decided/shipped and against real log evidence before adopting
anything.

### Layer 1 (config lowering + watermark) — already decided, no new information
`MAX_TOOL_ITERATIONS=5` + `HIGH_ITERATION_WATERMARK=4` is exactly item 3's
decision already in this doc. Nothing to change.

### Layer 2 (reserve the last iteration as tools-disabled, instead of PR #55's extra wrap-up call) — a real alternative design, conflicts with already-shipped code
**This is not additive to PR #55 — it's a different design for the same
problem, and PR #55 is already merged and live** (`app/providers/{openai,
anthropic,gemini}_provider.py`, `enable_wrapup` gating, four review rounds).
Comparing the two:

- **PR #55 (shipped)**: all `N` iterations get to try tools; only if the
  loop truly exhausts them without ever producing text does it pay for
  *one extra* (`N+1`th) tools-disabled call to force narration. Best case
  (model finishes in 2 rounds): 2 calls, no waste. Worst case: `N+1` calls.
- **This proposal**: strip tools on iteration `N-1` (the last one)
  pre-emptively, guaranteeing narration fits inside the existing budget —
  worst case is `N` calls, one cheaper than PR #55's `N+1`, but every turn
  that would have needed the full `N` rounds now only gets `N-1` real
  tool-calling rounds before being forced to narrate on whatever facts
  exist so far.

Real tradeoff, not a strict improvement: proposal saves one API call in
the worst case, at the cost of one fewer round of real tool-calling
headroom in every turn that runs long — with `MAX_TOOL_ITERATIONS` already
tight at 5, that's a meaningful chunk of an already-scarce budget (a
`start_combat` + 2×`add_npc_to_combat` turn, 3 rounds, would have 2 rounds
of slack under PR #55's scheme vs. 1 under this one). Also worth noting the
pasted code sketch uses Chat-Completions-style `messages.append({"role":
"system", ...})` and reads `response.tool_calls` — this codebase's OpenAI
adapter uses the Responses API (`instructions` field + `input` list,
`response.output` filtered for `function_call` items), so it'd need
translating either way, same as every other pasted proposal so far.

**Decided: keeping PR #55's shipped design as-is, not swapping to this.**
User confirmed the one-call latency saving isn't worth trading away a
round of tool-calling headroom out of an already-tight 5-iteration budget.
Revisit only if real production data (once the first-batch fixes here are
live) shows turns still frequently exhausting all 5 iterations even with
the wrap-up safety net.

### Layer 3 (hard-cap search_scenario to 1 call per turn) — no evidence this problem actually exists
The proposal assumes repeated `search_scenario` calls are "鬼打牆"
(unproductive repetition/synonym-guessing) and proposes both a prompt
instruction and a hard tool-gateway rate limit (reject the 2nd+ call with a
`rate_limited` status). **Checked against the actual queries logged in the
diagnosed 82.9s "語塞" turn** (this doc's own earlier root-cause finding)
— the four `search_scenario` calls in that turn were:

1. "柯比特藏身處 遭到攻擊 開始戰鬥 Corbitt body attacks Flesh Ward"
2. "Walter Corbitt attacks investigators when they approach body hiding place..."
3. "Corbitt casts Flesh Ward as soon as anyone enters the house..."
4. "Corbitt Dominate spell variant effects on investigator check"

These are four **different** queries progressively covering different
facts about one complex NPC (when it wakes up, its Flesh Ward ability, its
separate Dominate ability) — not the same query rephrased/re-guessed. A
hard 1-call-per-turn cap would have blocked the model from looking up both
Flesh Ward *and* Dominate in the same turn, forcing it to either guess at
the ability it didn't get to look up or spend a whole extra turn on it —
a real capability regression for legitimately multi-faceted scenario
research, to fix a failure mode (repeating the *same* query) this doc has
no actual evidence of happening. Not recommending this layer without first
finding a real log example of `search_scenario` being called multiple
times with the *same or near-synonymous* query in one turn — right now
this would be guessing at a fix for a problem not yet observed, the same
mistake this whole doc has been correcting other proposals for.

### Layer 4 (macro tool `initialize_combat`) — split into its own doc
Identical idea to this doc's item 5 (`initialize_encounter`) — split out,
along with the design constraint this discussion already resolved (no
silent duplicate-name filtering; auto-suffix only as a last-resort
fallback, and only into the player-visible display name — see PR #55's
`find_live_enemy_by_any_alias` fix for why), into
`docs/specs/enhancement-macro-combat-initialization-tool.md`. Still
backlog, same risk class as the melee-tie/ranged-combat rules work — that
new doc has the full schema sketch and open questions for whenever it
comes off the backlog.

## Notes
- Streaming (item 7) stays backlog — only helps the final narration output
  (not the multi-round tool-calling majority of turn latency) and needs its
  own edit-rate-limit-aware design (Discord throttles message edits hard)
  before it's a real plan.
- Executor model tiering, `reasoning_effort` tuning, output-token capping,
  and dynamic tool scoping (item 8) — including three rounds of real-API
  verification (model/effort comparison, repeat trials, an 8-scenario
  sweep) and a real bug found along the way
  (`app/agents/tool_gateway.py`'s tool list silently missing
  `search_scenario`) — all live in
  `docs/specs/enhancement-executor-model-tiering-and-tool-scoping.md`.
