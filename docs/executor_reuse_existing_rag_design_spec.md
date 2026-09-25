# Executor Reuse Existing RAG Context Design Spec

## Problem and goal

In the Corbitt House basement-stairs log, the Supervisor proactively retrieved scenario passages before Executor ran. Executor then issued several additional `search_scenario` calls with overlapping stair queries before creating the DEX check. Each search can add an embedding request and another LLM tool-use iteration. The recent local API experiment used the real stored Corbitt scenario and found that, when the supplied RAG context was sufficient, instructing Executor to reuse it avoided the additional search while preserving the same hard DEX check in all 10 treatment trials.

The goal is to make Executor treat useful scenario RAG context already present in its prompt as reusable evidence, while still allowing another search when a decision-relevant detail is missing.

## Scope

- Add Executor-specific instructions for inspecting and reusing the current turn's `rag_context` before calling `search_scenario`.
- Keep `search_scenario` available. Executor may call it when an unresolved, concrete scenario fact can change the mechanic or immediate consequence.
- Clarify that paraphrasing an already answered question is not a reason to search again; after a search returns, use that result before considering another search.
- Keep scenario facts and mechanic decisions separate. Reusing RAG must not itself establish a check, resolve a roll, change character state, or move the investigator.
- Add focused regression coverage for sufficient context, missing decision-relevant detail, empty context, and preserving ordinary mechanic tool behavior.

## Non-goals

- No approximate/embedding query cache or new persisted data.
- No hard per-turn search cap and no suppression of genuinely new scenario questions.
- No change to RAG ranking, chunking, result count, or embedding model.
- No parallel execution of state-mutating tools.
- No change to legacy single-agent Keeper prompts in this iteration.

## Data and interfaces

No schema or persistent-state changes are required. `AgentMessage.payload["rag_context"]` already carries the proactive scenario RAG output into `ExecutorAgent`; `build_dynamic_prompt_with_context` already labels it as scenario-related content. The implementation will add Executor-scoped prompt text at the existing prompt assembly boundary and leave the provider tool schema and `search_scenario` implementation unchanged.

## Proposed flow

1. `context_builder.build_context` performs the existing proactive retrieval and stores its formatted result in `rag_context`.
2. `executor.run_executor` builds the Executor prompt with an explicit reuse policy.
3. Executor checks whether `rag_context` answers the concrete scenario question needed for this action.
4. If it does, Executor proceeds using those facts and calls only the necessary mechanic tools.
5. If a decision-relevant scenario fact is absent or ambiguous, Executor may call `search_scenario` for that fact.
6. Executor incorporates the returned passage. It may search again only for a distinct unresolved fact that can affect the current action; wording a covered question differently does not qualify.
7. Narrator continues to receive the Executor's mechanic result and scenario context through the existing pipeline. RAG reuse alone never authorizes a state change.

## Prompt behavior

Executor instructions should say, in plain language:

- The current turn's scenario RAG block is already retrieved material and should be inspected before searching.
- Do not call `search_scenario` to reconfirm facts stated clearly in that block.
- Search only for a concrete missing scenario detail that can change the current ruling or immediate consequence.
- When a search result answers the question, use it and continue the turn; do not repeat the same search using alternate wording.
- If available context and search results do not establish a fact, preserve that uncertainty and do not invent it.
- These rules govern information retrieval only. Follow the player's action and game mechanics normally; do not create a check or advance the scene merely to avoid a search.

When `rag_context` is empty or degraded, existing scenario-search behavior remains available.

## Integration and observability

Use the existing `build_dynamic_prompt_with_context` path so the rule is limited to agents that receive the proactive context. Preserve current `search_scenario` logging and spans. No additional lookup or classifier request is introduced, and no query-result text is written to persistent state.

## Testing plan

- Add focused tests showing the Executor prompt includes the reuse policy and retains the actual RAG block.
- Add tests that empty/degraded context still allows the scenario search tool and that the policy does not suppress mechanic tools or imply a result.
- Run the existing provider, Executor, prompt configuration, and state/narration tests, then the full suite and static checks.
- The pre-implementation and post-implementation 20-trial API A/B runs are recorded below. The harness and reports stay outside the repository's unit tests.

