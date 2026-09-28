# Codex OAuth conversation provider

Status: implemented on the experiment branch; 50-case live evaluation completed (47/50 state/narrative passes).
Branch: `enhancement/codex-oauth-provider`. Base: current `origin/main_v2`.

## Goal and scope

Run an isolated CoC experiment in `workspace/coc_codex` using the installed
Codex CLI's ChatGPT login. Keep Python responsible for authorization, rules,
state, dice and tool execution. First prove `codex exec`, then add an app-server
transport behind the same protocol. Do not start the Discord bot automatically.
Do not change the existing deployment or migrate OCR/analysis to Codex in phase 1.
The copied `.env` is ignored by Git, mode 0600, with all game storage redirected
inside the new checkout. Existing API credentials remain available for analysis.

## Findings in main_v2

The current interface already accepts static/dynamic prompts, tools, history,
new_message, an async execute_tool callback, max_iterations and enable_wrapup.
Preserve it rather than replace every caller with a messages-only API.
Executor expects an existing resolution JSON string; Narrator expects prose.
Therefore final.content must pass through unchanged, including JSON strings.

Provider dictionaries are duplicated across Executor, Narrator, Guard,
Keeper/KP Assistant and analysis modules. Introduce a shared capability registry
with a typed conversation Protocol and a separate analysis selector. Audit
pregen_extractor, scenario_compare, scenario_index, scenario_intro, scene_map,
pdf_ai_repair, markitdown_shim and Keeper analysis, plus Discord warmup/shutdown.
Avoid silently returning no analysis when LLM_PROVIDER becomes codex.
Make Executor/Narrator dynamic tool gating capability-based, preserving the
combat-status gate currently wired only for OpenAI. Preserve provider contracts.

## Configuration

| Setting | Experiment value | Meaning |
| --- | --- | --- |
| LLM_PROVIDER | codex | Conversation backend |
| ANALYSIS_PROVIDER | copied original provider | Structured text and image analysis; defaults to existing LLM_PROVIDER for existing backends |
| CODEX_MODEL | gpt-6-luna | Requested model; availability requires live verification; no silent substitution |
| CODEX_TRANSPORT | exec | Later optional app-server |
| CODEX_TIMEOUT | 120 | Total conversation deadline, including admission, retries and tool waits |
| CODEX_MAX_CONCURRENCY | 2 | Event-loop-safe admission limit |
| CODEX_MAX_OUTPUT_BYTES | 1048576 | Bound captured transport output, including stderr |
| MAX_TOOL_ITERATIONS | 6 | Maximum decision requests; repair attempts consume this budget |
| MAX_TOOLS_PER_TURN | 4 | Shared game-tool budget across agents within one player turn |

Validate positive values and recognized providers at startup. For Codex, require
an explicit supported analysis provider. No database schema migration is needed.
Use an explicit turn context for deadline, tool budget, conversation/turn IDs and
receipts. A new player action or separately submitted check gets its own context;
agent transitions or transport restarts do not reset the current turn's budget.

## Protocol and flow

```text
Player -> existing turn pipeline -> latest state + scenario evidence
       -> Executor -> conversation provider interface
                   -> CodexProvider -> ExecTransport (phase 1)
                                    -> AppServerTransport (phase 2)
                   <- validated final | tool_call
tool_call -> validate schema + current allowlist + budget
          -> existing execute_turn_tool / execute_tool
          -> Python state update + evidence receipt -> CodexProvider
final.content -> existing Python resolution validation -> Narrator
              -> existing narrative/check-button validation -> reply
Pending check -> player check submission -> Python dice/Luck pipeline
              -> same provider interface for follow-up -> narration
Analysis/import -> ANALYSIS_PROVIDER -> existing API adapter
```

Logical responses (exactly one per model decision):

```json
{"type":"tool_call","name":"skill_check","arguments":{"skill":"Spot Hidden","difficulty":"regular"}}
```

