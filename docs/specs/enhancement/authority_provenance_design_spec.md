# Provenance-aware world authority and narrative continuity

[繁體中文](authority_provenance_design_spec_zh.md) | [Docs index](../../README.md)

## Status and problem

Category: `enhancement`. Status: **backlog, awaiting design confirmation**. Audited against `main_v2` at `7cef87c`.

`GroupState.log` currently stores mostly `role/content` for player turns, KP canon, and approved corrections. The same flattened text feeds the rolling summary and Memory RAG. A mistaken narration can therefore return as apparent evidence and be repeated or used to justify a tool call. `record_established_fact` and `record_clue` currently accept text without checking a source; their rows also enter Scene Digest. The correction adjudicator can cite nearby `log:N` as evidence, even though it proves only what was said.

## Authority model

| Layer | Meaning | Can authorize a consequential world fact or mutation? |
| --- | --- | --- |
| Authority | Current committed state, durable tool/event receipts, scenario evidence with its conditions met, verified fact/clue records, explicit authorized KP canon | Yes, within timeline, audience, and source scope |
| Presentation | Delivered Keeper narration and NPC dialogue | No. Preserve what players heard and ordinary sensory continuity. |
| Claim | Player statements, OOC allegations, unverified model inferences | No. Preserve and investigate without promoting them. |

Scenario rules can describe a hidden or conditional event before it occurs. An investigator's discovery becomes an established play fact only after its condition and reveal are committed. Current state wins over a stale Scene Digest. A verified historical digest can support continuity but cannot overwrite current state. `campaign_summary`, conversation memory, and raw prose remain progressively weaker conversation aids, never independent mutation authority.

The AI Keeper may correct its misreading of scenario evidence; it may not decide to rewrite the scenario. A registered human KP Assistant may explicitly override scenario canon with a recorded supersession. Neither can rewrite a committed dice/resource result through prose; use its authorized deterministic correction workflow. A player may directly request a nonconsequential narrative correction, but cannot grant an unsupported key, change a verified diary count, or move a character merely by claiming a different past.

## Durable records and projection

- Keep `state.log` and player-visible history. New entries carry `record_kind`, `authority`, `turn_id`, `timeline_id`, audience, and fact/event references where available. Player text is a claim; a delivered Keeper response is presentation. Explicit KP input and committed tool results are recorded separately from the AI's reply to KP. Existing bare entries become `legacy_mixed` presentation unless a matching durable receipt, approved correction, or explicit KP source can be identified.
- Persist a small `CanonicalFactRef`/event projection, not a general knowledge graph. A verified fact has a stable ID, typed subject and optional quantity/location/identity constraints, source kind and durable source ref, visibility/recipient, timeline, and verification/supersession status. A scenario ref binds scenario version, record ID, and content digest; `tool:N` is only a transient observation and cannot authorize a durable fact. A changed scenario digest requires revalidation of scenario-derived facts, while already committed mechanic results remain intact.
- Keep `record_established_fact`/`record_clue` available for play and correction. New or legacy records without a verifiable source remain visible as `unverified` but cannot enter the canonical projection or authorize a consequential tool call. A source ID existing is insufficient if it does not support the asserted content. Verifiable source quotations or typed tool receipts can be promoted; explicit human KP canon follows its own audited path. Do not silently discard older rows.
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

`NarrationRequirements` contains relevant authoritative facts, committed events, pending/Luck controls, allowed evidence refs, and typed hard constraints. It does not require every known fact to be repeated. `DeliveryEnvelope` requires only facts due in this turn and visible to the recipient. Deterministic checks can reject explicit, structurally recognizable quantity/location/identity contradictions or a missing required fact/control. They cannot guarantee semantic correctness of arbitrary prose. On a proven conflict, deliver a deterministic verified-facts-and-controls fallback; retain the raw generated candidate only for diagnostics, not as delivered history. Do not add a fixed LLM fact judge.

