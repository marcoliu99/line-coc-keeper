# Turn latency: pending buttons and scenario search

**Status (2026-09-26):** The pending-button and search-count scope below was
implemented with tests and pushed in `404c417`. The Chinese scenario-template
and model-round-trip designs at the end of this document are proposals for a
separate review. They have not been implemented.

## Problem and goal

Three Discord runtime logs from 2026-09-25 show two avoidable sources of
player-visible delay. In the `high` reasoning run, the median completed router
span was 40.25 seconds (95th percentile 99.81 seconds). Requests that created
checks sometimes delivered the text reply and then waited for the conversation
lock again before posting the check or Luck button. Among 95 completed router
requests, 18 spent more than 10 seconds after the router completed; the longest
spent 200.56 seconds, almost entirely in subsequent lock waits. In one of
those turns, Executor made three successive `search_scenario` calls about the
same basement stairs; each retrieval took about 0.24 seconds, while the model
round trips between them took several seconds each.

The logs are:

- `/Users/marcoliu/coc_v2_log/20260925-141719-883841_runtime_profile-async.log`
  (`medium`, 89 completed router requests, median 18.54 seconds);
- `/Users/marcoliu/coc_v2_log/20260925-150958-612104_runtime_profile-async.log`
  (`high`, 95 completed router requests, median 40.25 seconds);
- `/Users/marcoliu/coc_v2_log/20260925-151704-122091_runtime_profile-async.log`
  (`max`, only five completed router requests, including a Narrator timeout
  and retry lasting 112.12 seconds).

The paired pyinstrument HTML reports spend over 99% of process wall time in
the event-loop selector. They profile the whole bot lifetime, including idle
time, so request spans in the structured logs are the latency evidence. SQLite
loads and saves usually take milliseconds; median scenario retrieval is about
0.25 seconds. `request.completed` includes work after the text reply, so the
router span and the later button-posting tail must be measured separately.

The goal for this change is to deliver a newly created check/Luck button
promptly after its turn's narration, while preserving ordered game state. A
small trial did not validate the proposed scenario-search change, so search
behavior remains unchanged in this spec. Its latency and relevance findings
are recorded below for a separate design.

## Small-scale verification (2026-09-26)

These were isolated trials using a copy of the scenario database. The model
trials used the configured OpenAI model (`gpt-6-luna`, `medium` reasoning) and
real scenario retrieval, but only `search_scenario` and a stubbed
`skill_check`; no game state was changed. One warm-up call per A/B trial was
excluded. Each arm had three scored runs, too few to estimate production
latency or judge a complete Keeper turn. Results are saved under
`/private/tmp/coc_*_small_trial_results.json` for this review.

| Trial | Current path | Candidate | What it establishes |
| --- | --- | --- | --- |
| Mocked conversation-lock contention; next turn holds lock for 250 ms, n=3 each | `_post_check_buttons` median 252.11 ms | Previously claimed direct send median 0.05 ms | The second lock acquisition creates the expected wait. Discord network time and full claim integration were mocked. |
| Real-model search flow; n=3 each | Median 11.96 s; median 5 API calls; 10 search rounds total; 1 `skill_check` call total | Batch first search and cap at two rounds: median 13.78 s; median 5 API calls; 6 search rounds total; 5 `skill_check` calls total | Fewer tool rounds did not reduce model calls or elapsed time. Expected one pending check per run was not reliable; candidate check counts were 0, 2, and 3. |
| Real-model proactive RAG, with one fixed DEX check; n=3 each | Raw Chinese-query context: Executor median 6.32 s, 3 API calls, 1 explicit search per run, 3/3 one check | English-aligned context: Executor median 4.83 s, 2 API calls, 0 explicit searches, 3/3 one check | Better initial evidence can save an Executor search round in this narrow setup. These Executor times exclude query rewrite and retrieval. |
| Chinese query against scenario text; n=3 each | Original English scenario: RAG plus Executor median 7.26 s, 3 API calls, 1 explicit search per run, 3/3 one check | Page 10 translated into Chinese, other 26 pages unchanged: median 4.22 s, 2 API calls, 0 explicit searches, 3/3 one check | The translated basement rule entered the top five RAG results in all three runs. This is a one-page, one-action trial, not a full translated scenario or full Discord turn. |

