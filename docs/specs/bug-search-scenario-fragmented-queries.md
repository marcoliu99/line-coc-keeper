# Spec: search_scenario fragmented/repeated queries

## Changeset Tracking
- **main_v2 start**: origin/main_v2:923a8c437e07c6c4b28cfea84fde6a6c3d27c203
- **implementation end**: TBD

## Purpose & Scope

Real-log evidence (the same class of log this session's earlier "常常語塞
還有速度也有點慢" investigation and PR #56's latency work came from):
during a single scene involving the NPC 柯比特 (Corbitt), the Keeper called
`search_scenario` several times in a row for closely-related facts about
the *same* NPC/event instead of one broad query, e.g.:

1. `"柯比特、不死魔物、身體活動、Flesh Ward、護甲、魔法點、戰鬥輪……"`
2. immediately followed by `"Corbitt 使用 Dominate 的時機、第一個攻擊、目標、玩家反應、Flesh Ward……"`

Both queries are about the same ongoing encounter with the same NPC — the
second query's needs (Dominate timing, attack sequencing, player reaction
handling) were foreseeable at the time of the first query, since they're
all part of "what do I need to know to run this Corbitt encounter," not
information that only became relevant after seeing the first query's
results. Each `search_scenario` call is its own tool-calling loop
iteration (same iteration budget `MAX_TOOL_ITERATIONS`/
`HIGH_ITERATION_WATERMARK` from PR #56 track), so fragmenting one
information need into several small sequential searches directly burns
into that budget and adds latency, on top of just being redundant.

This is a prompt/tool-description issue, not an application-logic bug:
`_SEARCH_SCENARIO_TOOL`'s current description (`app/keeper.py:782-797`)
only says a search is *required* before inventing scenario content — it
gives no guidance on how broad a single query should be, or when it's
appropriate vs. wasteful to search again. Nothing currently discourages
the fragmented pattern above, and nothing currently prevents a model from
under-searching either (a hyper-strict "one query only" rule would risk
not retrieving enough context, which is exactly why this isn't just "cap
it at one call").

## Changes

- `app/keeper.py`: extend `_SEARCH_SCENARIO_TOOL`'s `description` (not the
  static prompt block — this is specifically about *how to use this one
  tool*, matching the precedent PR #58 set for `damage_combatant`/
  `apply_combat_damage`'s disambiguating text) with guidance to:
  - Think in terms of the **current event/encounter**, not just the one
    fact immediately missing — before calling, identify what else that
    same event will plausibly need (the NPC/creature/location involved,
    the current situation, related actions/behaviors, related spells/
    weapons/abilities, their prerequisites/costs/durations/limits,
    immediate consequences/follow-ups) and fold those into one focused
    query rather than issuing them as separate follow-up searches.
  - Stay bounded to the current event — do not search unrelated future
    scenes, secrets, or encounters the current moment doesn't depend on
    (avoids spoiling content prematurely and avoids pulling in
    unnecessarily large context).
  - After getting results back, use them carefully before searching
    again; only search again if something actually needed to resolve the
    current event is missing from what came back — not to double-check
    information already present.
  - Explicitly not a hard one-call limit: if one query genuinely doesn't
    return enough to resolve the current event, calling again is fine —
    the guidance is against *fragmenting one foreseeable information need
    into several small sequential calls*, not against multiple calls in
    general.
- Draft wording (Traditional Chinese, matching this codebase's existing
  prompt language) to append to the tool description, based on the
  user's reviewed draft:
  > 查詢時以「目前這個事件/場景」為單位思考需要哪些劇本資料，不要只查眼前缺
  > 的單一事實——呼叫前先想一想這個事件接下來可能還會用到哪些相關資訊（相關
  > 的 NPC/怪物/地點、目前情境與遭遇、可能的行動與行為模式、相關法術/武器/能
  > 力、使用條件與代價與限制、立即的後續發展），把這些一起包進同一次查詢
  > 裡，不要每個小問題都分開各查一次。查詢範圍要涵蓋這個事件需要的東西，但
  > 不要查到之後才會發生的場景、秘密或遭遇，避免劇透也避免資料過大。拿到查
  > 詢結果後，先仔細看有沒有涵蓋到目前需要的資訊，只有真的缺東西才再查一
  > 次；不要因為想再三確認已經查到的內容而重複查詢——但這不代表只能查一
  > 次，如果一次查詢真的不夠涵蓋這個事件所需的資訊，可以再查。

## Testing Strategy

- This is LLM tool-selection/query-formulation behavior, not application
  logic — same class of change as PR #58's tool-description fix. No
  meaningful mocked unit test can verify "the model bundled related needs
  into one query" (a unit test can only check the tool schema/description
  string itself, not whether it changes real model behavior).
- Before landing, verify via a real-API trial mirroring the Corbitt case:
  construct a scenario/NPC with several related facts (abilities, armor,
  a triggered special attack with its own conditions) and a single user
  message that plausibly needs more than one of those facts to resolve,
  then compare query count/content before vs. after the wording change —
  looking for the fragmented multi-query pattern to collapse into one (or
  fewer) broader queries covering the same event.
- Grep existing tests for the current `_SEARCH_SCENARIO_TOOL` description
  text before editing, to catch anything asserting on it verbatim.
- Standard four checks (ruff, mypy, compileall, pytest).

## Notes
- Affects every caller that offers `search_scenario` when
  `SCENARIO_RAG_ENABLED` — both `app/keeper.py`'s legacy `run_turn` path
  and `app/agents/executor.py`'s Supervisor/Executor path (via
  `keeper._tools_for_speaker_role`), since both source the same
  `_SEARCH_SCENARIO_TOOL` definition. No model/provider-specific tuning
  needed — this is a shared tool description, not something gated by
  `docs/specs/enhancement-executor-model-tiering-and-tool-scoping.md`'s
  per-provider model choice.
- Related but distinct from `docs/specs/enhancement-conversation-lock-
  and-tool-loop-latency.md`'s `MAX_TOOL_ITERATIONS`/
  `HIGH_ITERATION_WATERMARK` work — that spec raised the iteration budget
  and added an early-warning signal; this spec reduces how many
  iterations a single information need actually costs. Both target the
  same underlying "語塞"/slow-turn symptom from different angles.