Explicit natural-language OOC corrections must be intercepted before the existing map action transaction. Ambiguous utterances are clarified rather than mutated. A sensory wording correction with no consequential effect may be accepted immediately and recorded as presentation correction. A claimed key, location, clue, quantity, or mechanical change remains an allegation until checked. The existing `/coc correct` path remains available. With a registered KP Assistant, that human rules; otherwise the AI Keeper uses the current conditional adjudication path and system-held evidence. `log:N` may prove a previous utterance, never world truth by itself. A valid resolution records what was superseded and what source supports the replacement. An unresolved dispute does not grant a resource or modify committed state.

## Summary, memory, and old data

Format summarization input as separately labeled authority receipts, delivered presentation, and claims. Build the authority portion deterministically; keep `campaign_summary` as a conversation/presentation summary and mark existing summaries unverified immediately. The summarizer may preserve scene atmosphere and what an NPC said, but may not manufacture canonical objects, quantities, positions, or ownership from prose. Keep the existing occasional summary call; do not add a normal-turn call.

Memory chunks retain the original conversation wording plus `memory_kind=conversation`, `authority=mixed`, source revision, timeline, and source-message metadata. Change the retrieval header to say these are earlier utterances, not necessarily verified events. Existing chunks are immediately treated as mixed/unverified. A later reconciliation marks only contradictions provable from typed authority; ambiguous material stays unverified. Do not delete what players saw. Rebuild the conversation summary from labeled inputs after reconciliation. Approved corrections supersede old descriptions in the authority projection without erasing the historical log.

## Delivery plan: four independent PRs

1. **Stop amplification:** log provenance for all writers and provider projection, Executor/Narrator hierarchy, memory header and metadata, authority-aware summary input, immediate downgrade of legacy summary/memory. Preserve current LLM request topology. New ungated fact/clue rows cannot silently become authority.
2. **Canonical projection:** durable source refs and receipts, verification status for fact/clue rows, scenario-condition/reveal semantics, typed audience-scoped authority block and `NarrationRequirements`. Keep current-state and timeline checks.
3. **Hard-fact delivery:** extend `DeliveryEnvelope` with verified refs and required visible facts, narrow deterministic checks and safe fallback. No general prose regex judge and no fixed LLM review call.
4. **Correction and reconciliation:** natural-language OOC admission before map parsing, existing human/AI adjudication rules, durable supersession, legacy receipt matching, conflict markers, and conversation summary rebuild. Preserve the explicit `/coc correct` route.

Each PR gets its own branch and spec status/evidence update. PR1 must not wait for PR4 to lower the authority of old summary and memory. Changes to the normal-turn provider call count require explicit measurement and review.

## Verification

- C01–C03: canonical three Corbitt diaries inside the sealed basement cabinet survive a Narrator output of “church records”, “one diary”, or “under the cabinet”; next-turn Executor/Narrator use the verified fact.
- C04: a player's unsupported “I have the key” does not create inventory or open the door.
- C05: an NPC's denial of the diaries remains dialogue, not world fact.
- C06: an explicit human KP override supersedes an older presentation, while the AI Keeper cannot self-authorize a scenario rewrite.
- C07–C08: contaminated old summary/memory remain retrievable as conversation history but cannot override canonical quantity/location or authorize tools.
- C09: harmless sensory continuity remains available.
- C10: ordinary turn LLM request topology is unchanged; conditional correction review is measured separately.
- C11: an OOC correction mentioning “go upstairs” cannot mutate position before adjudication.
- C12: private facts are not projected publicly and change audience only through a committed reveal.
- C13: scenario reindex/content change invalidates a scenario ref without replaying committed mechanics.
- C14: a required fact or pending control survives a failed Narrator through deterministic delivery, without repeating the original tool.

## Non-goals and limits

Do not delete conversation history, shorten narration, remove ordinary sensory creativity, make summary a source of authority, grant player claims automatic canon, or add a fixed LLM reviewer. Provenance proves *which source was cited*; it does not by itself prove every free paraphrase is semantically entailed. Admit only source-verifiable content into typed authority, and keep the correction path for disputes. Deterministic hard-fact checks are intentionally partial.

## Final design confirmation

The remaining decision is the write/authority distinction for `record_established_fact` and `record_clue`: this draft keeps their recording behavior for play and natural-language corrections, but treats unsourced entries as `unverified` until they can be promoted from a verifiable source. This preserves flexibility without allowing an unsupported entry to authorize later mechanics. Confirm this distinction before implementation.
