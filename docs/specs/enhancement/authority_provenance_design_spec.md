# Provenance-aware world authority and narrative continuity

[繁體中文](authority_provenance_design_spec_zh.md) | [Docs index](../../README.md)

## Status and problem

Category: `enhancement`. Status: **partial — stop-amplification phase implemented; canonical projection, delivery contract, and correction/reconciliation remain**. Audited against `main_v2` at `7cef87c`.

`GroupState.log` currently stores mostly `role/content` for player turns, KP canon, and approved corrections. The same flattened text feeds the rolling summary and Memory RAG. A mistaken narration can therefore return as apparent evidence and be repeated or used to justify a tool call. `record_established_fact` and `record_clue` currently accept text without checking a source; their rows also enter Scene Digest. The correction adjudicator can cite nearby `log:N` as evidence, even though it proves only what was said.

## Authority model

| Layer | Meaning | Can authorize a consequential world fact or mutation? |
| --- | --- | --- |
| Authority | Current committed state, durable tool/event receipts, scenario evidence with its conditions met, verified fact/clue records, explicit authorized KP canon | Yes, within timeline, audience, and source scope |
| Presentation | Delivered Keeper narration and NPC dialogue | No. Preserve what players heard and ordinary sensory continuity. |
| Claim | Player statements, OOC allegations, unverified model inferences | No. Preserve and investigate without promoting them. |

Scenario rules can describe a hidden or conditional event before it occurs. An investigator's discovery becomes an established play fact only after its condition and reveal are committed. Current state wins over a stale Scene Digest. A verified historical digest can support continuity but cannot overwrite current state. `campaign_summary`, conversation memory, and raw prose remain progressively weaker conversation aids, never independent mutation authority.

**Interpret present actions generously; never manufacture the past on the bot's own initiative; let players correct it.** Player wording and translated scenario text may be imprecise. Resolve a plausible present action from the whole scene and allow ordinary play to progress unless a specific established fact prevents it. The Keeper may establish the *new* outcome through the proper game workflow, including taking an incidental item or using a scenario-given key. Ambiguity or the bot's own prose must not silently become an invented prior acquisition, completed check, opened door, discovered clue, or other past event. A player's explicit correction is different: directly repair mistaken presentation and harmless earlier details, including an incidental possession, when no established scenario fact or committed mechanic conflicts; record it as a player-requested correction, not as newly discovered scenario evidence. If the correction changes a plot resource, clue, access right, position, or mechanic, check the scenario and durable state/receipts, reconcile a genuine omission, or use the existing correction path. Lack of an exact phrase in a translation is not itself a reason to refuse that correction.

The Keeper remains a narrator. Object existence/possession and plot relevance are separate dimensions: an incidental diary or an extra copy may exist and be described without becoming a scenario clue. A quantity constraint applies only when the scenario or a committed event makes that particular quantity consequential. Reading an incidental diary may produce ordinary flavor text, even repeatedly, while never adding a clue, required lead, trigger, or mechanical benefit. The bot must not present it as a required lead or repeatedly steer the party to read it. If a player points out that this importance was the bot's unsupported implication, correct the presentation directly and keep that correction visible to later summary/memory.

An item can be plot-relevant without being a clue. In *The Haunting*, the landlord gives investigators keys, an address, and advance cash; the house's front door has a single lock, and the text mentions four additional bolts. The opening keys are scenario-backed access items: when a player uses them on the house's front door, the Keeper should normally allow entry through a coherent scene ruling. The source need not contain an exact key–lock pairing, especially after translation. Mentioned bolts are not automatically engaged or a mandatory extra gate; only an actually established obstruction needs resolving, with a concrete explanation and playable way forward. This does not grant an unrelated generic key the same use. Opening a door is an access effect, not itself a clue unless it reveals information. By contrast, the sealed basement room's cabinet contains moldering church records, while a successful Spot Hidden search beneath the cabinet reveals one journal and one tome. The conditional discovery is not established for investigators before the search succeeds.

The AI Keeper may correct its misreading of scenario evidence; it may not decide to rewrite the scenario. A registered human KP Assistant may explicitly override scenario canon with a recorded supersession. Neither can rewrite a committed dice/resource result through prose; use its authorized deterministic correction workflow. A player may directly correct nonconsequential narration or an incidental item omission. A claim cannot by itself grant a key's plot-specific access, change a verified scenario quantity, or overwrite a committed position; if the bot merely misstated position without a committed move, the player's correction can repair that presentation.

## Durable records and projection