The raw Chinese action `我也走下地下室` returned five chunks, none containing
the basement stairs, Push, or fall terms needed for this ruling. Adding the
previous Chinese narration still missed them. Three model-generated English
search phrases found those terms in the top five chunks in all three runs;
rewrite median was 3.35 seconds and retrieval median 0.24 seconds. Adding
those median components to the 4.83-second aligned Executor median gives an
illustrative 8.42 seconds versus 6.32 seconds for raw context. This is an
unpaired, tiny-sample estimate, not a measured end-to-end A/B difference.
None of the three English results included the `1d6` damage term, so English
alignment alone does not prove complete scenario coverage.

The Chinese-page trial kept the same Chinese action and model prompt in both
arms. The original English scenario's proactive top five never contained all
four target markers (stairs, Push, fall, `1D6`); the Chinese-localized page
was ranked first and supplied all four in all three runs. The warmed RAG
search median was 0.03 seconds in both arms. The translated scenario index
took 2.33 seconds to build with embeddings once; that build cost is excluded
from the steady-state turn figures. The translation was a manually cleaned
rendering of the basement page, so an additional OCR-cleanup control replaced
that page with an equivalently cleaned English rendering. With the same
Chinese query, its top five still omitted the basement page and all four
markers. Retrieval results were deterministic on the repeated identical
query; the three model turns per arm are the latency samples. The fixed DEX
instruction and stubbed check test tool-loop cost, not spontaneous rules
selection, final narration, or Discord delivery. Results are recorded in
`/private/tmp/coc_chinese_scenario_small_trial_results.json`; the temporary
trial script and cleaned-English control are also under `/private/tmp`.

**Decision from the trial:** keep the button fix. Do not add the two-round
search cap, batch search schema, or a per-turn model rewrite as a latency
optimization now. The batch/cap trial did not pass its latency or one-check
gate; the rewrite's extra call can outweigh the saved Executor call. A
separate search design needs an outcome-aware replay that checks retrieval
evidence, tool calls, final ruling, and complete turn latency.
One-time translation or a bilingual scene index now has a concrete
single-scene signal worth testing separately; it is not part of this button
latency implementation. Translation accuracy, term consistency, index build
cost, and performance across other scenes remain open.

## Implemented scope

1. Claim new pending check/Luck button intents before the current request
   releases its already-held conversation lock, then send the Discord message
   immediately after release without joining the conversation-lock queue a
   second time. Cover ordinary text, `/coc check`, `/coc luck`, and both
   existing button callbacks. Keep a fallback for routes without one outer
   conversation lock and for partial failures.
2. Record the latency from the end of turn processing to button send start
   and completion. Record a turn-level count of Executor scenario-search
   invocations using existing structured logging conventions, without query
   or scenario text. This makes later search experiments measurable without
   changing the search policy in this change.

## Non-goals for the implemented scope

- No concurrent Keeper turns in one conversation and no removal of the outer
  conversation lock. Turn order, tool dependencies, and narration history
  remain serialized.
- No model or reasoning-effort tiering in code. The logs favor the existing
  `medium` default, but the `medium`/`high` traffic was not a controlled A/B
  run and the `max` sample is too small for a quality decision. The earlier
  `none`/`low` tiering experiment was reverted after production regressions.
- No global increase of API concurrency, shorter timeout, or reduced retry
  budget based on these logs. The admission semaphore showed negligible
  waiting; 429s and the one timeout remain separate operational concerns.
- No new scenario index, database table, state schema migration, or change to
  the scenario's access-control/spoiler policy.
- No general batching or parallel execution of state-mutating tools.
- No search batch schema, hard search-round cap, or per-turn LLM rewrite of
  player queries. The small trial did not show a safe latency win.

