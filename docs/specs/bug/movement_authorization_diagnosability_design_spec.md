# Movement authorization, and being able to diagnose a blocked turn

[繁體中文](movement_authorization_diagnosability_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `3e2e95c`.

## Problem and evidence

The runtime log from 2026-09-28 05:03 (`20260928-050340-380799_runtime_profile-async.log`), a freshly started game: **three of the turns resolved `blocked`** across twenty-two requests, and the player saw the same sentence every time:

> 這次行動目前無法繼續。請先確認目前狀態或更正原本的行動。

All three carry `validation_code: validated` — **the validator did not refuse them**. The Executor declared itself blocked because its `commit_movement` call failed, and it failed for a different reason each time:

| turn_id (last 8) | player input | tool result |
| --- | --- | --- |
| `47e6cde6` | `直奔商店購買油燈跟煤油罐` | `commit_movement 失敗：no_player_movement_authorization` (`add_carried_item` succeeded in the same turn) |
| `2f123f01` | `跟煤油罐` | only `search_scenario`, which returned 「（沒有找到相關內容）」 |
| `af968e7f` | (ordinary text) | `commit_movement 失敗：unknown_map` |

This group is on the **good** scenario variant — not the empty-artifact case described in [`empty_scenario_location_index_design_spec.md`](empty_scenario_location_index_design_spec.md):

```text
scenario_variant_id : original      scene_maps keys : ['17']
location_index      : 8 entries     current_map_page: {}
```

## Cause 1: the authorization gate is defeated by clause splitting

With no existing proposal, `commit` in `app/services/movement.py` requires the model's `source_span` to be **exactly equal** to one of the clauses `intent_parser.movement_clauses()` produced, or it returns `no_player_movement_authorization`. Against that input:

```text
'直奔商店購買油燈跟煤油罐'
  movement_clauses() = ['直奔商店購買油燈跟煤油罐']   ← no comma, so one clause
  span='直奔商店'   substring True   in clauses False   ← refused
  span='直奔商店購買油燈跟煤油罐'  in clauses True
```

Two things compound:

1. **`直奔` is not in `_MOVEMENT_VERB_RE`** (`app/intent_parser.py:28`). `離開/進入/走進/走向/前往/進去/走到/穿過/移動到/走回/回到/走/去` are there; a whole family — 奔, 趕, 衝, 跑, 抵達, 來到 — is not.
2. With no comma the sentence does not split, so **the only admissible span is the whole thing, purchase included**. A model quoting just the movement part — the reasonable thing to do — is refused every time.

For contrast, `前往商店，購買油燈跟煤油罐` splits into two clauses with `has_movement_verb=True` and works. **The player changed one verb and omitted one comma.**

## Cause 2: `unknown_map`, with the arguments unrecorded

`movement.py:295` is `if page not in state.scene_maps: fail('unknown_map')`. This group's `scene_maps` holds only `'17'`, and retrieval that turn returned pages 6, 7, 8 and 17 together, so the model most likely passed a **page number from the scenario prose**.

**The log cannot confirm that.** `commit_movement`'s arguments are recorded nowhere: `llm.tool.completed` carries only `tool_name` and `duration_ms`, and `_describe_tool_call` keeps just the error code. This class of failure cannot be diagnosed after the fact, only guessed at — which is a gap worth closing first, or the next occurrence is another guess.

## Cause 3: one fallback sentence covering three different failures

`app/services/prompt_config.py:340-357`: a `blocked` turn with no pending check, no state change and no `scenario_evidence_blocked` falls through to that sentence. Three unrelated failures — an unrecognised verb, a page that is not on the map, missing scenario evidence — look identical to the player, and none of them says what to change.

## Scope

Four items, lowest risk first. **The arrival check itself is not relaxed**, and the empty-artifact path is untouched.

### 1. The missing movement verbs

Add `直奔|奔向|奔去|趕往|趕去|趕到|衝向|衝進|衝出|跑向|跑到|跑進|抵達|來到|返回|折返` to `_MOVEMENT_VERB_RE`.

`has_movement_verb` is documented as the cheap gate before a room-name match; a false positive costs one extra name match or RAG call and can never commit a move by itself — committing always requires the model to call `commit_movement` and pass the full validation. Erring wide here is therefore safe.

#### Review correction: erring wide attributes someone else's movement to the player

**That was not free, and the first version missed a path.** `propose()` takes the first clause carrying a movement verb, and `movement_clauses` only drops clauses that **open with** 他/她/有人 — not one with a named subject:

```text
怪物衝進地下室，我開槍
  clauses = ['怪物衝進地下室', '我開槍']
  propose() takes the first → a movement proposal for the player
```

`app/agents/executor.py:232` downgrades `resolved` / `resolved_without_check` / `no_mechanics` to `incomplete` with `arrival_not_committed` whenever a proposal exists and never arrived. So a player who fired one shot, and whose mechanics resolved, would be told 「這次行動尚未完整處理」 — **the exact reply this spec exists to remove.**

`衝進` made no proposal before it was added to the verb list, so this is a regression introduced here. The same shape already held for `走進`, which was always in the list — 「科比特走進客廳，我觀察」 — so that is fixed along with it.

Two layers:

1. `movement_clauses`' third-person prefix filter gains `牠|它|牠們|它們|某人`.
2. `propose()` skips a clause attributable to a third party (`movement._third_party_clause`): it takes the text **before** the movement verb, admits an empty prefix (verb-initial, 「直奔商店」) or one containing 我/咱/自己, and rejects only when the prefix names a known other actor — another player's character, a non-PC in the initiative order, a `scenario_npc_index` name or alias, all at least two characters — or matches a short list of generic stand-ins (怪物/影子/敵人/那隻…).

**Rejecting only on positive evidence is deliberate.** A spurious proposal breaks a whole turn; a missing one does not, because the Executor can still quote a `source_span` and let `commit_movement` adjudicate it.

### 2. Record every movement rejection

`movement.py` emits a `movement.rejected` event on **every** refusal path, carrying the error code, `page`, `destination`, the length of `path`, a truncated `source_span`, and the keys currently in `state.scene_maps`. At WARNING.

`source_span` and `destination` are player and scenario text that the log already contains (the router's `command_name` and the StateReducer's facts both carry the original wording), so this adds no new exposure; identifiers go through `observability.safe_identifier` as usual.

### 3. `source_span` may be a clause prefix

The exact-equality rule **stays**. One alternative is **added**:

```text
span is a prefix of some clause  AND  len(span) >= 4  AND  span itself carries a movement verb or a direction intent
```

Purely additive: everything that passes today still passes. The only newly admitted shape is "the model quoted the movement half of a compound sentence", and that half must read as a movement on its own. Mid-sentence fragments stay out — where a comma separates them, `movement_clauses` already splits them.

### 4. The blocked fallback says something actionable

`_record_check_status` records the last movement rejection code (`status['movement_blocked']`), and `enforce_mechanic_check_consistency` turns it into one actionable Chinese sentence:

The table as implemented (`prompt_config.MOVEMENT_BLOCKED_ADVICE`) holds ten codes rather than the four first sketched, because `_validate` has several more that are equally actionable:

| code | what the player sees |
| --- | --- |
| `no_player_movement_authorization` | 沒有看懂你要移動到哪裡；請把移動單獨寫成一句（例如「前往商店，然後購買油燈」）。 |
| `unknown_map` | 目的地不在這份劇本現有的樓層圖上；請先說明你從哪個已知地點出發。 |
| `map_entry_required` | 你們還沒有進入這張樓層圖；請先描述從入口進入，再往裡面走。 |
| `movement_evidence_missing` | 這次移動還沒有劇本原文可以佐證；請先描述你在現場看到什麼，或換一個劇本提過的地點。 |
| `destination_not_supported_by_evidence` | 劇本裡找不到這個地點；請改用劇本提過的地點名稱。 |
| `known_map_location_requires_path` | 這個地點在樓層圖上，要沿著房間之間的路徑過去；請說明你經過哪些地方。 |
| `disconnected_movement_path` | 從目前位置沒辦法直接到那裡；請先說明中間會經過哪些地方。 |
| `passage_blocked` | 這條路目前不通。 |
| `movement_prerequisites_unresolved` | 還有尚未完成的檢定；請先完成它，再繼續移動。 |
| `movement_waits_for_final_luck` | 你還有一個幸運選擇沒有決定；請先決定，再繼續移動。 |

An unmapped code keeps the existing sentence. **No disposition and no mechanical result changes** — only the wording. The precedence is unchanged: a pending check, a pending Luck decision and `scenario_evidence_blocked` all still come before the movement advice. A `commit_movement` that succeeds later in the same turn clears the code — that move did happen.

## Testing

- The new verbs: each is recognised, and `他右手邊有把刀` / `我拿走桌上的刀` still are not.
- `movement.rejected` fires on every refusal path and carries the code and the `scene_maps` keys; identifiers such as the scenario title are hashed.
- Prefix authorization: `直奔商店購買油燈跟煤油罐` with `span='直奔商店'` is admitted; `span='直'` (too short), `span='購買油燈'` (no movement intent) and `span='商店購買'` (not a prefix) are not; every exact-equality case that passes today still passes.
- The fallback: each mapped code produces its own sentence, an unknown code returns the original, and the existing `state_changed` and pending branches are unaffected.

Mutation-checked: removing the new verbs, the prefix branch, or the code table each has to turn a test red.

## Limits

This does not address **why** the model chose the wrong `page` — item 2 only makes the next occurrence findable. `map_entry_required` is also unchanged: a new party's first move onto a map still has to enter through the entry room, which is a separate question.
