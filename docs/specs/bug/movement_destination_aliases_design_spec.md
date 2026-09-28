# Source-grounded movement destination aliases

## Problem and goal

In the 2026-09-28 16:05 log, `我去圖書館` with destination `中央圖書館` and `我去老屋` with destination `科比特宅邸` were rejected as `movement_destination_mismatch`. Current-turn search returned the Central Library and Corbitt House passages. The validator requires the model's destination string to appear literally in the player's movement clause, then requires that same string to appear in the source or map. This rejects ordinary short names and Chinese/English aliases before the movement graph is considered. PR #99 introduced the strict arrival gate; PR #121 relaxed incomplete map topology but retained these two string gates.

Goal: admit explicit player travel when a different name for the *same* scenario location is grounded in current-turn evidence, without admitting an unrequested or unsupported arrival. Do not rely on unrestricted string-similarity scoring: `老屋` and `科比特宅邸` have no useful lexical overlap, while unrelated locations can share words.

## Scope and decision

- Preserve the existing strict behavior by default. Add an opt-in environment setting `MOVEMENT_DESTINATION_MATCH_MODE=source_bound` (`strict` is the default). Document it in `.env.example`; reject unknown values at startup. This is the temporary switch the operator can use to loosen destination-name matching. It does **not** disable movement authorization, canonical obstacles, pending checks/Luck, or source provenance.
- In either mode, require an exact span of the current player's own movement clause and preserve the existing third-party, negation, hypothetical, and OOC exclusions.
- In `strict`, keep the current lexical destination and evidence checks, including the existing map-label path.
- In `source_bound`, first try the strict checks. If they fail for a scene transition, accept a model-supplied `player_destination` that occurs in the authorized movement span and a `source_destination` copied from a registered current-turn RAG passage. The source passage must also be cited by the movement evidence and contain the asserted `source_destination` as a complete normalized phrase, not merely a common substring. Resolve all three names to **one** indexed location record: `destination` equals its canonical name or alias, `source_destination` equals another name or alias, and `player_destination` equals a name/alias or an unambiguous short form of one. Alternatively, a single verified bilingual source passage may explicitly identify all three as the same place. Reject multiple candidates. The model can explain a cross-language alias in the existing response; Python verifies the names, source identity, and uniqueness without another LLM call.
- Allow a lexical short name such as `圖書館` within `中央圖書館` when its boundaries are unambiguous and the full destination is source-grounded. The cross-language case must use the explicit source-name fields above; broad edit-distance matching alone is insufficient.
- Prefer indexed `aliases` where present. If no reliable indexed match exists, source-bound acceptance requires one cited source to explicitly bind the player name, canonical destination, and source destination to the same place. A free model assertion of equivalence is not sufficient. If the available English passage has only `Corbitt House` while the canonical destination is `科比特宅邸`, and no indexed alias binds them, return a typed `movement_alias_unverified` result and ask for a more precise name or a source-backed alias; do not silently teleport.
- Keep the destination committed as the resolved canonical name, and record the player name, source name, and resolution basis in a bounded diagnostic event. Do not log whole passages or prompts.
- Show distinct actionable feedback for name mismatch versus unsupported source versus unverified alias. Do not turn a rejected turn into a generic incomplete-turn message.

## Non-goals

- No toggle for all of PR #99's movement checks; it would also bypass requested-action and arrival ordering protections.
- No new per-turn LLM translation or review call, fuzzy edit-distance threshold, global alias dictionary, or automatic PDF reprocessing.
- No change to `/coc scenario use <scenario-id> <variant-id>`; choosing an approved Chinese RAG variant already exists and is independent of destination validation. The log's `requested_variant=original` means no Chinese variant was selected for that game.
- No new scene, room, route, encounter, or item inferred from the retrieved passage.

## Interface and state

- Extend `commit_movement` tool schema with optional `player_destination` and `source_destination`; old calls stay valid. Python derives and validates both against the existing player span and current-turn registered evidence. The fields do not become canonical state.
- `MOVEMENT_DESTINATION_MATCH_MODE` is process-level configuration, default `strict`; changing it requires a bot restart. Existing saved games need no migration.
- `scenario_location_index` already stores `name` and `aliases`. Use it as a bounded candidate set, not as permission to travel without an explicit current action and evidence.
- The selected scenario variant remains per-group state (`scenario_variant_id`), not this process-level switch. An approved Chinese variant may improve retrieval but cannot by itself prove `老屋` equals `科比特宅邸`.

## Flow

```text
Player movement clause -> exact source-span authorization
       -> pending check/Luck and explicit blocker gates
       -> strict destination + source checks
            | pass -> existing map/scene arrival validation
            | fail and mode=strict -> typed rejection
            | fail and mode=source_bound
                 -> player name in authorized span?
                 -> source name in cited current-turn source?
                 -> unique canonical/index/map/source binding?
                      | no -> typed rejection, no state change
                      | yes -> existing map/scene arrival validation
       -> commit arrival before destination-dependent effects
```

## Tests and acceptance

- Reproduce the two logged requests at the real `commit_movement` seam: strict mode rejects them with the current codes; source-bound mode admits them only when a verified alias binding exists. Add fixtures with index aliases or bilingual source binding and assert the committed location.
- A source-only English passage with no verified binding does not license the Chinese canonical name; it returns `movement_alias_unverified` without state change.
- Unrelated RAG hits, stale source IDs, fabricated source names, multiple candidate locations, player questions/negations/OOC, third-party movement, pending checks/Luck, locked routes and reaction points remain blocked in both modes.
- A literal supported destination follows the original path and needs no extra model request. Count existing executor calls in a mocked integration turn; no fixed LLM stage is added.
- Run focused movement/executor tests, config tests, ruff, mypy, and the full suite. The original isolated log-replay test must turn green for the cases with verified bindings.

## Open questions / tradeoffs

- Inspect the actual group's `scenario_location_index` and approved Chinese variant before implementation. The provided log proves `original` was selected, but does not show whether the index has `老屋`/`科比特宅邸` aliases. If neither the index nor a bilingual passage binds the names, the safe resolver must ask for clarification. A broader operator override would trade scenario fidelity for convenience and needs a separate explicit decision.
- A source-bound mode may admit a mistaken model alias when the indexed alias itself is wrong. The index remains the source of that mapping, so diagnosis should include the index record identity and offer a correction path.