## Data structures and contracts

`GroupState.pending_checks` and `pending_luck_decisions` keep their existing
dict entries and `_buttons_posted` marker. The marker remains the durable
cross-request claim, excluded from legacy button identity hashes. A private,
turn-local `PendingButtonIntent` carries the kind, owner, copied entry,
timeline/identity, and optional public marker from the successful claim to
the Discord send. It is never written to the story log or database. Claims
for one completed turn should be collected and saved in one short state
transaction while the caller already owns the conversation lock.

No new Executor search state or tool schema is needed. The existing
`search_scenario` interface and read-only result contract remain intact. A
per-turn integer search count is sufficient for the new aggregate event.

## Pending-button flow

1. The Discord entry point captures its existing pre-turn snapshots. The
   router or button callback runs the turn under its existing conversation
   lock. A small completion hook runs in `finally` **inside** that lock,
   after any state mutation or reply attempt, and claims currently pending
   entries that are new or changed and not already marked `_buttons_posted`.
   It does not await Discord I/O. The hook must be wired to the lock-owning
   branches; it must not reacquire the non-reentrant lock. A failed claim is
   logged and left for the outer recovery pass rather than masking the
   original turn exception.
2. The handler releases the conversation lock and sends the claimed intents
   directly. A queued next turn may begin concurrently, but the button send
   no longer queues behind that turn's slow LLM call. Current identity checks
   still reject a button whose underlying decision was resolved or replaced
   before the player clicks it.
3. On send failure or cancellation, release only this intent's claim, using
   the existing identity comparison and claim-release helper. Cleanup must
   survive task cancellation. The outer `finally` recovery pass handles
   unclaimed entries and routes without the completion hook; it must skip
   already claimed intents, so it cannot duplicate posts or add a fresh
   lock wait to the normal path. A process crash between claim and send is
   an existing limitation; the implementation must not enlarge that window
   with other awaited work between the claim and send.
4. Preserve the same view labels, custom IDs, owner checks, and direct-send
   behavior for skill, choice, and Luck buttons. Keep a fresh state read
   between check and Luck recovery passes where a Discord await can allow
   another request to change the latter, as the current code requires.

This is a change to *when* the durable claim is made, not permission to run
two game turns at once or to send buttons while holding a network operation
inside the conversation lock.

## Executor search follow-up

`context_builder.build_context` and Executor continue to use the existing
proactive context and `search_scenario` tool. The real-model trials exposed a
retrieval-language mismatch in the basement-stairs example, but the tested
batch/cap candidate did not make the final check sequence more reliable or
faster. The earlier RAG-reuse spec's “no hard per-turn search cap” decision
therefore remains in force. Prompt wording alone is also insufficient: the
existing reuse prompt was present during the logged three-search incident.

The search count event should distinguish turns with zero, one, and multiple
explicit searches. A later search spec can use these counts and controlled
replays to evaluate one-time scenario translation, bilingual indexing, or
another no-extra-model-call alignment method. It must check that necessary
evidence and the final ruling remain correct before using a search-round
limit.

## Integration and observability

- Extend the current `app/discord_bot.py` and `app/commands/router.py`
  handoff rather than replacing locks or creating a second independent
  posting path. Reuse `_buttons_posted`, button identity helpers, and the
  stranded-claim release logic from the duplicate-button fix.
- Count explicit `search_scenario` calls at the existing Executor tool
  execution boundary. Do not change `app/agents/tool_gateway.py`,
  `app/keeper.py`, `scenario_rag.search`, or result formatting for this spec.
- Add structured events for `pending_button.claimed`,
  `pending_button.send.completed/failed`, and one
  `executor.scenario_search.summary` per turn. Record elapsed time, kind,
  count, and status as applicable; do not add player text, query text, page
  content, owner IDs, or Discord message bodies to structured events.
  Existing `LOG_TEXT_ENABLED` query logging remains separate.
- Measure final narration delivery and button delivery separately. A lower
  `request.completed` duration alone is not evidence that players got their
  buttons sooner.

