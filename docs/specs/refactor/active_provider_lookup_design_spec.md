# One lookup for the active LLM provider

[繁體中文](active_provider_lookup_design_spec_zh.md)

Status: **backlog** (awaiting spec review). Base: `main_v2` at `a68df95`.

## Problem

PR #113 moved the provider maps into `app/providers/registry.py` (`CONVERSATION_PROVIDERS`, plus `ANALYSIS_PROVIDERS`, which excludes `codex`). The map is now shared, but **the lookup is still copied**: twelve call sites each take an alias of a map and index it with a module-level setting.

| Kind | Call sites | Lookup | Missing provider |
| --- | --- | --- | --- |
| Conversation | `keeper.py:66`, `agents/executor.py:33/47`, `agents/guard.py:13/24`, `agents/narrator.py:18/34` | `_PROVIDERS[LLM_PROVIDER]` | `KeyError` |
| Conversation | `agents/assistant.py:34` | `keeper._PROVIDERS.get(keeper.LLM_PROVIDER)`, reaching into `keeper`'s private alias | inline setup-error reply |
| Analysis | `pdf_ai_repair.py:15/76`, `pregen_extractor.py:26/238`, `scenario_compare.py:18/49`, `scenario_index.py:26/104`, `scenario_intro.py:22/105`, `scene_map.py:47/155` | `_PROVIDERS.get(LLM_PROVIDER)` | `None` → each returns its own empty result |
| Analysis | `keeper.py:3614` (summarisation) | `ANALYSIS_PROVIDERS.get(ANALYSIS_PROVIDER)` | `None` → keep the old summary |

Three concrete costs:

1. **A misleading name.** The six analysis modules `from app.config import ANALYSIS_PROVIDER as LLM_PROVIDER`, so `LLM_PROVIDER` means the conversation setting in one module and the analysis setting in the next. Reading a module no longer tells you which provider it uses.
2. **A private reach across layers.** `assistant.py` depends on `keeper._PROVIDERS`. This is one of the entries in PR #116's SLF001 debt list.
3. **Tests patch the copies.** About 100 test lines patch `keeper._PROVIDERS`, `executor._PROVIDERS`, `narrator._PROVIDERS`, `repair._PROVIDERS` or a module's `LLM_PROVIDER`. The save/patch/restore sequence around `keeper._PROVIDERS["openai"]` alone is repeated about a dozen times. A new call site means a new thing to patch.

## Decision

The registry owns the lookup, and callers ask it by role:

```python
# app/providers/registry.py
def conversation_provider() -> ConversationProvider | None: ...   # config.LLM_PROVIDER
def analysis_provider() -> AnalysisProvider | None: ...           # config.ANALYSIS_PROVIDER
```

- Both read `app.config` **at call time** (`config.LLM_PROVIDER`), not a value copied at import, so tests set the provider in one place.
- Both return `None` for an unknown name. **Callers keep their existing fallback behaviour.** The four conversation modules that index with `[...]` today switch to the same explicit `None` check `assistant.py` already does, so each one raises or replies with a clear setup error instead of a bare `KeyError`. This is the only behaviour change, and it only affects a misconfigured `.env`.
- Provider-specific branches that aren't lookups (`assistant.py:103` `keeper.LLM_PROVIDER == "openai"`) read `config.LLM_PROVIDER` directly and stop going through `keeper`.

## Scope

1. Add the two functions to `registry.py`. `ANALYSIS_PROVIDERS` stays derived from `CONVERSATION_PROVIDERS`.
2. Replace all twelve call sites. Delete the module-level `_PROVIDERS` aliases and the `ANALYSIS_PROVIDER as LLM_PROVIDER` imports. Modules that still need the setting name for logging import it under its real name.
3. Tests: add one helper, `tests/provider_fakes.py::use_fake_provider(fake, *, role="conversation", name="openai")`. It patches `app.config` and `patch.dict`s the registry map, and it replaces the hand-written save/restore blocks. Rewrite the existing patches onto it mechanically; test assertions are unchanged.
4. Remove `app/agents/assistant.py` from the SLF001 debt list in `pyproject.toml` if it has no other private access left (it currently has others, so the entry probably stays).

## Testing

- The full suite passes after the mechanical rewrite with **no assertion changes**. That shows the refactor preserves behaviour.
- New: `conversation_provider()` / `analysis_provider()` return `None` for an unknown name, and `analysis_provider()` never returns `codex`.
- New: each conversation entry point with an unknown `LLM_PROVIDER` produces the setup-error path, not a `KeyError`.
- `git grep -n "_PROVIDERS\s*=" app` returns only `registry.py`.

## Limits

- Provider *capabilities* (`supports_dynamic_tools`, `supports_response_stage`) are already in the registry and are untouched.
- The largest cost is test churn, not production code. If that's judged not worth it now, steps 1–2 can land alone by keeping the old aliases as thin deprecated wrappers, but that's the halfway state this spec is trying to leave.
