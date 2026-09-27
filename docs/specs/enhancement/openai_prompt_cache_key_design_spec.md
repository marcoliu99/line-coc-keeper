# OpenAI prompt cache key for cross-turn prefix reuse

Status: implemented. Base: main_v2. Branch: enhancement/openai-prompt-cache-key.

## Problem and evidence

Measured 2026-09-27 over 125 recorded requests in `~/coc_v2_log` (2,301,394 input tokens, 1,044,405 reported as cached). Implicit prefix caching already works, at 45.4% overall, but it is distributed unevenly by position within a player turn:

| position | requests | input tokens | cached | hit rate |
| --- | --- | --- | --- | --- |
| first of a turn | 42 | 814,162 | 37,781 | 4.6% |
| second | 34 | 661,028 | 472,149 | 71.4% |
| third onwards | 49 | 826,204 | 534,475 | 64.7% |

The prefix itself is stable. `keeper._build_static_prompt` is documented as changing only when a scenario PDF is loaded, a character joins or leaves, or a sheet is edited, and the Anthropic adapter already marks `cache_control` on it. Within a turn, `previous_response_id` keeps successive requests on one cache node and the prefix is reused. A new turn opens a new chain, so the first request is routed without affinity and re-sends the whole ~17,378-token static prefix (static_system 7,851 plus tools 9,527) at full price. 776,381 tokens, 33.7% of all input tokens, are cacheable but uncached for this reason alone.

## Scope

Send `prompt_cache_key` on OpenAI `responses.create` so requests sharing a prefix are routed together across turns. The field exists in the installed SDK (openai 3.19.2, `openai/types/responses/response_create_params.py`): "Used by OpenAI to cache responses for similar requests to optimize your cache hit rates. Replaces the `user` field."

Key selection: reuse the already-hashed `conversation_id` from `observability.current_context()`. It is stable across turns for one game, distinct between games, and carries no player-identifying content, having passed through `_safe_identifier`. The provider signature is unchanged.

Not in scope: `prompt_cache_retention` (deprecated in the SDK), `prompt_cache_options` (ttl defaults to 30m and is the only supported value), any change to prompt composition or ordering, and any change to the Anthropic or Gemini adapters.

## Behaviour

`conversation_id` is bound only when logging is enabled. With no key available the parameter is omitted entirely rather than sent empty, because an empty or shared constant key would group unrelated games onto one cache entry. A key is never derived from `turn_id` or `request_id`: those change every turn, which would reproduce the current miss rate under a different name.

## Testing strategy

Offline, with a fake provider client; no paid API calls.

- A bound conversation context sends `prompt_cache_key` equal to the hashed conversation id.
- The same key is sent across two separate turns of one conversation, and differs between conversations.
- With no conversation bound, the parameter is absent from the request rather than empty.
- `turn_id` and `request_id` do not appear in the key.

## Verification

Cache hit rate is already recorded: `observability.usage_fields` normalizes `cached_input_tokens`, so no new instrumentation is needed. After deployment, re-run the per-position breakdown above and read the "first of a turn" row against the 4.6% baseline.

## Limits

This is verified as a routing and cost change, not as a rate-limit change. Whether cache-hit input tokens count fully toward TPM was not established here, so no rate-limit benefit is claimed. Cache affinity is a routing hint, not a guarantee; a miss remains correct behaviour and changes no output. The measurement window is one day of logs from a single deployment.