## Verification plan

1. Async contention regression: queue a second slow turn before the first
   releases the conversation lock. Assert the first turn's new check and Luck
   button sends start without waiting for that second LLM turn, while game
   state processing stays serialized. Cover ordinary text, `/coc check`,
   `/coc luck`, and both button callbacks.
2. Preserve one post per decision across overlapping requests, a replacement
   decision for the same owner, legacy IDs, choice buttons, send failure,
   cancellation, and exception paths that saved a pending entry before the
   later turn failed. Verify no lock leak or stranded `_buttons_posted` flag.
3. Search instrumentation tests: assert the per-turn event reports zero,
   one, and multiple explicit `search_scenario` calls correctly, including
   tool errors, without exposing query or scenario text. Verify that the
   existing tool result and access-control behavior stays unchanged.
4. Run relevant Discord/router, Executor/RAG, provider, and state tests,
   then the full project suite and existing static checks. In a subsequent
   live log, compare button-send tail against the baseline above; report
   sample sizes and queue load. Use the search count to size a separate
   outcome-aware search experiment, not to claim a speedup from this change.

The implementation in `404c417` passed `python3 -m pytest -q` and Ruff on the
changed Python files on 2026-09-26. Live button-delivery timing after rollout
is still to be measured.

## Review decisions and tradeoffs

- **Search decision:** retain the current Executor search policy. The
  batch/two-round candidate reduced explicit searches but showed no speedup
  and produced missing or duplicate `skill_check` calls in the isolated
  replay. Translating the relevant scenario page once improved retrieval and
  reduced median isolated turn time by 3.04 seconds in the basement trial.
  It still needs broader correctness and latency testing before changing the
  scenario pipeline. A per-turn model rewrite was slower in the narrow
  one-search comparison once its estimated cost was included.
- **Button ordering:** send outside the conversation lock to avoid holding
  game state serialization during Discord network I/O. The claim is made
  before release; a button may become stale before delivery, as it can
  today, and identity checks remain authoritative at click time.
- **Crash recovery:** the existing claim-then-send crash gap remains. A
  durable outbox/lease would solve it but adds a schema and delivery worker;
  this latency change keeps the current failure rollback and does not add a
  persistent queue.

## Follow-up evidence from the same logs (proposal only)

The following counts use completed turn IDs and per-request structured events,
not pyinstrument's process-wide idle time. They describe different traffic in
the `medium` and `high` runs, so differences between runs are not causal A/B
estimates. The `max` run has only four completed turn IDs and is too small to
guide a new optimization.

| Measure | `medium` log | `high` log | Interpretation |
| --- | ---: | ---: | --- |
| Completed turn IDs | 86 | 91 | Includes more than ordinary text requests. |
| Executor spans | 69; median 10.22 s | 77; median 14.66 s | Executor often dominates the mechanics part of a turn. |
| Narrator spans | 69; median 5.90 s | 76; median 8.51 s | Usually one separate model request after Executor. |
| Executor turns with no tool execution | 30/69; median 5.49 s | 27/77; median 5.55 s | An upper bound on turns that *might* qualify for a safer Narrator-only route; the logs do not say what the player asked. |
| Executor turns with a tool and a final text-only API request | 39/69; final request median 4.84 s | 50/77; final request median 4.05 s | Executor's returned text is discarded by the Supervisor path; not every final request is safe to omit. |
| Final tool was `skill_check` or `offer_npc_attack_defense_choice` | 12/69; final API median 3.30 s | 27/77; final API median 4.07 s | Candidate upper bound for a pending-check terminal stop. Logs do not record whether each result was actually `pending=true`. |
| Explicit scenario searches per turn | 0:49, 1:23, 2:7, 3+:7 | 0:50, 1:20, 2:14, 3+:7 | A search-round limit would affect a small tail and could miss needed evidence. |

