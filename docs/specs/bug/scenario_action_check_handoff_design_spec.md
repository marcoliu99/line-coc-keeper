# Scenario action and check handoff

Status: approved implementation. Base: main_v2. Branch: bug/scenario-action-check-handoff.

## Incident

Two messages, nine seconds apart, declared the same pickup with a typo correction. The first took 67 seconds, searched six times and created one check; the second ran Executor and Narrator again for the unchanged pending check. The scenario distinguished picking up an object, retaining a held object, and catching an airborne object, but the selected check followed a different condition. A separate NPC roll was not bound to the player's pending check. Chinese source records contained the referenced rule but had no dependency edges.

## Contracts

- Preserve the actual player declaration separately from the model's action interpretation. Checks may carry a bounded action basis (object state, applicable rule reference and transition); it is interpretation, not new canon. Prompt all agents to follow the most specific established trigger and not choose an unrequested defensive maneuver. No hard-coded scenario names.
- Extend skill_check with an optional opposed contract: opponent skill/value, explicit tie winner (player/opponent/neither), source, and bounded win/loss consequences. The server rolls the opponent once after pending/Luck/idempotency checks and persists the receipt. The model cannot supply dice results. Compare success tiers in Python; two failed rolls do not grant success. Carry the contract and original action through pending, Luck, final structured events and narration. Consequences describe the selected branch; state mutations still require existing tools. Do not expose private opponent values/source in public feedback. Autoroll and manual checks use the same comparison; Luck never rerolls either side.
- Skip Narrator only after a validated await_check/await_luck handoff references the same existing owner/check/decision on the same timeline, with no gameplay change or new tool/output effects. Return a brief reminder and commit the ordinary turn normally. Never classify messages as duplicates by fuzzy text matching; cancellation, corrections and independent actions still reach Executor.
- External workbooks require explicit cross-page dependencies. Deterministically recognize named source references only when a unique source heading matches; add required links without guessing from keywords or page adjacency. Resolve against all records before authorized chapter filtering; retrieval must still block inaccessible required targets. Unknown/ambiguous references are diagnostic and must be resolved in external preparation, not invented. Preserve full text/numeric checks. Existing approved records may receive derived in-memory links without editing their audit files.

## Flow

```text
player declaration + state + scenario closure
 -> Executor selects applicable condition + tool
 -> skill_check stores action basis + opponent roll
 -> pending player check -> player roll -> optional Luck
 -> Python tier/tie comparison -> authoritative event -> Narrator

Executor awaits same existing check, no new effects
 -> static pending reminder -> normal history commit (no Narrator call)

source named reference -> unique source heading -> required dependency
 -> chapter-authorized projection -> complete closure / fail closed
```

## Verification and limits

Offline tests cover unchanged-check reminders versus new/changed checks and effects, opposed win/loss/ties/both-fail, duplicate registration, Luck skip/spend, autoroll, restart persistence, raw-reason privacy, cross-page discovery, ambiguous references, chapter boundaries and unchanged source files. Full suite, Ruff and mypy are required. No paid LLM stage, production replay, reroll or live state repair. Rule selection is still an AI interpretation; structured evidence makes it inspectable but does not prove semantic correctness.
