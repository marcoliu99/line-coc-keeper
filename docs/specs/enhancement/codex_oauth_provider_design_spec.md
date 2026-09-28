# Codex OAuth conversation provider

Status: proposed; environment prepared, runtime implementation pending review.
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