```json
{"type":"final","content":"The narrator text or Executor resolution JSON string"}
```

The example does not redefine the existing skill_check argument schema. Generate
the actual schemas from current tool definitions. Validate exact shape, reject
extra keys, unknown names and invalid argument types, then recheck the current
allowlist immediately before execution. Do not parse partial assistant deltas,
reasoning, Markdown fences or tool logs as a completed protocol message.
Choose a CLI-supported output-schema envelope without weakening local validation;
if union schemas are unsupported, normalize a strict envelope in the transport.

On malformed output allow at most one corrective request within the same deadline
and iteration budget, before executing anything. Tool exceptions become bounded
error receipts; never replay a completed or uncertain state mutation automatically.
Report failed operations as failed; do not turn exhaustion into a success narrative.
Respect enable_wrapup=False. Any optional finalization request must fit the same
request budget and have tools disabled. A pending check must not trigger automatic
rolling or advancement through later actions. Four tools can constrain legitimate
multi-target combat or RAG-heavy turns; preserve completed state and explain the
remaining action rather than raising the limit silently.

## Transport and lifecycle

Exec: use argv and stdin (no shell interpolation), --json, --output-schema,
--ephemeral, --skip-git-repo-check, an empty working directory and read-only
sandbox. Isolate user/project instructions, MCP/plugins, native shell/file/web
tools and environment secrets; read-only alone does not disable file reads.
Verify the installed CLI's actual disable controls before sending game data.
Keep CLI-managed OAuth storage; do not copy tokens or call undocumented OAuth
endpoints. Remove API-key overrides from child environment, and check login mode.
Parse documented completion events; require successful process completion.
Bound stdin payload and incremental stdout/stderr capture. Redact stderr and log
only safe diagnostics. Cancellation/timeout terminates and then kills the process
group if needed, drains pipes and reaps children, releasing admission in finally.
Tool execution cancellation must preserve existing persistence semantics; uncertain
completion is never retried as a new mutation.

App-server comes only after exec passes its tests: use local stdio JSON-RPC,
initialize handshake, isolated thread per logical conversation, correlated request
IDs, turn completion/error handling and cancellation. Verify the installed protocol
schema and reject unavailable sandbox/tool isolation. Bound and drain notifications;
disconnect/restart invalidates pending work without replaying tools. Shut down the
server on provider shutdown. Keep exec selectable for comparison and rollback.
Do not reuse another player's thread, hidden context or stale game state.

## Tests and acceptance

1. Fake transport: text, one tool, two sequential tools, error receipt, final after
   receipt, unknown tool, invalid arguments, malformed/empty JSON and extra fields.
2. Subprocess: nonzero exit, malformed JSONL, timeout including queue time,
   cancellation while queued/running, output flood and process cleanup.
3. Budgets: iteration exhaustion, shared Executor/Narrator tool limit, concurrent
   turns isolated, finalization cannot exceed budget, no duplicate mutation retry.
4. Existing provider contract tests and analysis/vision routing compatibility.
5. CoC integration with isolated state: inspect documents -> pending skill check
   -> player check -> Python dice and optional Luck -> resolution -> narration.
   Assert receipts and state, not just output text; no reroll or unsolicited action.
6. Opt-in live OAuth exec smoke tests for text and isolated CoC flow; repeat the
   same fixtures on app-server only after exec passes. No Discord sends.
7. Record model, CLI version, login mode (no tokens), requests, game tools,
   queue/transport/whole-turn duration and correctness. Authentication status alone
   does not prove the selected model or a game turn works.

Run the relevant existing suite, lint/type checks and diff checks. Publish measured
results separately from assumptions; do not promise an OAuth speed improvement.

## Staging and open checks

After spec approval: interface/config and text PoC; tool protocol and bounds;
CoC integration; then app-server parity. gpt-6-luna availability, CLI native-tool
isolation, schema support and cancellation behavior require implementation tests.
If any necessary isolation control is unavailable, report the concrete blocker.
Decide whether to migrate analyze_text/analyze_image only after these results.

