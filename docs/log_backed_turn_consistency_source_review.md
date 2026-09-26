# 回合一致性：source review 與修正流程

日期：2026-09-26。程式基底：main_v2 `95d8ca3`。
第 1–8 節保留實作前程式審查／設計；修正已實作，結果見第 9 節。搭配
[修正規格](log_backed_turn_consistency_design_spec.md) 閱讀。

## 1. 結論

能修正幾個確定的資料流缺口，且不需要固定新增一次 LLM 審稿。
不能保證修完後所有模型選擇都正確；過窄的舊 benchmark assertions 也必須修正。
關鍵不是沒有存 state，而是 state 在不對的時間才給到需要它的 agent，
加上 Executor 的裁決沒有交接、歷史快照與當前狀態混在一起。

## 2. 目前入口到回覆的完整路徑

```text
Discord ordinary text / existing command entry
  |
  +-- conversation lock -> reload latest GroupState
  |     router._handle_ordinary_text_message_locked
  |     -> active-character resolution / map action
  |     -> keeper turn lock
  |
  v
supervisor.run_turn
  +-- ensure timeline
  +-- context_builder.build_context
  |     +-- actual state + active character
  |     +-- scenario RAG + memory RAG (existing gates retained)
  |     +-- recent resolved_check_events
  |
  +-- intent_router.classify_intent [Python, no LLM]
  |     +-- KP Assistant ------> assistant.run_assistant (separate path)
  |     +-- PURE_ROLEPLAY -----> Narrator directly
  |     `-- GAMEPLAY_ACTION --> Executor
  |
  +-- Executor.run_executor
  |     +-- static rules + dynamic prompt + RAG + state.log
  |     |     [A] no current pending_checks / pending_luck list
  |     |     [B] live inventory + older digest inventory mixed
  |     +-- provider.run_conversation (existing tool loop)
  |     |     +-- request -> retry / admission -> API
  |     |     +-- tool_gateway -> keeper._execute_tool
  |     |     |     -> state lock -> reload -> mutate -> save -> sync
  |     |     `-- model final text
  |     |           [C] discarded at Executor boundary
  |     `-- MechanicResult: tool facts + check_status
  |           [D] no tools => "no mechanic needed", regardless of reason
  |
  +-- Supervisor compares before/after pending and Luck
  |     -> select last changed pending, otherwise speaker's old pending
  |     [E] only now adds authority; normal pending loses action_context
  |
  +-- state_reducer.apply_mechanic_result [logging only; no double mutation]
  +-- Narrator [ordinary turns: no tools]
  |     -> refreshed live state + same digest source + mechanic facts
  +-- Guard [system leak / Markdown; optional existing repair calls]
  +-- spoiler sanitizer
  +-- enforce_mechanic_check_consistency [Python]
  |     [F] appends check instruction for any selected pending
  |     [G] incomplete language coverage if no pending
  +-- _commit_turn_result [reload, timeline check, append log, save]
  `-- public reply / existing button delivery / post-turn maintenance
```

### Button / check-result path (must remain consistent)

```text
check button or /coc check
  -> lock + load latest state
  -> button: verify pending identity and timeline (reject stale button)
  -> deterministic check transaction
      -> pending consumed / choice processed
      -> dice result
      +-- waiting Luck -> existing Luck decision path
      `-- finalized -> resolved event + original action_context
  -> supervisor.run_turn(turn_kind=resolved_check_followup)
      -> same context entry
      -> skip ordinary Executor
      -> tool-enabled Narrator (restricted tools, no reroll)
      -> existing guards + enforce_resolved_check_consistency
      -> commit/reply
```

`app/legacy_commands.py` still contains deterministic command handlers; the
followup at line 1303 calls the new Supervisor, not an old Keeper agent.
Opening fallback similarly uses its explicit turn kind. This review does not restore a second player agent path.

## 3. Source findings and evidence

| Priority | Location | Finding | Impact / scope |
|---|---|---|---|
| P1 | app/keeper.py:3357; app/agents/executor.py:53 | Dynamic prompt has character/combat/digest but no authority list of pending/Luck | Executor cannot reliably inspect the prior check it is asked to correct |
| P1 | app/agents/executor.py:107,143; app/services/prompt_config.py:42 | Prompt promises an internal summary; provider final text is discarded | Deferred, blocked, no-check adjudication and omitted action can collapse to the same handoff |
| P1 | app/keeper.py:3373; app/scene_digest.py:65,83,142 | Latest digest means latest *within timeline*, not equal to live revision; whole public/private dictionaries go into prompt | Older inventory/combat snapshots appear beside current values; remove history can be read as current absence |
| P1 | app/services/prompt_config.py:275 | No-pending guard does not catch “完成這次鬥毆攻擊檢定後，才能確定結果” | Player receives an impossible next step |
| P2 | app/agents/supervisor.py:114 | Normal pending whitelist omits action_context; Luck path includes it | Narrator knows skill name but lacks authoritative reason linking check to current clarification |
| P2 | app/agents/supervisor.py:92 | Only one last changed pending becomes primary status | Multiple actors are not fully represented in the mechanic handoff; do not extend actor-blind assertions |
| P2 | app/services/prompt_config.py:195; executor.py:143 | “success=True” means Executor completed, but prompt labels it simply success | Tool errors / incomplete action must not become an implied successful player action |

The source establishes these mechanisms, not that each alone caused every sampled model output.
Raw Executor final texts were not recorded in the original API benchmark, so their exact content cannot be recovered.

### 3.1 Old pending: exact causal chain and ambiguity

```text
state has prior check + action_context
  -> Executor only sees narrative history, not authority check list
  -> correction text may be treated as clarification without tools
  -> clear_pending_check never called
  -> state still has check
  -> Supervisor correctly reloads selected pending into check_status
  -> Narrator may accept correction in prose
  -> Python guard appends /coc check because pending is real
  -> contradictory reply; old button can still resolve old check