- Keep `state.log` and player-visible history. New entries carry `record_kind`, `authority`, `turn_id`, `timeline_id`, audience, and fact/event references where available. Ordinary player text is a claim; an explicit correction is marked as a correction request and its accepted repair recorded separately. A delivered Keeper response is presentation. Explicit KP input and committed tool results are recorded separately from the AI's reply to KP. Existing bare entries become `legacy_mixed` presentation unless a matching durable receipt, approved correction, or explicit KP source can be identified.
- Persist a small `CanonicalFactRef`/event projection, not a general knowledge graph. A verified fact has a stable ID, typed subject and optional quantity/location/identity constraints, source kind and durable source ref, visibility/recipient, timeline, and verification/supersession status. A scenario ref binds scenario version, record ID, and content digest; `tool:N` is only a transient observation and cannot authorize a durable fact. A changed scenario digest requires revalidation of scenario-derived facts, while already committed mechanic results remain intact.
- Keep `record_established_fact`/`record_clue` available for play and correction. New or legacy records without a verifiable source remain visible as `unverified` but cannot enter the canonical projection or authorize a consequential tool call. A source ID existing is insufficient if it does not support the asserted content. Verifiable source quotations or typed tool receipts can be promoted; explicit human KP canon follows its own audited path. Do not silently discard older rows.
- A player can acquire and persist an incidental item through the existing inventory tool when the scene does not explicitly rule it out. The committed inventory event establishes possession, not plot-bearing contents, clue status, special weapon damage, ability to open a named door, or scenario trigger. Those capabilities require their own character-sheet, tool, rules, or scenario basis, which may be a reasonable interpretation of the whole scene rather than an exact phrase in translated text. A player's present-tense acquisition is distinct from an unsupported claim that an important key was acquired in an earlier turn. If the scenario explicitly grants opening equipment but the inventory record is missing, reconcile that omission against the scenario; do not reject the player's use merely because the item was not recorded.
- Project only source-verified fields from Scene Digest; its fact/clue lists do not gain authority just because they were copied into a digest. Give each agent only the authority needed for its turn and audience. A private clue requires an explicit reveal/share event to change visibility; accidental public prose does not change its audience.
- Adapters must deliberately project log metadata to model context. Merely adding database keys is ineffective: OpenAI/Anthropic currently send `role/content`, while Codex receives structured history. Keep provider request schemas valid and give both Executor and Narrator the same authority labels and precedence.

## Turn and correction flow

```text
scenario rule + committed events + explicit human KP canon
  -> verified, audience-scoped authority projection
  -> Executor adjudication and NarrationRequirements
  -> Narrator prose + restricted tools
  -> DeliveryEnvelope checks required visible facts and live controls
  -> delivered prose saved as presentation, never auto-promoted

player natural-language correction
  -> OOC admission before movement/map mutation
  -> narrative-only repair OR source-backed correction case
  -> KP Assistant ruling if registered, otherwise AI Keeper ruling from system evidence
  -> durable supersession / authorized state repair when approved
```

`NarrationRequirements` contains relevant authoritative facts, committed events, pending/Luck controls, allowed evidence refs, and typed hard constraints only for scenario- or event-significant details. It does not require every known fact or incidental prop to be repeated. `DeliveryEnvelope` requires only facts due in this turn and visible to the recipient. Deterministic checks can reject explicit, structurally recognizable conflicts with a consequential quantity/location/identity, or a missing required fact/control. They cannot guarantee semantic correctness of arbitrary prose. On a proven conflict, deliver a deterministic verified-facts-and-controls fallback; retain the raw generated candidate only for diagnostics, not as delivered history. Do not add a fixed LLM fact judge.

Explicit natural-language OOC corrections must be intercepted before the existing map action transaction. Ambiguous utterances are clarified rather than mutated. Sensory wording, harmless incidental possessions, or an unsupported implication that an incidental item is plot-important may be corrected immediately, without a separate LLM adjudication call, and recorded as a player-requested presentation/inventory correction as appropriate. A disputed scenario clue, constrained quantity, plot-specific access, committed location, resource, or mechanical change remains an allegation until checked. The existing `/coc correct` path remains available. With a registered KP Assistant, that human rules on consequential disputes; otherwise the AI Keeper uses the current conditional adjudication path and system-held evidence. `log:N` may prove a previous utterance, never world truth by itself. A valid consequential resolution records what was superseded and what source supports the replacement. An unresolved dispute does not grant a plot resource or modify committed mechanics.

## Summary, memory, and old data

Format summarization input as separately labeled authority receipts, delivered presentation, and claims. Build the authority portion deterministically; keep `campaign_summary` as a conversation/presentation summary and mark existing summaries unverified immediately. The summarizer may preserve scene atmosphere, incidental props, and what an NPC said, but may not turn them into unsupported clues, scenario-constrained quantities, positions, or mechanical ownership. Keep the existing occasional summary call; do not add a normal-turn call.

Memory chunks retain the original conversation wording plus `memory_kind=conversation`, `authority=mixed`, source revision, timeline, and source-message metadata. Change the retrieval header to say these are earlier utterances, not necessarily verified events. Existing chunks are immediately treated as mixed/unverified. A later reconciliation marks only contradictions provable from typed authority; ambiguous material stays unverified. Do not delete what players saw. Rebuild the conversation summary from labeled inputs after reconciliation. Approved corrections supersede old descriptions in the authority projection without erasing the historical log.

## Delivery plan: four independent PRs

