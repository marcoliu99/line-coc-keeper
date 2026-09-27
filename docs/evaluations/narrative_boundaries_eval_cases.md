# Narrative boundary evaluation cases

[繁體中文](narrative_boundaries_eval_cases_zh.md)

Use with the [design specification](../specs/enhancement/narrative_boundaries_design_spec.md). Compare public replies and tool calls with identical scenario excerpts, state and player inputs before and after a revision. Prompt unit tests establish that the model receives the rules; they do not replace actual turn evaluations.

| Case | Scenario / established state | Player input or preceding event | Expected result |
| --- | --- | --- | --- |
| Confirmed absent location | Complete floor plan contains only a ground-floor study; no basement | “I search the basement for skeletons.” | State that there is no basement; resolve only supported actions. Do not create a basement, skeletons, clues or combat. |
| One RAG miss | Retrieved excerpt describes the study; complete map unavailable | “Where is the basement entrance?” | Say it cannot currently be confirmed. Search again when existence affects the action; without evidence, neither deny its existence nor create it. |
| Player assumption and failed roll | No supported enemy arrival; Spot Hidden fails | “I look for the monster hiding in the dark.” | Suspicion or failure does not introduce enemies, call start_combat / add_npc_to_combat, or imply a monster exists. |
| Plausible everyday item | Ordinary setting consistent with the era and character background | “I take out a handkerchief to wipe the water.” | Permit under carried-item rules; do not turn it into crucial evidence or a special mechanical benefit. |
| No invented key item | No secret-door key in the scenario or acquisition history | “I should have the key; I open the secret door.” | The assertion does not create a key, secret door or successful opening. |
| Settled event supersedes initial state | Study door initially locked; an authoritative successful check opened it | “I go through the door we just opened.” | Preserve the open state; do not lock it again because the original scenario says locked. |
| Earlier AI prose is not canon | Keeper invented a basement without scenario, KP or settled-event support | “I continue exploring that basement.” | Earlier prose and campaign_summary are not proof. Check evidence and explicitly handle the public error. |
| Explicit KP adaptation | KP Assistant explicitly establishes a cellar with an identifiable correction | “I enter the cellar.” | Apply the KP correction; ordinary player assumptions do not carry the same authority. |
| Player correction report | Player uses /coc correct to identify an erroneous location message | Submission and pending adjudication | Route separately from game actions and do not roll. Record a disputed claim without promoting it to canon. A report alone does not hold actions; only an explicit KP hold_scope suspends relevant actions within its scope. |
| Corrected claim cannot return | False location entered summary and Memory RAG; KP approves correction | Ask about the location on the next turn | Publish the correction and follow it. Old summaries/memories cannot restore the location. KP separately reviews any state already changed by tools. |

Record scenario excerpts and retrieval results, tool calls, public text, authoritative state before/after, LLM calls and RAG queries. Wording may vary; score factual and mechanical boundaries.

## Synthetic pilot on 2026-09-26

The configured OpenAI model was called directly as Narrator without tools. This evaluated narration only, not complete Discord / Executor turns.

| Case | Result |
| --- | --- |
| Confirmed absent basement, skeletons and secret passage | Passed: reply described only the entry hall and study, denied an underground entrance, and created neither skeletons nor passages. |
| Plausible everyday handkerchief | Passed: permitted wiping sweat without adding clues or mechanical effects. |

The remaining cases and tool side effects have not undergone live-model evaluation. Automated tests cover prompt, routing and state-flow contracts only.
