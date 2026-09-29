# Scenario retrieval execution

[繁體中文](scenario_retrieval_execution_design_spec_zh.md)

Status: **backlog — conditional on the depth gate below; awaiting spec review**. Base: `main_v2` at `7cef87c` (2026-09-29).

## Problem and current evidence

`scenario_templates.search_for_state` already shares the core authorized Chinese/original search, chapter scope, incomplete-evidence handling, and continuation binding. Do not extract that search again. Its two gameplay callers still repeat or distribute request orchestration: `app/agents/context_builder.py` prepares proactive principal and prompt budget, context variables, retrieval metrics, semantic-only acceptance, and formatted prompt text; `app/keeper_tools/scenario.py` prepares explicit source/continuation/principal, metrics, completeness, record IDs, and continuation tokens. `app/agents/executor.py` sets a separate explicit-search budget context. A third caller, correction adjudication, uses its own fixed principal and must keep its evidence policy.

The goal is a deep execution module that makes shared request identity, budget application, diagnostics, and evidence-completeness interpretation local while preserving the callers' different policies and the already-shared search implementation.

## Depth gate before implementation

First write a concrete call-site inventory for proactive context, explicit Keeper tool, Executor budget setup, and correction adjudication. Record which responsibilities are actually duplicated and which vary intentionally. Proceed with a new module only if one small interface can own **all three** shared concerns—principal/continuation identity, budget/context lifetime, and normalized result completeness/diagnostics—without forcing callers to pass the entire prompt or a large policy dictionary. The deletion test must be satisfied: removing it would return meaningful knowledge to multiple callers. If the inventory shows only formatting or metrics duplication, narrow or stop this refactor and report that decision rather than adding a pass-through module.

## Scope and interface if the gate passes

- Use an explicit request mode for proactive context or explicit Keeper search. Give the module the current authorized state, query, caller identity and necessary budget inputs. It invokes the existing `search_for_state` once per current policy and returns a structured retrieval result containing formatted evidence, provenance, completeness, diagnostics, and continuation data appropriate to that mode.
- Centralize setup/reset of `scenario_retrieval.BUDGET` and `MODEL` context variables. Preserve the conservative byte fallback and `budget_tokens=0` behavior. Budget calculation must still include current prompt, tools, provider history, output reserve, and safety margin; it must not increase the context ceiling or silently remove required facts.
- Preserve the principal binding to group, timeline, variant, permitted chapter window, source, query, and history. A continuation token from another principal, changed timeline, or stale source must remain invalid.
- Proactive context accepts a successful semantic source only and may skip lexical fallback in its prompt. The explicit tool may use BM25 and accepts `source=original` plus a valid continuation. The shared `search_for_state` Chinese-to-original supplementation remains intact, including the case of hits whose required armor, attacks, abilities, triggers, costs, or limits are still incomplete.
- Keep source authorization and complete-for-action claims conservative. A hit alone does not prove complete evidence; absent or incomplete material must retain its current markers and allow Executor to supplement from original source. Maintain current logging separation: free-form queries stay in text-gated logs, structured events contain bounded diagnostics only.
- Keep correction adjudication on its current evidence path unless the call-site inventory proves that the same request identity and completeness rules apply. Do not make a correction report into a game-turn prefetch.

## Data and compatibility

No database, template schema, source record, provider, or prompt policy changes are planned. Existing result text, evidence record IDs, continuation tokens, and metrics must match before and after for identical state/query/mode inputs. The new module does not translate queries, add an LLM call, or change the chapter window.

## Flow

```text
Proactive context policy ─┐
                          ├─> retrieval execution module
Explicit search policy ───┘      -> request identity + scoped budget + diagnostics
                                 -> existing search_for_state
                                 -> normalized evidence/completeness result
```

## Non-goals

Do not rebuild indexes, change ranking, alter original-source fallback, merge the proactive and explicit acceptance policies, automatically translate during play, or claim a latency improvement from code movement alone.

## Verification

1. Capture before/after fixtures for original-only, complete Chinese, zero-hit Chinese, partially complete Chinese, missing required enemy details, expired continuation, wrong principal, chapter change, and tokenizer byte fallback. Assert identical evidence text/order, IDs, completeness, source labels, and continuation behavior.
2. Assert proactive lexical fallback remains excluded while explicit search can use it; no required evidence is silently omitted. Test budget exhaustion and correct context-variable reset after exceptions and concurrent searches.
3. Instrument identical cases for LLM call count, RAG calls, budget, retrieval latency, and full-turn latency. No new LLM request is allowed; latency is measured rather than promised to improve.
4. Keep `tests/test_retrieval_prefetch.py`, `tests/test_retrieval_readiness.py`, `tests/test_scenario_query_fallback.py`, and `tests/test_scenario_template_units.py` green. Run full pytest, Ruff 0.16.8, `mypy app`, and `python -m compileall app tests` after implementation.

## Review decision

Marco confirmed an independent branch, preserved fallback differences, output equivalence, zero additional LLM calls, and a real depth gate. The existing shared search is an asset to retain, not a reason to add another wrapper. Implementation begins only after the call-site inventory passes the gate and this spec is reviewed.
