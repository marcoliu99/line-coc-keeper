# Logging delivered Discord replies

[繁體中文](enhancement-discord-reply-text-logging_zh.md) | [Docs index](../../README.md)

## Status and scope

Category: `enhancement`. Status: **implemented**. Audited against `main_v2` at `afe8ace` (2026-09-27).

This edition describes the current contract. Proposed work is explicitly identified; historical source text is linked below.

## Current contract

1. Record public reply text through the text-log channel so incidents can be matched to what players saw. LOG_TEXT_ENABLED controls this independently from structured metrics.

2. Delivery status must reflect actual send results, including failures and split messages. Do not report an unsent reply as completed merely because generation finished.

3. Structured performance events retain bounded fields and identifier policy. Free-form player/scenario text belongs only in the separately configured text channel.

## Flow and interfaces

```text
Render/split reply -> send successfully -> text log and delivery metrics
```

## Implementation and verification

The linked implementation and existing regression tests are the audit evidence. Test names and exact payload schemas in source resolve implementation detail. Historical test counts and API trials are not current performance guarantees.

- [app/discord_bot.py](../../../app/discord_bot.py)
- [app/logging_config.py](../../../app/logging_config.py)
- [tests/test_discord_reply_text_log.py](../../../tests/test_discord_reply_text_log.py)
- [tests/test_logging_completion.py](../../../tests/test_logging_completion.py)

## Historical evidence

The immutable original retains the incident narrative, migration chronology, draft examples and historical measurements. Those sections may describe superseded behavior; the current contract above takes precedence.

[Original source](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/specs/enhancement-discord-reply-text-logging.md)