## References

- https://developers.openai.com/codex/noninteractive
- https://developers.openai.com/codex/auth
- https://developers.openai.com/codex/app-server
- Local codex-cli 0.157.1 help; login status reports ChatGPT authentication.

## Implementation record (2026-09-28)

Both transports passed a real OAuth check/gateway/resolver/narration smoke test
with gpt-6-luna on codex-cli 0.157.1. A fake-transport regression also exercises
Supervisor -> Executor -> actual skill_check -> pending persistence -> Python
check resolution -> Narrator without rerolling.

The strict wire envelope is `{"decision": ...}`. Tool decisions encode arguments
as `arguments_json` (a JSON string), preserving optional legacy tool fields without
making them mandatory in a strict output schema. Python decodes the string and
validates it against the actual current game tool schema before dispatch.
Final content remains a string, including the Executor's resolution JSON.

App-server uses local stdio, a server per conversation, and a fresh ephemeral
thread per decision with authoritative Python context. The server is reused
between tool decisions and closed at conversation exit; this is not a global
server pool. Effective inherited MCP/plugin names are explicitly disabled for
threads. CLI native shell settings affect only the child, not the developer's
shell or global Codex settings.

In-flight game mutations retain existing gateway ownership and may outlive the
LLM deadline while settling. Expiration prevents subsequent model calls; it does
not undo or replay the mutation. A repeated identical tool invocation in a turn
is rejected conservatively. This can defer legitimate repeated operations, which
should be expressed as a separate action rather than silently bypass the guard.

The 50-case evaluation means 25 synthetic scenario runs per transport, each with
any required deterministic check follow-up. It measures full Supervisor behavior
and records tool receipts and state assertions. It does not replay production
logs or certify arbitrary campaign/combat behavior. See
[testing guide](../../guides/codex_oauth_testing.md).

[50-case evaluation / 50 案例結果](../../evaluations/codex_oauth_50/README.md)

## Tool correctness follow-up

Live traces reproduced a final decision claiming skill_check unavailable while
the same request supplied it. Distinguish native CLI tools from executable JSON
host actions with concrete request/receipt examples. Existing pending identities
were already in prompts; add a structured, fresh Executor decision context and
waiting-resolution candidate. Scope check-creation schemas to investigators
without pending checks/Luck, refreshing after each mutation. Preserve explicit
clear/correction, other investigators, and unrelated inventory work. Gate only
Codex callers; keep Python's final state/evidence validation unchanged.

Record proposed decisions, validation codes and full tool receipts only in opt-in
synthetic evaluation traces. Count invalid JSON/tool proposals as accuracy errors
even if local validation blocks execution. No fixed extra LLM review or automatic
check creation, no PR/merge. Validate adversarial/stale schema cases and rerun
focused pending/new-check cases plus unrelated-action regressions.

The follow-up also reproduces an Executor final response claiming a check was
requested without sending any tool_call. Permit one conditional retry only for
an incomplete Executor final with no prior transcript/tools, available tools,
and enough iteration budget for both a tool and final. Explain that an unsent
call has no receipt; preserve genuine evidence/permission blockers. This is not
a fixed review stage or automatic Python check creation. Count such retries
separately from first-pass correctness; all existing deadlines/budgets apply.

## Follow-up: inventory actions with pending checks

Return existing Python resolution validation feedback to Codex before accepting
a final, at most once and within the same iteration/deadline/tool budgets.
Reuse existing validation; do not loosen completion evidence or replay committed
tools. Refresh inventory/pending context and explain that an independent pickup
needs its own successful tool receipt before handing back the remaining check.
Validate real blockers and retry exhaustion. Run 25 real OAuth synthetic cases
(15 exec, 10 app-server) covering pending pickup, plain pickup, unchanged pending,
new successful checks and failed-check follow-up. Record repairs separately.