The OpenAI tool-calling loop in `app/providers/openai_provider.py` always sends
the tool output back for another Responses request. In the Supervisor path,
`app/agents/executor.py` uses tool side effects and facts while discarding the
returned text; `app/agents/supervisor.py` then calls Narrator. A last
`skill_check` call is only a *candidate* saving: an automatic roll, failed
registration, multiple player actions, or another required tool would make an
early stop wrong. The observed 3.30/4.07-second medians are durations of
those final requests, not measured end-to-end savings.

The per-request log reported 35-36 available tools in nearly all tool-bearing
OpenAI calls (median 36). It cannot yet quantify their token cost: only one
of 265 `medium` and one of 319 `high` completed async LLM request events
recorded `input_tokens`. The sync OpenAI helper records usage, but
`_create_response_async` omits it. Tool-schema reduction therefore needs
token instrumentation and correctness review before any latency claim.

### Candidate A: stop Executor after a terminal pending check

Propose a narrowly scoped provider-loop stop signal from Executor's tool
callback. It may stop after a *successful* `skill_check`, `sanity_check`,
`offer_check_choice`, or `offer_npc_attack_defense_choice` result with
`pending=true`, and only when the model response contained a single tool call
and the turn has no known remaining action. Preserve the tool result, facts,
state mutation, response ID handling, and subsequent Narrator call. The stop
must never fire for a failed/resolved check, a read-only search, a tool result
requiring correction, legacy Keeper calls whose returned text is visible, or
multi-tool responses. Define the remaining-action guard with labeled replay
cases before implementation; if it cannot be made reliable, leave the loop
unchanged. No general iteration cap is proposed.

Replay real anonymized single-action and multi-action turns through all
supported providers. Assert identical pending state, tool sequence, facts,
Narrator policy, button delivery, and player-visible ruling. Then compare
model requests and complete turn p50/p95 with a controlled A/B. The count of
eligible turns and saved time must be measured from tool **results**, since
tool names in the current logs cannot establish eligibility.

### Candidate B: broaden the pure-roleplay fast path cautiously

`app/agents/intent_router.py` currently skips Executor for a small exact
acknowledgment set, empty text, and parenthesized OOC. Review a labeled sample
of the 30 and 27 no-tool Executor turns to find actual narration-only inputs.
Only a high-precision rule for those inputs may route directly to Narrator;
ambiguous wording stays on the current Executor path. A retrospective no-tool
result alone is not a routing rule: an action can require scenario lookup or a
state change even when Executor failed to call a tool. Evaluate false skips
against checks, combat, scenario-dependent questions, mixed roleplay/actions,
and pending decisions before measuring API calls and end-to-end latency.

### Candidate C: measure prompt cost before changing tool exposure

Add async OpenAI usage fields (input, cached input where available, output,
and reasoning tokens) to the existing `llm.request` span without logging
prompt or scenario content. Compare token counts, cache rates, request time,
and correctness by agent and tool count. A later tool-visibility or schema
change must retain the tools required for the turn's role and state, including
scenario search, check registration, and recovery. Do not assume that fewer
tools imply fewer seconds; the earlier `none`/`low` model-tiering experiment
was reverted after production regressions. A controlled `medium`/`high`
configuration A/B can be considered separately with a ruling-quality gate.

## Chinese scenario template proposal (separate review and implementation)

### Goal and decision boundary

The one-page trial shows that a Chinese rendering of the relevant basement
rule can make a Chinese query retrieve the needed scene without an extra
Executor search round. It does not validate a whole scenario or final ruling
correctness. The requested direction is to preprocess the scenario into a
consistent, structured Chinese scenario template, then use that Chinese text
as the source indexed by RAG. `SCENARIO_RAG_ENABLED` is already enabled in the
user's normal workflow; this design assumes retrieval mode and does not
propose changing that setting. Do not translate player queries on each turn.

### Data and build flow

1. Extract the original PDF/OCR into page-preserving source blocks. Keep the
   original PDF and extracted source text as the audit source. Translate and
   normalize the whole playable scenario once during preprocessing, before
   RAG indexes it. Preserve page numbers, chapter IDs, image references, and
   the existing chapter access window so a translated template cannot expose
   later or KP-only material early.