```

The removal handler itself works through `_mutate_and_save_state`, and never rolls dice.
The bug is not “pop failed”. Nor should the output layer silently remove state.
Importantly the benchmark’s “throw” check has a crafting action_context: player denying attack
is not sufficient evidence to cancel a valid crafting risk check. Separate explicit cancellation,
wrong skill correction, and valid existing check fixtures.

### 3.2 Attack followup: initiative must decide the next step

start_combat can make Ken current even when Marco initiated the message.
The source already instructs off-turn actors to wait (`keeper._build_dynamic_prompt`).
If no Marco pending exists, a valid result may be “wait for Ken”, not “create a Marco roll”.
Tool facts alone describe start/add; they do not encode why the declared attack was deferred.
Narrator then independently interprets the same player request. Merely adding skill_check
for every attack would violate initiative and harm defense/reaction exceptions.

### 3.3 Inventory: distinguish events from snapshots

A remove event records what happened at that time; it is not an eternal blacklist.
`add_carried_item` intentionally need not delete old events. A transfer, reacquisition or
splitting “two bottles” into remaining “one bottle” must leave a usable live inventory.
Fix the prompt projection: choose live mechanical fields; keep dated historical facts separately.
Do not use fuzzy text matching to erase quantities, merge items or manufacture missing supplies.

## 4. Proposed flow (same normal LLM stages)

```text
load current state
  |
  v
build authority context
  +-- current pending + Luck + action_context + owner/character/check identity
  +-- live inventory / HP / initiative
  +-- history/digest labeled with revision, no conflicting live-field copies
  `-- original RAG / scenario policy
  |
  v
Executor: existing provider conversation
  +-- real tools -> existing locked persistence -> actual results
  +-- relevant state refresh in tool results after mutation
  `-- existing completion response -> structured TurnResolution
        (use existing response, no extra completion request)
  |
  v
Python reconciliation
  +-- actual final state / tool events override claimed outcomes
  +-- cancelled? referenced pending really removed?
  +-- await_check? matching active pending really exists?
  +-- await_luck? dice rolled, Luck decision exists?
  +-- deferred? actual initiative / pending dependency supports it?
  +-- no-check result? cited scenario evidence, no fabricated state change
  `-- missing/invalid resolution -> incomplete; preserve applied effects
  |
  v
Narrator receives validated disposition + referenced actual state
  |
  v
existing guards + state-based allowed next action
  -> no nonexistent check instruction
  -> no claim of cancellation unless state agrees
  -> no completed attack narration for deferred action
  -> commit/reply using existing timeline protection
```

A structured result is evidence of the model’s decision, **not a new source of truth**.
Validation can check identities, existing state and actual tool operations; it cannot generally
prove a natural-language scenario inference correct. An `incomplete` response makes unresolved
work visible; it does not complete the action. Do not silently auto-retry mutating tools.

### Interface changes and order

1. **Authority projection first:** shared prompt helper for live pending/Luck/inventory,
   with referenced owner/action context. Preserve private information filtering.
2. **History projection:** remove duplicate live mechanical fields from digest input;
   show digest revision/time and keep canonical historical facts. No DB migration needed.
3. **Executor handoff:** add TurnResolution to MechanicResult; capture the existing returned
   completion; bounded JSON parsing and validation, never execute its text as a tool.
4. **Supervisor:** reconcile outcome per referenced actor/check, not a global boolean;
   include action_context in normal pending. Preserve other players’ pending and settled results.
5. **Narrator / reply guard:** distinguish waiting, deferred, no-check completion, cancellation
   and incomplete; guard next steps using authoritative status. Regex additions are a fallback,
   not a complete semantic validator.
6. **Provider contract:** all three providers already return final text. If iteration cap is
   reached with enable_wrapup=False, there may be no valid decision text: report incomplete,
   keep tool effects and do not enable an additional forced wrapup.

