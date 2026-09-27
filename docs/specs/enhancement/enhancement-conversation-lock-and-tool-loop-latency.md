# Conversation queuing and bounded tool iteration

[繁體中文](enhancement-conversation-lock-and-tool-loop-latency_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Signal Discord typing and acknowledge lock waits. Serialize state-sensitive turns per conversation without making the event loop wait on synchronous provider I/O.

2. MAX_TOOL_ITERATIONS defaults 5 and HIGH_ITERATION_WATERMARK defaults 4. The watermark is diagnostic, distinct from the hard iteration budget.

3. Executor now returns a validated handoff with enable_wrapup=False. Generic provider wrap-up remains supported for callers that enable it; do not add a fixed review request to every turn.

4. Macro combat initialization and general dynamic tool scoping remain separate backlog items. Status snapshot gating alone does not implement those designs.

## Flow and interfaces

```text
Incoming action -> typing/queue notice -> conversation lock -> bounded tools -> result delivery
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/locks.py](../../../app/locks.py)
- [app/commands/router.py](../../../app/commands/router.py)
- [app/discord_bot.py](../../../app/discord_bot.py)
- [app/providers/openai_provider.py](../../../app/providers/openai_provider.py)
- [app/config.py](../../../app/config.py)
- [tests/test_conversation_lock_with_notice.py](../../../tests/test_conversation_lock_with_notice.py)
- [tests/test_llm_turn_wrapup.py](../../../tests/test_llm_turn_wrapup.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-conversation-lock-and-tool-loop-latency.md)