2. Use a fixed Chinese template for every scene, rule, NPC, clue, and handout.
   Each retrievable unit has: canonical name and aliases/keywords; unit type;
   player-visible description; KP-only information when present; trigger and
   conditions; required skill or characteristic; target/dice expression;
   success, failure, Push, and consequence rules when present; exceptions and
   cross-references; and original page/section references. Keep narrative
   prose as a faithful translation in its own field. Put normalized rule
   summaries in a separate field so a summary cannot silently replace or
   expand the source rule.
3. Apply a per-scenario glossary for names, skills, places, recurring terms,
   and common Chinese aliases. Preserve original names on first mention and
   keep the same Chinese rendering throughout. Preserve numbers, units,
   dice, thresholds, negation, uncertainty, and conditional wording exactly;
   do not fill gaps with COC conventions or model guesses. Mark unclear source
   text as needing KP review instead of inventing a translation.
4. Make each template unit self-contained for retrieval. When a long unit
   must be split to fit RAG chunks, repeat its canonical scene/name and
   keywords in each part, retain the source page/section reference, and keep
   dependent trigger/result conditions together. This matters because the
   current index chunks by page and paragraph at roughly 400 characters.
5. Save the generated Chinese template as a versioned derived scenario text
   while retaining the original PDF and source-to-template mapping (source
   hash, page, section, unit ID, and template version). Keep it as a distinct
   library artifact; do not overwrite the original `scenario.txt` or PDF.
   Extend the library's atomic save/replace flow so reparsing the original
   cannot silently delete the translated artifact. On source, chapter-window,
   glossary, template, or translation-version changes, regenerate the derived
   text and its RAG index. Reuse the existing CJK bigram/BM25 plus embedding
   index; add no per-turn translation call.
6. At activation, make RAG search the Chinese template for both proactive
   context and explicit `search_scenario` calls. Return the matching Chinese
   template unit with its source page reference. Keep review status visible
   to the KP; units with unresolved translation issues must not be treated
   as verified mechanical rulings. If preprocessing or index building fails,
   keep the original scenario usable and report that the Chinese version is
   not ready.

Example template unit (omit fields the source does not contain; write
「原文未提及」 only when that absence matters to a ruling):

```text
--- 第 10 頁 ---
## 場景單元：地下室階梯
類型：場景／判定規則
標準名稱：地下室階梯
別名與檢索詞：地下室、樓梯、跌落、推進（Push）
玩家可見描述：忠實翻譯原文敘述。
KP 秘密資訊：忠實翻譯，並沿用原章節可見範圍。
觸發條件：玩家做出什麼行動時適用。
判定：技能或特徵；難度；骰式／目標值。
成功：原文寫明的結果。
失敗：原文寫明的結果。
推進：原文寫明的推進規則；沒有就省略。
例外與後續：保留原文條件及跨頁參照。
來源：原文第 10 頁，對應段落識別碼。
校對狀態：待 KP 校對／已校對。
```

Do not infer that 「推進（Push）」 is allowed just because it is a common
Call of Cthulhu mechanic; include it only if the source says so.

### Template record types and source links

The supplied Corbitt Markdown outline is the reference case for the template.
Use record types that match the material instead of putting the whole module
into one long overview block:

- `overview`: era, region, patron, assignment, payment, campaign-level secret,
  and play phases;
- `investigation_location`: location, NPCs, available approaches, checks,
  consequences, and clue/Handout IDs;
- `handout_or_clue`: what it reveals, who can provide it, its source, and
  links to the next relevant record;
- `room_or_scene`: hierarchical location, description, events, threats, and
  exits or connections;
- `check_rule`: trigger, eligible skills/characteristics, difficulty or
  opposed value, success/failure, Push, and Push-failure consequence;
- `npc_or_encounter`: identity, role, stats, abilities, attacks, defenses,
  damage, Sanity effects, and defeat conditions;