The JSON requirement itself may change model behavior or iteration count; “no fixed extra
stage” does not promise identical per-turn requests. Measure actual counts after implementation.

## 5. Existing branches and integration

- #67 (`bug/self-corrected-check-leaves-stale-pending`): merged; tool wording already strengthened.
  This adds authority context and correction outcome checks rather than redoing the same wording fix.
- #71 / #81: merged; retain final pending reconciliation and add missing cases.
- #79 / #87: merged; resolved followup and no-reroll behavior remain.
- #64: merged; keep retry budget, jitter, Retry-After and SDK retry disabled.
- #86 (`enhancement/narrative-boundaries-spec`): still open, **not in this baseline**.
  `/coc correct` is not currently available on baseline main_v2; do not pretend it exists.
  Its reports/approved corrections do not themselves mutate pending. Preserve future compatibility.
- #83 (`enhancement/reduce-turn-latency`): still open. A proposed stop-immediately-after-check
  optimization could remove the completion text needed for handoff; handle with deterministic
  disposition for such proven cases or retain the completion. Do not enable contradictory designs.
- #62: closed for invalid root-cause/verification assumptions; not a pending-action fix to reuse.

## 6. Verification done in this review

No real API calls. No production .env / DATA changes.

Existing relevant suite:
`python3 -m pytest -q tests/test_agentic_pipeline.py tests/test_narrator_check_consistency.py tests/test_resolved_check_events.py tests/test_tool_gateway_speaker_role.py`

**51 passed** on an isolated temporary DB/data path. Existing test success does not cover the new failures:
most mock Executor results or deliberately use ignored completion text; no assertion proves
pending authority reached the real Executor prompt.

Local probes (real prompt builder / validator / Executor, mocked provider and digest I/O):

| Probe | Current result |
|---|---|
| Clear pending and Luck; compare generated dynamic prompt | identical: missing authority input |
| Live inventory plus older digest carrying sentinel inventory | both reach prompt |
| Older digest has state_revision=10, live revision=20 | digest revision not exposed by current formatting |
| Missing pending + “完成這次鬥毆攻擊檢定後...” | guard returns it unchanged |
| Two different model decisions (defer vs incomplete cancellation), no tools | identical MechanicResult |
| Both no-tool results | same “這句話不需要任何機制判定” fact |

Probe script retained locally at `/private/tmp/coc-source-flow-probes.py`.
After approval, convert to meaningful repo regression tests with expected *corrected* behavior;
add real-tool integration tests for cancellation, initiative, inventory transfer and partial failures.

## 7. Retry / timing is a separate diagnosis

```text
provider request -> admission -> API attempt
  +-- success -> response
  +-- unsupported optional parameter -> specific parameter fallback
  +-- 429/transient -> release slot -> Retry-After or jitter -> retry within budget
  `-- permanent error / exhausted -> fail with actual error classification
```

234 recorded 429 retries account for 759 seconds of explicit sleep across both arms.
The latency confidence interval crossing zero is not a fault to patch. Request/token quota
headers and delay source were not captured in the benchmark, so account-wide rate-limit
cause remains unknown. Add safe diagnostic fields before choosing a new scheduler.
Do not increase retry budget or add extra LLM review to paper over game-state errors.

## 8. Completion criteria

- Inputs expose referenced live pending and provenance; no stale inventory authority conflict.
- Accepted cancellation changes the correct unresolved check, not another player's or finalized roll.
- Deferred attacks remain unexecuted; actual reactions and resolved-check followup remain valid.
- No-pending turns cannot invite use of a nonexistent check, including natural wording without a command.
- No-tool Executor output is distinguishable from a completed action, refusal or missing work.
- Existing wrapup suppression, sequential mutation and no-reroll behavior remain tested.
- API behavior improvements require a later bounded real-model check; unit tests alone cannot prove them.


## 9. Implementation verification (2026-09-26)

Implemented the approved flow; see design spec section 8 for the current diagram and interfaces.
The old probe results in section 6 describe the baseline, not the corrected runtime.

- Full isolated suite: **668 passed, 1 skipped, 15 subtests passed**.
- `python3 -m mypy app`: 69 source files passed.
- Ruff on modified runtime/test files and `git diff --check`: passed.
- Full-repository Ruff still has baseline `SIM114` at `app/pregen_extractor.py:759`;
  unrelated baseline code was not changed.
- Regression tests exercise actual SQLite persistence and Keeper tools; mocked provider completions
  verify exactly one existing Executor conversation, with wrapup disabled.
- Multi-player regressions reject using another actor's resolved roll as completion evidence and
  preserve the referenced pending check when another player has a Luck decision.
- No new real API benchmark and no production environment/data changes.

Provider JSON compliance and scenario interpretation remain live-model verification limits.
The validator verifies state and evidence provenance, not every natural-language rule inference.
