# Spec: search_scenario fragmented/repeated queries

## Changeset Tracking
- **main_v2 start**: origin/main_v2:923a8c437e07c6c4b28cfea84fde6a6c3d27c203
- **implementation end**: bug/search-scenario-fragmented-queries:ad3f0fc — ruff/mypy/compileall/pytest all green

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

## Real-API verification

Two rounds, both against real OpenAI calls (real cost each time).

### Round 1 — synthetic corpus (superseded, see round 2)

First attempt used a small hand-written 5-chunk corpus about a fictional
NPC "赫爾曼" with a crude keyword-overlap retrieval mock standing in for
`app/scenario_rag.py`. Initial run (missing an "attacks" chunk from the
corpus by mistake) showed OLD description → 3 search calls, NEW → 2. Once
the missing chunk was added, **both configs converged to exactly 1
call each** — the earlier 3→2 gap was fully explained by the corpus's own
missing data (both configs kept fruitlessly searching for a fact that
genuinely wasn't there), not by the wording change. This round is kept
here as a documented negative result, not deleted: a 5-6-chunk synthetic
corpus with `top_k=5` is too small/easy to distinguish "the model bundled
its queries well" from "there was nothing left to search for" — not
powerful enough to validate or refute the hypothesis either way.

### Round 2 — real production scenario data, both real agent configs

Used the actual `app/scenario_rag.build_index`/`search` pipeline (real
BM25 + real OpenAI embeddings, not a mock) against the real scenario text
already on disk in this deployment: `data/scenarios/the-haunting-
scenario-trimmed-81eeddbe/scenario.txt` (75,125 chars → 260 chunks,
`has_embeddings=True`). This is "The Haunting," a real published COC
scenario whose villain NPC, Walter Corbitt, is almost certainly the real
NPC behind this project's original "柯比特" log evidence — the scenario
text confirms his stats (Flesh Ward armor, a Dominate spell variant, a
floating-dagger attack) are genuinely scattered across several
non-adjacent pages, exactly the shape described in that original log.

Trigger message: `"戰鬥中，柯比特（Corbitt）這時候現身加入戰局，朝你逼近。"`
— requires gathering his HP/armor/attacks/abilities to call
`add_npc_to_combat`, the same real action shape as the original bug.
Tested **both** real agents that offer `search_scenario`, each at this
deployment's actual real config (`app/config.py`):

| Agent | Config | OLD description | NEW description |
|---|---|---|---|
| Keeper (`app/keeper.py`'s legacy `run_turn`) | `gpt-6-luna`/medium | **8 calls, hit max_rounds without ever committing to `add_npc_to_combat`** | 3 calls, converged cleanly |
| Executor (Supervisor/Executor path) | `gpt-6-luna`/none | 3 calls | 2 calls |

**The Keeper/legacy result is a direct reproduction of this project's
original "語塞" iteration-exhaustion bug**, not just a slow/redundant
search pattern: under the OLD description, across 8 rounds the model
never converged on a final answer, and — notably — two of its
intermediate queries stated *different, self-contradictory* stat guesses
for the same NPC (round 2: `"STR 35 CON 55 SIZ 35 POW 50 DEX 70"`; round
5: `"STR 90 CON 115 SIZ 55"`), i.e. it wasn't just repeating searches, it
was actively hallucinating different numbers between them while failing
to settle on the real ones. Under the NEW description, the same model/
config converged cleanly in 3 rounds with a coherent, single set of
stats.

This is real, production-representative evidence (not a synthetic
corpus's artifact) that the wording fix reduces fragmented `search_
scenario` calls for **both** agents that use it, and for the Keeper path
specifically prevents a real occurrence of the iteration-exhaustion
failure mode this project has already spent significant effort
mitigating elsewhere (`MAX_TOOL_ITERATIONS`/`HIGH_ITERATION_WATERMARK`,
docs/specs/enhancement-conversation-lock-and-tool-loop-latency.md).

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