## Pre-implementation API experiment

The temporary harness at `/tmp/coc_search_loop_experiment_actual_rag.py` used the local `The Haunting Scenario trimmed` state and the real `scenario_rag.search()`/`search_scenario` implementation. The proactive query was `嘗試下到地下室`; it returned 3 RAG results from pages 7 and 17, formatted to 835 characters. The configured model was `gpt-5.6-luna`. Twenty API trials were interleaved: 10 baseline trials instructed to perform an additional scenario search, and 10 reuse trials instructed to use the already-injected RAG context when sufficient.

| Measure | Baseline: search again (n=10) | Reuse current RAG (n=10) |
| --- | ---: | ---: |
| Mean elapsed time | 9.50 s | 4.57 s |
| Median elapsed time | 8.87 s | 4.44 s |
| Scenario search tool calls | 10 | 0 |
| Hard DEX checks created | 10/10 | 10/10 |
| Estimated LLM requests per trial | 3 | 2 |

The mean elapsed time was 51.9% lower in the reuse group; the median was 49.9% lower. The LLM request count is an estimate derived from the sequential tool-call pattern, not provider telemetry. `search_scenario` performed real read-only retrieval; `skill_check` was stubbed to avoid mutating local game state. The same hard DEX requirement was explicitly fixed in both prompts, so the experiment demonstrates search/latency reduction while preserving that mechanic call, but it does not establish that the proactive RAG passages alone contain every rule needed to choose the check difficulty or consequence. This is a small, prompt-level experiment and not a production latency guarantee.

## Post-implementation API experiment

After implementing the Executor-specific reuse policy, the isolated harness at `/tmp/coc_search_loop_post_impl_20.py` ran another 20 interleaved API trials against the real local Corbitt RAG index. It used the actual `build_executor_dynamic_prompt_with_context` function and the application's real `search_scenario` and `skill_check` tool schemas, filtered to those two tools for a controlled comparison. A read-only real RAG query (`地下室樓梯 年久失修 Push roll falling 1D6 HP Corbitt basement`) returned 3 results from pages 7, 17, and 10, formatted to 1,096 characters. The static benchmark prompt held the action and hard DEX check constant. Scenario search used the real read-only RAG implementation; the skill-check handler returned a pending result without mutating game state. A temporary wrapper around `_create_response_async` counted actual Responses API invocations per trial.

| Measure | Pre-change prompt (n=10) | Executor RAG reuse policy (n=10) |
| --- | ---: | ---: |
| Mean elapsed time | 7.97 s | 5.29 s |
| Median elapsed time | 7.72 s | 5.22 s |
| Scenario search calls | 10 | 0 |
| Hard DEX checks created | 10/10 | 10/10 |
| Mean Responses API calls | 3 | 2 |

Mean elapsed time was 33.6% lower and median elapsed time 32.4% lower in the reuse group. This controlled trial shows that the implemented dynamic-context policy can avoid a redundant scenario lookup when the supplied real RAG passage already contains the relevant rule, while preserving the fixed mechanic outcome. It does not measure the full 36-tool Executor request or guarantee the same savings when proactive RAG omits a decision-relevant fact; the production policy explicitly allows a follow-up search in that case. The exact request count came from the temporary wrapper because the async OpenAI provider does not currently populate the shared `iteration_count` metric.

## Tradeoffs and unresolved questions

- Prompt instructions are probabilistic. The regression tests can ensure the policy is present and does not break wiring, but cannot prove every model will obey it in every turn.
- The 20-trial experiment intentionally compares reuse against a baseline that repeats a search despite existing context. Its result supports the direction for this scenario but does not justify a universal latency guarantee.
- The policy should permit follow-up searches where the proactive retrieval is incomplete. Overly broad wording could hide a necessary clue; tests and the post-implementation experiment should verify the specific basement-stairs flow while preserving the missing-fact path.

## Review gate

The user confirmed this design for implementation after requesting that the pre-implementation API results be added to the spec.