1. **Stop amplification:** log provenance for all writers and provider projection, Executor/Narrator hierarchy, memory header and metadata, authority-aware summary input, immediate downgrade of legacy summary/memory. Preserve current LLM request topology. New ungated fact/clue rows cannot silently become authority.
2. **Canonical projection:** durable source refs and receipts, verification status for fact/clue rows, scenario-condition/reveal semantics, typed audience-scoped authority block and `NarrationRequirements`. Keep current-state and timeline checks.
3. **Hard-fact delivery:** extend `DeliveryEnvelope` with verified refs and required visible facts, narrow deterministic checks and safe fallback. No general prose regex judge and no fixed LLM review call.
4. **Correction and reconciliation:** natural-language OOC admission before map parsing, existing human/AI adjudication rules, durable supersession, legacy receipt matching, conflict markers, and conversation summary rebuild. Preserve the explicit `/coc correct` route.

Each PR gets its own branch and spec status/evidence update. PR1 must not wait for PR4 to lower the authority of old summary and memory. Changes to the normal-turn provider call count require explicit measurement and review.

The stop-amplification branch stamps new log entries, projects compact authority labels through every conversation provider, frames old summaries and Memory RAG as unverified conversation, stores source-message metadata in new memory chunks, and stops unsourced fact/clue rows or raw log citations from independently authorizing correction rulings. Durable source verification and positive promotion are reserved for the canonical-projection phase. Existing player correction flows remain available.

## Verification

- C01–C03: *The Haunting* establishes moldering church records **inside** the sealed basement room's cabinet; a successful Spot Hidden search reveals **one journal and one tome beneath** it. If Narrator changes their identity, count, or location, next-turn Executor/Narrator follow the source and the committed discovery condition, not the mistaken prose. Before the successful search, they do not tell investigators that the journal and tome were found.
- C04: a player's correction about a harmless generic key may repair incidental inventory, but cannot give it access to a named door. The scenario's opening grant of house keys *does* support that access: a missing inventory entry is reconciled rather than treated as an unsupported claim.
- C05: an NPC's denial of the journal or tome remains dialogue, not world fact.
- C06: an explicit human KP override supersedes an older presentation, while the AI Keeper cannot self-authorize a scenario rewrite.
- C07–C08: contaminated old summary/memory remain retrievable as conversation history but cannot override canonical quantity/location or authorize tools.
- C09: harmless sensory continuity remains available.
- C10: ordinary turn LLM request topology is unchanged; conditional correction review is measured separately.
- C11: an OOC correction mentioning “go upstairs” cannot mutate position before adjudication.
- C12: private facts are not projected publicly and change audience only through a committed reveal.
- C13: scenario reindex/content change invalidates a scenario ref without replaying committed mechanics.
- C14: a required fact or pending control survives a failed Narrator through deterministic delivery, without repeating the original tool.
- C15: an incidental extra diary may be described and retained without becoming the scenario's journal or tome, revealing their contents, or changing their discovery condition.
- C16: when the bot incorrectly implies an incidental diary drives the plot, a player's natural-language correction removes that implication from future narration without a fixed review call.
- C17: an incidental item can be added to inventory, while its plot and mechanical capabilities remain unproven; an axe does not receive invented damage and a generic key does not open a specific locked door.
- C18: reading an extra incidental diary, including later rereads, can yield consistent flavor prose but never silently creates a scenario clue or changes plot progression.
- C19: the landlord's opening keys are retained or reconciled as scenario-backed possessions. A player using them on the house's front door can enter without an exact translated key–lock pairing or a mandatory bolt check. If an obstruction was actually established, the Keeper explains it and offers a playable next step instead of permanently blocking entry. The keys are not labeled clues solely because they allow access; an unrelated generic key does not inherit this use.
- C20: imprecise present-tense “use the keys and go in” is adjudicated from the scene and may commit a new entry event. If a player corrects “I kept an ordinary notebook earlier,” repair that harmless omission; “I already obtained this scenario clue / opened that specific door” requires source and state review before gaining that consequence. A missing scenario-granted key is reconciled from the opening source rather than invented or rejected.

## Non-goals and limits

Do not delete conversation history, shorten narration, remove ordinary sensory creativity, make summary a source of authority, grant player claims automatic canon, or add a fixed LLM reviewer. Provenance proves *which source was cited*; it does not by itself prove every free paraphrase is semantically entailed. Admit only source-verifiable content into typed authority, and keep the correction path for disputes. Deterministic hard-fact checks are intentionally partial.

## Resolved recording boundary

Keep the recording behavior of `record_established_fact` and `record_clue` for play and natural-language correction. An unsourced entry stays `unverified`. It may preserve harmless narrative continuity when doing so cannot advance the plot or change mechanics. It cannot establish a consequential clue, plot resource, scenario-constrained quantity or location, and cannot authorize a consequential later tool call until promoted from a verifiable source. A prior mention of “church records” therefore remains part of what the Keeper said, not evidence that those records are a required lead. Incidental objects may exist without such promotion.
