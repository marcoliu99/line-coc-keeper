# Same-turn mechanical obligations of a scenario event

[繁體中文](event_obligations_design_spec_zh.md) | [Docs index](../../README.md)

## Status and goal

Category: `bug`. Status: **implemented**. Source: finding CS-007 of the 5-player / 500-turn Camp Sunny validation (Workstream C). Based on `main_v2` at `aa79f22`.

A severed hand was revealed. The retrieved scenario evidence stated the SAN rule for seeing it, the narration showed the hand, and no SAN check existed until a player later said they were frightened: the canonical event happened in the story but not in the mechanics. The Narrator receives the same scenario evidence as the Executor and can narrate what it describes whether or not the Executor called the tool the rule requires.

## Contract

1. **Read only what the scenario wrote** (`app/services/event_obligations.py`). From the evidence a turn was given (proactive retrieval, the one recovery search, the Executor's own `search_scenario` results) it extracts three kinds of explicit rule: a SAN loss (`SAN 1/1D4`, `理智檢定 0/1d6`, `Sanity 0/1D3`), a damage roll with a trigger (`若有人觸碰祭壇…受到 1D6 點傷害`), and a required skill check with a trigger (`走進地下室的人須通過困難聆聽檢定`). Atmospheric horror without such a rule yields nothing; combat attacks are excluded; damage and forced checks need a trigger word.
2. **Decide that the event was shown.** A rule carries *anchors*, the distinctive words of the sentence before the rule (`斷手`, `水桶`), apart from generic ones. It is triggered when the narration contains every anchor (or two thirds of them when there are more than two), so mentioning the bucket without what is in it owes nothing.
3. **Apply before the narration is committed** (`app/agents/obligation_gate.py`, called by the supervisor after the Narrator and before the consistency, Guard and delivery steps, for player actions and resolved-check follow-ups). For each triggered rule that has not been applied to this investigator, in order and at most four per turn: `sanity_check`, `adjust_character` with a damage rolled by Python, or `skill_check`, through the ordinary tool path, so autoroll, pending checks, Luck, major wounds and combat behave as for any other call. The reply gets one deterministic line per obligation, and the handoff is refreshed so the pending-check instruction is appended.
4. **Idempotent.** A ledger entry (`check_consequence_receipts`, identity = timeline + character + rule, plus the turn for a rule that says 每次／each time) is reserved before the tool runs. A replay, a retry or a later mention of the same event finds it and does nothing; a failed or blocked application releases the reservation so a later turn can try again. Another investigator who sees the same event owes their own.

## Contract kept

State changes stay in the deterministic tools; the gate only decides that one is owed and with which arguments, taken from the scenario's own sentence. The LLM does not choose the loss. No rule text or trigger of a specific scenario is in the runtime.

## Enforcement

`tests/test_event_obligations.py`: rule reading in both languages and dice formats; no obligation from atmospheric horror, combat or a damage line without a trigger; the anchor test; one reply carrying the reveal and its pending SAN check; autoroll settlement; no duplicate on replay or a later mention; a second investigator; a blocked application released and retried; damage through the same mechanism; a forced check; and that only the gate applies obligations. Removing the ledger makes the replay and damage tests fail.

## Not covered

The spec's preferred order (derive obligations, then narrate) is not literal: whether the event happened is only known from what the Narrator shows, so the obligation is derived from the narration before it is committed and delivered. Detection is lexical, so narration that shows the event without its named things (or a rule whose cause is only implied) is not caught, and a rule stated outside the evidence the turn received is invisible. Only the acting investigator is charged; "everyone who sees it" is not expanded. A check blocked by another pending check is released, not queued. None of this has been run against the real Camp Sunny scenario or model.
