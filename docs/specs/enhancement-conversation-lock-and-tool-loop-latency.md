# Spec: conversation-lock and tool-loop latency — architecture discussion

**Status: discussion only, nothing implemented yet.** This doc exists to
capture an external architecture proposal, check its claims against the
actual code, and lay out options for the user to pick from. Do not start
implementing any of it without an explicit go-ahead on a specific option.

## Status at a glance

| # | Item | Status |
|---|---|---|
| 1 | Typing indicator (`channel.typing()`) | **Decided** — ship |
| 2 | Queue-ack on `conversation_lock` contention (delayed-notice, ~2s) | **Decided** — build (no OCC) |
| 3 | `MAX_TOOL_ITERATIONS` → 5, `HIGH_ITERATION_WATERMARK` = 4 | **Decided** |
| 4 | `parallel_tool_calls` real-API verification | **Decided** — standalone script, out-of-band |
| 5 | Macro tools (`initialize_encounter` etc.) | **Decided** — backlog |
| 6 | Add usage/`cached_input_tokens` logging to the real turn loop | **Still recommended** (ongoing production visibility) — but the underlying question it was for (#8) is now answered directly, see below |
| 7 | Cap Executor's output tokens | **Recommended, not yet decided** — scope: `executor.py` only |
| 8 | Restructure prompt ordering for caching | **Answered: not needed.** Verified against the real API with the real production prompt — already caching at 99.9% on repeat calls. See results below |
| 9 | Model tiering (cheap model for Executor) | **Verified against real API — see results below**; `gpt-4o-mini` not obviously better, needs its own mini-spec if pursued |
| 10 | `reasoning_effort=none` for Executor | **Verified against real API — see results below**; `gpt-6-luna`/`none` promising, `/low` inconsistent |
| 11 | Streaming | **Open** — needs its own design (edit-rate-limit batching) |
| 12 | Dynamic tool scoping (34→combat/non-combat subset) | **Open** — fold into #9/#10's follow-up mini-spec |

Items 1-5 (first batch) are decided and make up this branch's actual
implementation plan below. Items 6-11 (second batch) are evaluated further
down but **not yet decided** — this doc lays out a recommendation for each,
pending the user's sign-off, same as the first batch went through.

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

## Decisions — first batch (user, after reviewing the above)

1. **Lock**: do **not** narrow it into OCC / let two LLM turns for the same
   conversation actually run concurrently — correctly rejected on the same
   grounds this doc raised (TRPG turns are a strongly-ordered event chain;
   two players' actions resolving in parallel risks genuine narrative
   contradictions, not just a data race). Instead: keep the conversation
   fully serialized, but stop the *silent* wait — when a message arrives
   and the conversation lock is already held, immediately send an
   acknowledgement ("守密人正在處理上一位調查員的行動，你的動作已排入佇
   列，請稍候……") before awaiting the lock, so the player gets instant
   feedback instead of tens of seconds of nothing that reads as the bot
   being dead. This preserves both the multi-step tool-call dependency
   chain and conversation-history ordering (the actual correctness gaps
   this doc raised against OCC) while fixing the "looks like it crashed"
   UX problem.
2. **Typing indicator**: ship immediately (independent, low-risk win).
3. **`MAX_TOOL_ITERATIONS`**: final number is **5** (from 16) — low enough
   to cut off the worst 80+-second chains, relying on PR #55's forced
   wrap-up so "cut off early" still means real narration, not silence.
   `HIGH_ITERATION_WATERMARK` = **4** (cap − 1, keeping the "one iteration
   before actually hitting the wall" gap — a watermark equal to the cap
   would only ever fire in lockstep with hitting it, which PR #55's
   wrap-up path already logs on its own and adds no earlier signal).
4. **`parallel_tool_calls`**: don't reason about it from docs — write a
   small standalone script hitting the real OpenAI API with the configured
   model (`gpt-5.6-luna`, `reasoning_effort=medium`) and a prompt that
   forces multiple simultaneous tool calls (e.g. two independent skill
   rolls), and see whether it actually parallelizes or 400s the same way
   `temperature` did. Only add prompt/provider changes once support is
   confirmed this way. Kept as a standalone experiment, not part of this
   branch's app code.
5. **Macro tools** (`initialize_encounter` etc.): backlog. Not part of this
   latency hotfix — revisit once the front-line UX fixes (typing + queue
   ack) are stable, since it's a combat-state-machine + tool-schema
   redesign with real regression-testing cost, same risk class as the
   melee-tie/ranged-combat rules work done earlier this session.

Execution order: typing indicator + iteration tuning first (fast,
independent wins), then the queue-ack mechanism (the actual fix for the
"語塞" complaint that started this whole investigation). `parallel_tool_
calls` verification runs separately as a throwaway script, not blocking
anything here.

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
  text_message_impl`'s call sites. Revised design after reviewing a third
  pass (see "Third batch" below): if the lock isn't immediately available,
  start a background task that waits ~2.0s and, only if the lock is
  *still* not acquired by then, sends the queued-notice reply — cancelled
  the moment the real `lock.acquire()` succeeds. This avoids sending a
  notice for waits that resolve almost immediately, unlike a plain
  `lock.locked()` check done once up front.

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

## Second proposal batch: per-call latency (max_tokens, prompt caching, model tiering, reasoning_effort, streaming)

**Critical correction found while checking this against the code: there are
two separate turn architectures in this codebase, and most of these
proposals only cleanly apply to one of them.**

- `app/agents/supervisor.py` → `executor.run_executor` (tool-calling loop,
  its own text output discarded — only the tool calls' side effects and the
  `MechanicResult` it assembles matter) → `narrator.run_narrator` (a
  *second*, separate LLM call, no tools, produces the actual reply text).
  This is the path for ordinary free-text roleplay
  (`router.py:584`/`_handle_ordinary_text_message_locked`).
- `app/keeper.py`'s `run_turn`/`_run_turn_impl` — the older, single-call
  architecture: **one** `provider.run_conversation` call that both decides
  tool calls *and* has to produce the final narration text itself, no
  separate Narrator step. Confirmed still live and heavily used: every
  skill-check-result narration goes through this path
  (`app/legacy_commands.py:1133`, inside the check-resolution flow — the
  exact flow behind both "語塞" incidents this whole investigation started
  from, confirmed via the live log's `agent="keeper"` tag vs. `agent=
  "executor"`/`"narrator"` for the Supervisor path), plus KP-sudo and
  system commands (`app/commands/handlers/system.py:659`).

Both share the same `provider.run_conversation` function (which is why PR
#55's wrap-up fix, living inside that shared function, already covers both
paths correctly) — but that also means **"Executor doesn't need long
output" and "give Executor a small/fast model" do not apply to
`keeper.run_turn`'s single call**, since there it's the only call and it
has to produce the actual narration. Capping `max_tokens`/turning off
reasoning/downgrading the model for that path would directly cut narration
quality on the single most common turn shape in actual play (every dice
check). Any implementation of items 1/3/4 below needs to target
`app/agents/executor.py` specifically (where the text output really is
thrown away), not `run_conversation` itself or `keeper.run_turn`.

### 1. Cap output tokens (Executor: ~300, Narrator: ~250-300 words)
Valid for `executor.py`'s call specifically (its text is genuinely unused).
Not valid for `keeper.run_turn`'s single-call path (see above) or for
`narrator.py` if a tight length cap would clip legitimate longer scene
descriptions — worth confirming with the user what narration-length
ceiling is actually acceptable before hardcoding one, rather than assuming
250-300 characters/words is right for this game's tone.

### 2. Prompt caching / `cached_input_tokens`
**The proposal's evidence doesn't actually hold up.** Checked the live log:
only 3 lines anywhere mention `cached_input_tokens`, all from `_create_
response` (the *synchronous* helper behind `analyze_image`/`analyze_text`
— PDF/scenario-image OCR calls, unrelated to gameplay turns), because
that's the only function in `openai_provider.py` that calls
`observability.usage_fields(response)` at all. The actual turn loop
(`_create_response_async`, used by every Keeper/Executor/Narrator call)
**never logs usage/cached-token data in the first place** — its
`observability.span("llm.request", ..., metrics=request_metrics)` never
gets `request_metrics` populated with `usage_fields`. So "all requests show
0 cached tokens" isn't evidence prompt caching is failing — it's evidence
we're not measuring the calls that would tell us either way.

On the ordering claim specifically: `app/keeper.py:_build_static_prompt`'s
own docstring says static-first, scenario text bounded/RAG'd rather than
inlined, and character sheets in the "rare-changing" static block with
HP/SAN/Luck deliberately kept in `_build_dynamic_prompt` instead — i.e. the
static/dynamic split this doc's authors already designed for was intended
to be cache-friendly and matches the proposal's own "correct order"
recommendation (static things first, per-turn state last). It also notes
"Gemini's context caching isn't wired up yet" but says nothing suggesting
OpenAI's is broken.

Recommended first step, before touching prompt structure: add `**
observability.usage_fields(response)` to `_create_response_async`'s
`llm.request.completed` span the same way the sync `_create_response`
already does, ship that alone, and read real `cached_input_tokens` numbers
from the live log. If they're genuinely low despite the already-
cache-friendly static/dynamic ordering, *then* there's a real prefix-
stability bug to chase (e.g. something in `_build_dynamic_prompt` or the
tool list ordering subtly changing byte-for-byte between calls). Don't
restructure the prompt before that measurement exists — we'd be guessing
at a fix for a problem we haven't actually observed yet, the exact mistake
this whole doc started out correcting in the first proposal.

### 3. Model tiering (small/fast model for Executor, keep the big model for Narrator)
Directionally reasonable *for the Executor path specifically* (see above),
but real plumbing cost: `LLM_PROVIDER`/`OPENAI_MODEL`/`KEEPER_REASONING_
EFFORT` etc. are single global config values shared by every caller of
`run_conversation`, including the legacy `keeper.run_turn` path. Giving
Executor its own model means either a new config surface (`EXECUTOR_MODEL`
etc.) threaded through `executor.py`'s own call, or restructuring
`run_conversation` to accept a model override — a real design decision
requiring its own mini-spec (which tool-matching quality bar a cheaper
model needs to clear across all ~10 tools, tested against real cases, not
assumed), not a config-flip.

### 4. `reasoning_effort=none`/`low` for Executor specifically
Same plumbing dependency as #3 (currently one shared `KEEPER_REASONING_
EFFORT`). Once Executor can be addressed independently, this is a small
addition on top of that same change — not separate work.

### 5. Streaming
Real technique for cutting perceived latency (lower TTFT), but two real
constraints to design around, not just "turn it on": (a) mid-loop tool-call
decisions can't act on partial/streamed function-call JSON — a token
stream only helps the *final* narration output, not the multi-round
tool-calling portion that's actually the bulk of the latency we've been
chasing; (b) simulating streaming in Discord means repeatedly editing one
message as text arrives, and Discord aggressively rate-limits message
edits — a naive "edit on every token" implementation would get throttled
fast. Needs its own design (batching edits every N tokens/M milliseconds,
not every token) before this is a real plan, not just an API flag flip.

## Recommendations — second batch (pending user sign-off, not yet decided)

Mirroring how the first batch went (this doc laid out findings, the user
picked): here's the recommended default for each item, ready to just
confirm/override.

1. **Add usage logging to the real turn loop first** (item 6 in the status
   table) — cheap, low-risk, no behavior change (`observability.event`
   logging only), and it's the prerequisite for having real data instead of
   guessing on items 8-10. Recommend bundling this into the same PR as the
   first batch's implementation, since it's small and unrelated to the lock
   work but easy to ship alongside it.
2. **Cap Executor's output tokens** (item 7) — recommend yes, scoped
   strictly to `app/agents/executor.py`'s call (not `narrator.py`, not
   `keeper.run_turn`). Needs one number decided: how many tokens is enough
   margin for Executor's occasional short clarifying remarks without
   clipping a tool call's JSON mid-stream — recommend erring generous
   (e.g. 500, not the proposal's 300) until real data from item 6 shows
   Executor's actual token usage distribution.
3. **Prompt restructuring for caching** (item 8) — recommend **not yet**.
   Wait for item 6's real `cached_input_tokens` numbers before deciding
   there's even a problem to fix.
4. **Model tiering** (item 9) and **`reasoning_effort=none` for Executor**
   (item 10) — recommend treating as a follow-up mini-spec of its own
   (needs a real quality bar tested against the ~10 tools, not assumed),
   not part of this branch. Same backlog tier as macro tools.
5. **Streaming** (item 11) — recommend backlog. Only helps the Narrator's
   final text (not the tool-calling majority of turn latency) and needs its
   own edit-rate-limit-aware design before it's a real plan.

## Third batch: Gemini's code-level review (PDF, fed actual files + log excerpts)

The user had Google Gemini review this same discussion plus actual source
files (`discord_bot.py`, `locks.py`, `executor.py`, `config.py`) and log
excerpts. Two of its claims are concrete and checkable — checked both
directly against the installed library and the live log rather than taking
them at face value, same as the rest of this doc.

### Typing indicator: Gemini's `_typing_heartbeat()` is unnecessary — verified against the installed discord.py
Gemini's review says `channel.typing()`/`trigger_typing()` "呼叫一次只能維持約10秒", and since LLM calls run 15s+, it proposes a manual background
task that resends the typing signal in a loop (`_typing_heartbeat()`,
`asyncio.wait_for(stop_event.wait(), timeout=8.0)` etc.) wrapped around the
whole message handler.

**Checked directly against the installed `discord.py` 2.7.1**
(`python3 -c "import discord.context_managers, inspect;
print(inspect.getsource(discord.context_managers.Typing))"`) — its
`Typing.__aenter__` already spawns exactly this kind of background task
(`do_typing`: `while True: await asyncio.sleep(5); await
typing(channel.id)`) internally, cancelled cleanly in `__aexit__`. The
library's own docstring confirms it too: "allows you to send a typing
indicator to the destination for an indefinite period of time" when used
as `async with`. So Gemini's premise is simply wrong for the version this
project has installed — plain `async with message.channel.typing():
await _handle_message(message)` (this doc's original plan) already
re-sends the signal every 5 seconds for as long as the block is open, no
extra heartbeat wrapper needed. Not adopting that part.

### Queue-ack: Gemini's delayed-notice refinement is worth adopting
Gemini's `_acquire_lock_with_notice()` sketch differs from this doc's
original `_notify_if_queued` plan in one real way: instead of checking
`lock.locked()` once and immediately sending a notice if true, it starts a
background task that only actually sends the notice if the lock is *still*
unacquired after a short delay (its sketch uses 2.0s), cancelling that task
the moment the lock is acquired. This avoids sending a "queued" notice for
a wait that resolves in, say, 200ms — which the original plan would have
sent every time regardless of how short the wait turned out to be.
**Adopting this refinement** into the implementation plan above (replacing
the plain `lock.locked()` check) — same intent, better-tuned to actual wait
length. Delay value: this doc will use 2.0s to match, unless the user wants
different.

### Prompt-caching / token numbers: the specific log evidence Gemini used is misattributed
Gemini's deep-dive cites concrete numbers from the log: `tool_count: 34`,
`input_tokens: 24864`, `cached_input_tokens: 0`, `output_tokens: 1879`,
`duration_ms` ~16-18s, and attributes the 16-18s latency to prefill+decode
of a 24.8k-token/1879-output request in the main Executor/Keeper loop.

Checked both numbers directly:
- **`tool_count: 34` is accurate** — `len(app.keeper.TOOLS)` really is 34,
  confirmed by import. This part of the diagnosis (34 tool schemas on
  every Executor-path call) is real and matches this doc's item 6/7
  discussion above.
- **The `input_tokens`/`output_tokens`/`cached_input_tokens` numbers are
  not from the main conversation loop at all.** Traced the exact log lines
  by request_id: they're `llm.request.completed` events with `iteration:
  None` and `agent: None` — every genuine turn-loop request always carries
  an `iteration` number (0, 1, 2...; confirmed separately in this doc's
  second-batch section that `_create_response_async`, the real loop,
  never logs usage fields at all). These specific lines come from
  `app.providers.openai_provider._create_response` (the *synchronous*
  helper) being called from `app/keeper.py:3188` — the rolling
  conversation-history summarization path (`campaign_summary`, mentioned
  in `MAX_LOG_TURNS`'s config comment), not the Keeper/Executor/Narrator
  turn loop. A 24.8k-token input for *that* call makes complete sense (it's
  summarizing a large chunk of old conversation history) and says nothing
  about whether the main gameplay loop's `instructions` prefix is caching
  or not — same root issue as this doc's second batch already found (we
  aren't measuring the calls that would actually answer this question).
  The `16-18s` duration figure is real and does also occur in genuine
  turn-loop calls (confirmed: `llm.request.completed` durations across the
  whole log range up to 18205ms), but without usage logging on those calls
  we can't attribute it to token count the way Gemini did here — could
  just as easily be `reasoning_effort=medium`'s thinking-token overhead
  (which item 2 already flagged as a real, separately-confirmed cost).
  Doesn't change the recommendation already in this doc (add usage
  logging to the real loop first) — if anything, reinforces it.

### Model tiering code sketch: real correctness gap for a 3-provider codebase
Gemini's sketch adds `EXECUTOR_MODEL = os.environ.get("EXECUTOR_MODEL",
"gpt-4o-mini")` as the *default*. This project supports three providers
(`LLM_PROVIDER` = openai/anthropic/gemini — see `app/providers/`), and
`"gpt-4o-mini"` is an OpenAI-specific model id. Defaulting to it
unconditionally would silently break `EXECUTOR_MODEL` resolution the
moment `LLM_PROVIDER` is anything else (there's no equivalent-tier
Anthropic/Gemini model name to fall back to without provider-specific
defaults). Any real implementation of item 9 (model tiering) needs a
per-provider default, not a single hardcoded string — worth remembering
if/when that item comes off the backlog.

### Dynamic tool scoping (`get_scoped_tools(is_in_combat)`): reasonable, adds to the backlog item
New idea not in the first two batches: filter the 34-tool list down to a
combat/non-combat subset before sending it to the Executor, with a
whitelist check in the tool executor to gracefully reject a stale/
out-of-scope tool call instead of raising. This is a real, well-scoped
mitigation for the confirmed 34-tool overhead (item 6/7) — narrower and
less risky than full model tiering since it doesn't change model/reasoning
behavior, just trims what's offered. Worth folding into the same
"Executor-path token/latency" follow-up mini-spec as items 9/10 rather
than doing ad hoc, since getting the combat/non-combat tool split exactly
right (not hiding something the Keeper legitimately needs mid-scene) needs
the same care as the rest of that follow-up.

## Real-API verification results (items 8, 9, 10)

Two throwaway scripts (not committed, not part of app code — kept in the
session scratchpad), run against the real OpenAI API from `line-coc-keeper-
main-v2` (real `.env`, real live campaign state for an authentic prompt).

### Prompt caching (item 8) — works, confirmed with the real prompt

`verify_prompt_caching.py` built `instructions` the exact way `app/agents/
executor.py` does (`prompt_config.build_executor_static_prompt(keeper.
_build_static_prompt(state))` + dynamic block) from the real live
`discord-channel-1550744273060765719` campaign state, then fired 3
consecutive Responses API calls with that identical prefix:

| Call | input_tokens | cached_input_tokens | duration |
|---|---|---|---|
| 1 (cold) | 17469 | 0 | 5087ms |
| 2 (same prefix) | 17470 | **17448** (99.9%) | 2901ms |
| 3 (same prefix, +2s) | 17468 | **17448** (99.9%) | 3437ms |

**Conclusion: caching already works.** Both external proposals' claim that
it was broken was built on misattributed log evidence (see the second/third
batch sections above) — this settles it directly instead of by inference.
No prompt restructuring needed. Item 6 (adding usage logging to the real
loop) is still worth doing for ongoing visibility, but it's no longer
gating a decision — this already answers the question it was meant to
answer.

### Executor model/reasoning tiering (items 9, 10) — real, mixed results

`verify_executor_tiering.py` sent an identical two-skill-check prompt (with
the real 34-tool schema) across model/effort combinations, twice (two
separate runs):

| Config | Run 1 | Run 2 | Correctness |
|---|---|---|---|
| `gpt-5.6-luna` / `medium` (current) | 3362ms | 4191ms | ✅ both — correct `skill_check` × 2 |
| `gpt-6-luna` / `none` | 2510ms | 1999ms | ✅ both — correct `skill_check` × 2 |
| `gpt-6-luna` / `low` | 1549ms | 2327ms | ⚠️ **1 of 2** — run 1 called `roll_dice` directly instead of `skill_check`, skipping the actual mechanic entirely; run 2 was correct |
| `gpt-4o-mini` / `none` | — | — | ❌ **HTTP 400**: `"Unsupported parameter: 'reasoning.effort' is not supported with this model."` — confirmed non-reasoning models reject the param outright, same class of issue as `temperature` already being rejected for reasoning models (existing `_unsupported_params` fallback in `openai_provider.py` already handles exactly this generically — `("temperature", "reasoning")` are both checked — so a real integration would self-heal this automatically, unlike this standalone script) |
| `gpt-4o-mini` / no reasoning param | 5084ms | — | ✅ correct, but **slowest of everything tested** — directly contradicts the proposal's "幾乎是即發即回，Sub-second 體驗" claim against our actual 34-tool schema |

**Conclusions:**
- `gpt-6-luna`/`none` is the most promising candidate seen so far — faster
  than the current baseline in both runs, correct both times. Still only 2
  data points; would want more trials across a wider variety of scenarios
  (multi-NPC combat, index lookups, SAN checks) before trusting it in
  production.
- `gpt-6-luna`/`low` is **not reliable** — one wrong-tool-selection out of
  two trials is a real correctness failure, not noise to wave away. This
  concretely confirms this doc's earlier caution ("needs a real quality bar
  tested against the ~10 tools, not assumed") rather than just a
  theoretical concern.
- `gpt-4o-mini` (Gemini's specific suggestion) does **not** show the clear
  win it was pitched as — it was the slowest option tested here, on our
  actual tool schema. Not recommending it as the default candidate for
  item 9 based on this data; `gpt-6-luna`/`none` looks like the better
  direction if model/effort tiering is pursued further.

**Follow-up: is `gpt-4o-mini`'s slowness specific to the 34-tool schema?**
Ran it 3× more at 34 tools and 3× more at a 4-tool scoped-down subset
(`skill_check`, `roll_dice`, `search_scenario`, `get_character_sheet`,
`adjust_character`) to check both whether the first 5084ms result was
representative and whether the "dynamic tool scoping" idea (item 12) would
actually help this model specifically:

| Config | Runs | Avg | Correctness |
|---|---|---|---|
| `gpt-4o-mini` / 34 tools | 3847ms, 6450ms, 4126ms | **4808ms** | ✅ 3/3 |
| `gpt-4o-mini` / 4 tools (scoped) | 3144ms, 2429ms, 2741ms | **2771ms** | ✅ 3/3 |

Confirms the first result wasn't a fluke — `gpt-4o-mini` really does
average ~4.8s against the real 34-tool schema (consistent with the earlier
single 5084ms data point), meaningfully worse than `gpt-6-luna`/`none`'s
~2-2.5s in the same condition. Tool scoping (item 12) does help this model
specifically — cuts its average by ~42% — which is a genuine, separately-
useful confirmation of item 12 regardless of which model ends up in the
Executor role. But even scoped down to 4 tools, `gpt-4o-mini` (2771ms avg)
still isn't clearly faster than `gpt-6-luna`/`none` was at the full 34-tool
count (1999-2510ms) — so tool scoping alone doesn't make `gpt-4o-mini` the
better pick either. Tool selection was correct in all 6 of these runs
(unlike `gpt-6-luna`/`low`'s one miss), which is a mark in `gpt-4o-mini`'s
favor on correctness specifically, even though its raw speed doesn't beat
`gpt-6-luna`/`none` here.
- None of this is enough data to greenlight shipping tiering yet — still
  recommending this stay a follow-up mini-spec (item 9/10's original
  status), just now with real numbers instead of assumptions to start from.

## Notes
- `parallel_tool_calls` verification is explicitly out-of-band — a
  throwaway script against the real API, not app code in this branch.