- `ending_or_hook`: required outcome, reward or consequence, and follow-up
  leads.

Give every record a stable ID and parent ID. For example, distinguish
Corbitt House ground-floor Room 1 from Basement Room 1 with IDs such as
`corbitt-house-ground-room-01` and `corbitt-house-basement-room-01`; never
rely on the repeated label `Room 1` alone. Preserve source links as Markdown
heading paths/anchors, plus PDF page and paragraph when available. Handouts,
checks, rooms, and NPCs should link by ID so a retrieved clue can point to a
related rule without copying or conflating their contents. Preserve chapter
and public/KP visibility metadata on every record.

The RAG index must treat each template record as a chunk boundary. If one
record exceeds the current roughly 400-character chunk target, split it into
numbered parts with the record ID, canonical name, aliases, source link, and
visibility repeated in each part. Keep conditional mechanics together when
possible; if they must span parts, include the relevant trigger with the
outcome. This may require making the index builder accept explicit template
units instead of relying only on page/paragraph splitting.

Example record based on the supplied basement-stairs section (the template
does not assert that this transcription has been verified against the source):

```text
record_id: corbitt-house-basement-stairs
parent_id: corbitt-house-basement
type: check_rule
canonical_name: 地下室階梯陷阱
aliases: 地下室樓梯、階梯晃動、下樓跌落
source: Markdown heading「決戰階段 > 地下室 > 階梯陷阱」；PDF 頁碼（若有）
visibility: 依來源章節與 KP 可見規則
trigger: 調查員逐一下樓時
check: DEX 或 Climb 複合檢定，任一合格即成功
success: 安全下樓
failure: 可退回或推進檢定
push_failure: 墜落至地下室，承受 1D6 傷害
assistance: 通過者可協助並給獎勵骰；失敗可能使兩人一同墜落
校對狀態: 待 KP 校對
```

The source visibility should determine `visibility`; the example's value is
illustrative and must not be copied without checking the source's spoiler
policy.

The first usable template can be authored and proofread outside the bot, then
exported as a page-preserving Chinese PDF for the existing import flow. That
is the smallest end-to-end trial and requires no runtime translation feature.
If it improves retrieval and ruling quality, automate the same template
contract in a later implementation. The small basement trial passed
translated text to Executor; it did not test a structured template or
source-to-template audit mapping, so those need separate validation.

### Acceptance and rollout gate

- Prepare template-based Chinese content and labeled Chinese actions across
  several pages and scene types, including irrelevant queries, similarly
  named rooms on different floors, Handout-to-location cross-references,
  negative/conditional rules, and restricted future chapters. Check source
  page and chunk recall at five, ranking, explicit search counts, API calls,
  complete Discord turn p50/p95, and final ruling accuracy. The basement
  example remains one case, not the whole acceptance set.
- Have a KP compare template units with the original for dice, damage, skill
  thresholds, proper names, negation, Push rules, and player/KP visibility.
  Track corrections and terminology consistency across pages. Verify
  source-hash/version invalidation, restart, partial-build fallback, and no
  chapter or group leakage.
- Compare one-time translation and embedding cost against repeated-turn
  savings. Keep automation out of the runtime path until multi-scene replay
  preserves rulings and reduces complete turn latency without a per-turn
  translation request. Record preprocessing, retrieval, and model time
  separately.

## Follow-up scope and review decisions

The current branch's runtime implementation remains the pending-button change
and search-count event in `404c417`. Candidate A, B, C, and the Chinese
scenario template above are **spec-only**. Proposed order: first improve async
usage and result-level observability, then evaluate the manually prepared
Chinese template and terminal-check stop independently; investigate roleplay
routing after labeling actual turns. These are separate correctness gates,
not a bundled code change.

Review choices before a new implementation: approve the template fields and
terminology rules; decide whether the first trial uses a manually prepared
Chinese PDF/text or bot-generated output with KP review; and determine which
template units require original-source text alongside the translated result
at runtime. Pending-check terminal detection also remains gated on proving
that no further action is owed.
