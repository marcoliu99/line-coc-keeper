# Discord and internal interfaces

[繁體中文](API_zh.md)

## Entry points

The application exposes a Discord gateway adapter, not a public REST API. Start it with `python3 -m app.discord_bot`. `app/discord_bot.py` handles events, attachments, DMs, images and persistent buttons. `app/commands/router.py` dispatches normalized commands and ordinary messages to handlers or agents.

## Agent interfaces

`supervisor.run_turn` handles `player_action`, `resolved_check_followup` and `opening_fallback`. KP Assistant is independent in `app/agents/assistant.py`. All authority tools reuse `keeper._execute_tool`; `keeper.run_turn` no longer exists. The command router also handles purchase/correction commands and KP sudo with actor/subject separation. Runtime callbacks enforce role, ownership and timeline constraints.

## Help and generated references

`app/help_registration.py` initializes the Help registry; `app/help_actions.py` maps every entry to an executable action. Forms collect free text, lists select server-known resources and confirmations precede configured consequential actions. Regenerate the canonical Chinese command reference with:

```bash
python3 -m app.help_docs --output docs/references/player_command_reference_zh.md
```

The English reference preserves command tokens and explains their usage. Registry changes must update both references.

## Configuration authority

Use `app/config.py` for actual defaults and `.env.example` for deployment configuration. Main groups are provider keys/models; SQLite and scenario/import directories; RAG, embeddings and optional prewarm; retry/timeout/deadline/admission; history/output budgets; logging; spoiler/privacy/Guard policy; backups and digest cadence. Do not copy historic spec defaults over current configuration.
