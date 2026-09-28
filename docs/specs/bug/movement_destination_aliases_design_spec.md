# AI-adjudicated movement destination matching

## Problem and goal

The 2026-09-28 16:05 log shows `我去圖書館` -> `中央圖書館` and `我去老屋` -> `科比特宅邸` rejected as `movement_destination_mismatch`. Current-turn scenario search found Central Library and Corbitt House. A second probe using the player's literal `圖書館` as destination passed the player-name check but failed `destination_not_supported_by_evidence` because the retrieved text was English. These are string-matching failures before route validation. PR #99 added the strict arrival gate; PR #121 relaxed incomplete map topology but left both name checks in place.

Goal: let the **existing Executor** decide whether a destination is the place the player requested and whether its current-turn scenario evidence supports it. The operator can opt into this behavior without another LLM call, translation step, alias lookup, fuzzy scoring, or scene-map rebuild.

## Decision and scope

- Add `MOVEMENT_DESTINATION_MATCH_MODE=strict|ai` to `app/config.py` and `.env.example`. Default is `strict` for compatibility; unknown values fail configuration validation. The operator sets `ai` and restarts the bot to enable the new behavior.
- In `ai` mode, `commit_movement` skips only the two destination-name gates: `movement_destination_mismatch` and `destination_not_supported_by_evidence`. The Executor must still submit a destination, a player `source_span`, a current-turn evidence citation, and a clear/awaiting-check/blocked condition. Python validates the evidence source identity and quote rules already required by PR #121; it no longer tries to prove the semantic equivalence of the destination and either the player wording or the source text.
- Keep every other existing guard: exact current-player movement authorization, third-party/OOC/negation exclusions, pending check and Luck identities, explicit blocked conditions, known locks, required checks, reaction points, combat restrictions, map ambiguity, and arrival-before-effects ordering. A player merely mentioning or asking about a place must not move.
- Existing map resolution remains advisory as in PR #121. No map page or OCR edge becomes a prerequisite for a valid scene transition. Do not create intermediate rooms or events to narrate travel.
- Do not add tool fields or a new LLM stage. The same Executor tool call carries the judgment; Python still applies rule/state gates and records the selected mode plus accepted destination in a bounded diagnostic event. Do not log prompts or full retrieved passages.
- Return a distinct result when other gates block the move; the UI must not collapse a known refusal into a generic incomplete-turn message.

## Non-goals and explicit tradeoff

- This mode does not turn off all of PR #99. It relaxes the two name/relevance gates that produced the logged failures.
- In `ai` mode, Python cannot independently prove that `老屋` means `科比特宅邸` or that a cited passage actually supports a destination. The existing Executor may make a wrong semantic decision. This is the operator-selected tradeoff to avoid extra analysis and false rejections. `strict` remains available to reverse it.
- The Chinese RAG variant is a separate per-group choice. `/coc scenario use <scenario-id> <approved-variant-id>` already selects it. The log's `requested_variant=original` means this group used the original source. Switching to Chinese RAG alone cannot remove the player-name mismatch.
- No state migration, scenario reimport, background translation, per-turn review call, or change to scenario-canon prompts.

## Flow

```text
Player's current action -> existing Executor + current-turn RAG
      -> commit_movement(destination, source_span, evidence, conditions)
      -> exact player-action authorization + pending/obstacle gates
      -> evidence provenance/quote validation
      -> mode=strict: existing destination-name/relevance gates
         mode=ai: Executor's destination judgment accepted
      -> existing map/scene resolution and blocker checks
      -> commit arrival before destination-dependent effects
```

## Tests and acceptance

- Reproduce both logged turns at the real `commit_movement` seam. `strict` rejects with the current code; `ai` commits travel to Central Library and Corbitt House using the same tool arguments and current-turn citations. Confirm no additional provider request.
- In both modes, reject unsupported evidence source IDs, invalid non-RAG quotes, missing player authorization, question/hypothetical/negation/OOC/third-party movement, pending check/Luck, locked route, required check, reaction point, and ambiguous mapped destinations. These leave location unchanged.
- Verify scene travel with incomplete OCR map connectivity still works, and a room's explicit blocker is never bypassed by the switch.
- Unit-test config parsing/default/unknown value and document the exact `.env` setting. Run focused movement/executor tests, ruff, mypy, and the full suite.

## Open implementation detail

The existing `destination_not_supported_by_evidence` check also prevents arbitrary unknown destinations when `strict`. In `ai`, the Executor owns that semantic judgment. Keep provenance validation and actionable diagnostics so a wrong decision remains traceable, but do not reintroduce a hidden alias or similarity check that makes the opt-in mode reject these same turns.
