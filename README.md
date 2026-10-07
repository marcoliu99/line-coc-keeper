# COC7e Keeper Bot for Discord

**English** | [繁體中文](README_zh.md)

Upload a *Call of Cthulhu, Seventh Edition* (COC7e) scenario PDF, or a UTF-8 Markdown scenario whose filename starts with `scenario`, to a Discord channel and let an LLM act as the Keeper. Players describe their actions in chat; the Keeper reads the scenario, narrates events, and decides when checks are needed. Python code handles dice rolls, skill checks, sanity, character resources, and combat state.

The bot supports Anthropic Claude, Google Gemini, OpenAI, and the authenticated Codex CLI. Conversation and document-analysis providers can be selected independently. Codex handles conversation and general text analysis; PDF/image/OCR and pre-generated character-card analysis require an API provider because Codex's measured extraction accuracy was insufficient. See the [setup guide](docs/guides/setup.md) and [Codex OAuth guide](docs/guides/codex_oauth_testing.md).

## Quick start

### Setting up the bot

Follow [Installation and setup](docs/guides/setup.md) to create a Discord application, configure permissions and `.env`, and start the bot locally.

### Playing with an existing bot

1. **Upload a scenario.** Attach a COC7e scenario PDF, or a UTF-8 `.md` file whose filename starts with `scenario`, to the channel. Markdown is ingested directly without PDF/OCR processing; image-heavy PDFs take longer to process. The bot acknowledges the upload before returning the extracted content. Complete any scenario-selection or replacement prompts it presents.
2. **Create a character.** Each player can use:

   ```text
   /coc pc Alex
   ```

   The command also accepts an optional occupation: `/coc pc <name> [occupation]`. Use the occupation labels shown by the bot. For interactive character creation or a scenario's pregenerated investigators, see the [gameplay guide](docs/guides/gameplay.md).
3. **Start the game.** Once at least one investigator is ready, use `/coc start`. The bot uses the scenario's read-aloud opening when available; otherwise, it generates an opening grounded in the scenario. Ordinary chat does not advance the story before the game starts.
4. **Describe your actions.** Write what your character does in ordinary channel messages. The Keeper narrates, requests checks, and updates game state through tools.
5. **Check commands and progress.** Use `/coc help` for categorized help, `/coc status` for session progress, and `/coc sheet` for your character sheet. See the [player command reference](docs/references/player_command_reference.md) for commands to copy, and the [gameplay guide](docs/guides/gameplay.md) for detailed rules and workflows.

## Architecture

```text
Discord channel
  -> app/discord_bot.py
  -> app/commands/router.py
       |
       +-- Commands / scenario uploads
       |     -> app/commands/handlers/*.py
       |     -> app/services/{scenario_ingestion,map_service,character_service}.py
       |
       +-- Ordinary player messages
       |     -> app/agents/supervisor.py
       |          -> context_builder.py (state, scenario RAG, memory)
       |          -> intent_router.py (rule-based routing)
       |               +-- Gameplay -> executor.py -> real tool updates
       |               |                -> validated TurnResolution
       |               +-- Pure roleplay -> skip Executor
       |          -> narrator.py
       |          -> rule_validator.py / guard.py
       |          -> spoiler and next-action consistency checks
       |          -> response and recipient-scoped outputs
       |
       +-- Resolved checks / Luck follow-ups / opening fallback
       |     -> supervisor.py with an explicit turn kind
       |     -> narrator.py with tools restricted to that entry
       |     -> validation and response (no reroll of resolved checks)
       |
       +-- KP Assistant discussion
             -> supervisor.py -> assistant.py
             -> independent provider/tool/guard/commit path

Shared services:
  app/prompt_builder.py          Static and dynamic Keeper prompts
  app/tool_dispatch.py           Tool dispatch, shared gates, per-speaker tool lists
  app/turn_commit.py             The one transaction that commits a turn's log entries
  app/memory_maintenance.py      After-reply rolling summary, scene digest, memory chunks
  app/keeper_tools/registry.py    Ordered tool schemas, capabilities, handlers
  app/services/turn_context.py   Current-state and historical projections
  app/services/turn_resolution.py  Deterministic handoff validation
  app/scenario_rag.py            Scenario retrieval
  app/memory_rag.py              Retrieval of older conversation history
  app/providers/*_provider.py    Anthropic / Gemini / OpenAI / Codex adapters

Persistence:
  data/coc_bot.db                Session state, character indexes, RAG caches
  data/groups/                  Extracted scenario page images
  data/scenarios/               Reusable scenario library
```

### Commands and message routing

- **`app/discord_bot.py`** maintains the Discord gateway connection and forwards events to the command router. What it sends or builds lives in **`app/discord_transport/`** (`gateway`, `delivery`, `interactions`, `controls` for the persistent Check/Luck/PDF buttons, `help_ui` for the Help views); the persistent buttons' `custom_id` formats are pinned by `tests/test_discord_custom_id_contract.py`.
- **`app/commands/router.py`** is the platform-independent routing entry point. It dispatches commands to domain handlers and ordinary messages to the Supervisor.
- **`app/commands/handlers/`** separates character, combat, system/scenario, and map commands into `character.py`, `combat.py`, `system.py`, and `map_handler.py`. Handlers reuse existing command logic where appropriate.
- **`app/services/scenario_ingestion.py`**, **`map_service.py`** and **`character_service.py`** hold what used to live in the single `app/legacy_commands.py` module (deleted in phase 4 of the [architecture refactor](docs/specs/refactor/architecture_refactor_phases_1_4_design_spec.md)): PDF / Markdown scenario upload orchestration and pregen merging, map uploads and movement resolution, and the claim / away-back / heal / readiness rules. Check and Luck resolution is in `app/checks`, battle actions in `app/services/combat_engine.py`. Resolved-check narration enters the unified Supervisor flow rather than a separate `keeper.run_turn` path.

### Agentic Keeper and the unified turn flow

- **`app/agents/supervisor.py`** coordinates player turns in Python. It handles ordinary actions, resolved-check follow-ups, and opening fallback through explicit entry modes, reconciles authoritative state, and commits the resulting narration.
- **`app/agents/context_builder.py`** gathers scenario and memory context. Scenario retrieval is gated by `SCENARIO_RAG_ENABLED`; proactive scenario and memory retrieval are skipped during active combat. Retrieval can use embeddings, so it is not necessarily an entirely local operation.
- **`app/agents/intent_router.py`** uses rules rather than an LLM call to classify ordinary messages as `OOC_ASSISTANT`, `PURE_ROLEPLAY`, or `GAMEPLAY_ACTION`.
- **`app/agents/executor.py`** and **`tool_gateway.py`** handle gameplay mechanics using `keeper_tools.registry.TOOLS` and `tool_dispatch.execute_tool`. Tools perform real calculations and persist real changes. The existing final completion carries a structured decision to the validation layer.
- **`app/services/turn_resolution.py`** checks that decision against current state and observed tool effects. An Executor claim alone does not prove completion. Deferred and cancelled decisions cannot hide unrelated committed mutations; unvalidated free-form reasons are excluded from Narrator authority.
- **`app/agents/assistant.py`** is the independent KP Assistant agent for out-of-character discussion. It owns its provider, tool, guard, and history path while preserving the rules for explicitly established canonical events.
- **`app/agents/narrator.py`** produces player-facing narration. Ordinary gameplay narration has no tools. Resolved-check follow-ups and opening fallback receive restricted tool sets suitable for those entry modes.
- **`app/agents/rule_validator.py`** and **`guard.py`** check narrative formatting and prohibited system wording. When enabled, the Guard attempts up to two repairs only after validation fails, then falls back if the output remains invalid. There is no fixed extra LLM review call on every turn.
- **`app/services/turn_fallback.py`** names why a gameplay turn could not complete (twelve reasons, one `turn.fallback` event per occurrence) and runs the single bounded recovery: at most one extra scenario search and one extra Executor decision, and never when it could apply anything twice.
- **`app/services/event_obligations.py`** and **`app/agents/obligation_gate.py`** settle what the scenario explicitly attaches to an event (a SAN loss, damage, a forced check) in the same turn the narration discloses it, after the Guard and before the reply is finalized.
- **`app/presentation.py`** is what players read: Chinese tier and difficulty labels instead of the engine's English values, internal ids removed, and the real party size (`DEBUG_SHOW_INTERNAL_IDS` keeps ids for debugging).
- **`app/services/turn_phases.py`** records where a turn's time went (queue, retrieval, Executor, tools, Narrator, memory) without counting overlapping work twice, and writes the one-line `turn.summary` log entry for every turn a player waited for.
- **`app/agents/state_reducer.py`** is a recording stage, not a second mutation stage: Executor tools have already applied and saved their changes.
- **`app/services/prompt_config.py`** builds stage-specific prompts around the shared Keeper instructions and enforces consistent next-action instructions.
- **`app/domain/models.py`** defines internal messages and results, including `AgentMessage`, `MechanicResult`, `StateDelta`, and `TurnResolution`.

See the [unified Keeper turn-flow specification](docs/specs/refactor/unified_keeper_turn_flow_design_spec.md) for the current entry points, the [turn-consistency specification](docs/specs/bug/log_backed_turn_consistency_design_spec.md) for authoritative handoffs, and the [original Agentic Keeper design](docs/specs/refactor/agentic_keeper_design_spec.md) for the design history.

### Shared game logic and infrastructure

- **`app/keeper_tools/registry.py`** declares every Keeper tool once — its JSON schema and its capability flags (read-only, KP-assistant-allowed, creates a check, ...) — as one `ToolSpec` each in `REGISTRY`, in the order sent to providers. Consumers derive their name sets from it (`docs/specs/refactor/keeper_tool_registry_design_spec.md`) instead of keeping their own literal copies.
- **`app/prompt_builder.py`**, **`app/tool_dispatch.py`**, **`app/turn_commit.py`** and **`app/memory_maintenance.py`** replace the former `app/keeper.py` hub (see the [split specification](docs/specs/refactor/keeper_module_split_design_spec.md)): provider-independent prompt assembly, the shared tool admission gates and dispatch, the authoritative turn commit, and the after-reply log maintenance. Every registered tool has an explicit `ToolSpec.handler` in `app/keeper_tools/<family>.py`, which share `app/keeper_tools/support.py` and may not import the dispatcher; `tool_dispatch.execute_tool` dispatches to the handler after the shared gates.
- **`app/providers/anthropic_provider.py`** adapts the Anthropic Messages API, including prompt caching. **`gemini_provider.py`** and **`openai_provider.py`** provide the Google GenAI and OpenAI integrations; **`codex_provider.py`** uses the authenticated Codex CLI for conversation and general text analysis. `ANALYSIS_PROVIDER` selects PDF/image/OCR and pre-generated character-card analysis from API providers; Codex is intentionally excluded because measured extraction accuracy was insufficient.
- **`app/locks.py`** provides per-conversation locking to prevent overlapping messages from overwriting saved state, with priority handling for KP Assistant messages.
- **`app/combat.py`** manages initiative, rounds, combatant HP, effects, and enemy mechanics; **`app/combat_flow.py`** is the receipt-backed managed pipeline built on it, and **`app/services/combat_engine.py`** (`CombatEngine.handle(state, action)`) is the one entry point for commands, Keeper tools and the check engine: it reads a battle's mode (idle or managed) once, and refuses an active battle that is not managed. `tests/test_architecture_combat.py` keeps `combat` free of any path to `combat_flow` ([spec](docs/specs/refactor/combat_engine_design_spec.md)).
- **`app/creation.py`** implements interactive character creation, including rolled attributes and occupation/personal-interest skill allocation.
- **`app/pregen_extractor.py`** extracts pregenerated investigators from scenario text.
- **`app/intent_parser.py`** detects movement and location-entry intent with regular expressions, without another LLM call.
- **`app/scene_map.py`** builds structured room graphs and resolves destinations from location, facing, direction, and door position in code.
- **`app/scenario_rag.py`** retrieves relevant scenario passages with BM25 and optional embedding-based scoring. When `SCENARIO_RAG_ENABLED=true`, the Keeper can use `search_scenario` instead of receiving the entire scenario text in its prompt.
- **`app/memory_rag.py`** retrieves older conversation chunks that have been trimmed from the live history, preserving details beyond the rolling `campaign_summary`.
- **`app/scenario_adjacency.py`** lets a scenario search hit that visibly continues into the next chunk bring that chunk along (bounded by `SCENARIO_RAG_ADJACENT_CHUNKS`), so a trigger and its consequence are not split by the chunk boundary.
- **`app/embedding_execution.py`** and **`app/memory_chunking.py`** keep memory embeddings bounded: token-measured parts of a trimmed chunk, a classified failure that never carries the provider's message, and a capped number of backfill attempts.
- **`app/scenario_index.py`** extracts NPC/monster and location indexes on upload or through `/coc index`, giving the Keeper a consistent reference for scenario statistics.
- **`app/scenario_library.py`** stores reusable PDF or Markdown scenario sources and their derived results under `data/scenarios/<scenario-id>/`. It supports chapters, a current-and-next-chapter context window, image lookup, and the `/coc scenario` commands. See the [scenario-library specification](docs/specs/feature/scenario_library_design_spec.md).
- **`app/dice.py`** implements COC7e dice and check calculations, including d100 rolls, bonus/penalty dice, success tiers, and sanity.
- **`app/models.py`** defines character/session models and quick investigator generation.
- **`app/pdf_loader.py`** extracts PDF text using MarkItDown and OCR preprocessing, with PyMuPDF for page rendering and text fallback. Vision/OCR handles image-heavy pages; extracted images remain available for display, and map pages can be converted into structured room graphs. `extract_preview()` supports inexpensive upload matching.
- **`app/markitdown_shim.py`** connects MarkItDown's OpenAI-style vision interface to the project's configured provider, including an Anthropic adapter.
- **`app/db.py`** stores session state, character indexes, and scenario/memory retrieval caches in SQLite.
- **`app/repositories/group_state.py`**, formerly `app/state.py`, handles session persistence and character-index mirrors. Page images remain separate PNG files.
- **`app/checks/`** is the check engine: `service.py` applies the check rules to the state a transaction hands it (a player's `/coc check` or button, a Luck decision, the Keeper's autoroll), `rules.py` does the dice and arithmetic behind a `DicePort`, `luck.py` states who may spend Luck, and `events.py` records the settled check once. Commands (`app/commands/handlers/checks.py`) and Keeper tools (`app/keeper_tools/checks.py`) are thin adapters over it; `tests/test_architecture_checks.py` keeps the engine free of transport, Keeper, provider and combat imports ([spec](docs/specs/refactor/check_engine_design_spec.md)).
- **`app/repositories/state_transaction.py`** is the only door for game-state writes: it locks the conversation, reads the latest row inside a `BEGIN IMMEDIATE` transaction, validates timeline, action ledger and revision, runs the mutation and commits state, events and the action result together. `tests/test_architecture_state_writes.py` fails the build when anything else writes a game-state row ([spec](docs/specs/refactor/state_transaction_design_spec.md)).

Only the Discord bot entry point needs to run. Discord receives image attachments directly; no webhook, ngrok tunnel, or public image URL is required.

### Optional two/three-column PDF reading order

With the existing optional Paddle dependencies on a supported Python interpreter (3.9–3.13), run `python scripts/setup_paddle_layout.py` once to download PP-DocLayoutV3. Set `PDF_PADDLE_LAYOUT_MODEL_DIR` for a different cache root; `PDF_PADDLE_LAYOUT_ENABLED=false` disables it. Normal extraction only loads local model files and never downloads them. Missing models/packages or uncertain layouts use the original flow, including on Python 3.14 where the official Paddle wheel is unavailable.

Paddle predicts regions and reading order only: text remains native PyMuPDF text. Only completely mapped, clearly separated two- or three-column pages are accepted. Two-column pages retain their model-order checks; three-column body text uses region geometry to read full left, middle and right columns without Paddle order. Single-column, mixed-column and uncertain pages retain the original flow. See the [two-column specification](docs/specs/enhancement/paddle_two_column_reading_order.md) and [three-column extension](docs/specs/enhancement/paddle_three_column_reading_order.md).

### Token budgets and API admission

The OpenAI path measures input composition, applies a configurable history budget, and uses response headers to estimate request/token admission budgets with a shared process-level cooldown. A turn deadline covers LLM admission, calls, retries, and narration. Output limits can be configured per stage; they are omitted by default.

Incomplete responses do not execute their tool calls. Earlier committed effects and queued private/image outputs are retained without automatically replaying tools. See the [token-admission specification](docs/specs/enhancement/token_admission_evaluation_design_spec.md) for defaults, experiments, and limitations.

### Performance profiling

Install the development tools:

```bash
pip install -r requirements-dev.txt
```

Profiling is disabled during normal startup. Select a profiler explicitly with `BOT_PROFILER`:

```bash
BOT_PROFILER=pyinstrument ./scripts/start_bot.sh discord --name profile-async
BOT_PROFILER=py-spy ./scripts/start_bot.sh discord --name profile-live
```

`pyinstrument` produces an async-aware HTML call stack. `py-spy` attaches to the running bot and produces an SVG flame graph. Both write artifacts under `.runtime/bots/`; use `./scripts/bot_status.sh` to find them.

Supported values are `off`, `pyinstrument`, and `py-spy`. Run `./scripts/start_bot.sh --help` for examples. On macOS, attaching `py-spy` may require root or additional process-attach permissions; the script fails explicitly when permission is unavailable.

## Documentation

This README is an introduction. Detailed guides and all specifications are available in English and Traditional Chinese. See the [documentation index](docs/README.md).

| Topic | Document |
|---|---|
| Discord credentials, `.env`, and local startup | [Installation and setup](docs/guides/setup.md) |
| Commands, gameplay, and mechanics | [Gameplay guide](docs/guides/gameplay.md) |
| Commands to copy | [Player command reference](docs/references/player_command_reference.md) |
| Feature history, design decisions, experiments, and known limitations | [Changelog](docs/changelog.md) |
| Keeper system-prompt rules | [Keeper instructions](docs/references/keeper_skill.md) |
| Original multi-agent pipeline design and history | [Agentic Keeper specification](docs/specs/refactor/agentic_keeper_design_spec.md) |
| Unified player-turn entry points and independent KP Assistant | [Unified Keeper turn flow](docs/specs/refactor/unified_keeper_turn_flow_design_spec.md) |
| Authoritative state and validated handoffs | [Turn-consistency specification](docs/specs/bug/log_backed_turn_consistency_design_spec.md) |
| History budgets, adaptive admission, and truncation handling | [Token-admission specification](docs/specs/enhancement/token_admission_evaluation_design_spec.md) |
| Reusable scenarios, chapters, context windows, and image assets | [Scenario-library specification](docs/specs/feature/scenario_library_design_spec.md) |
| HTTP endpoints, chat commands, PDF lifecycle, and internal interfaces | [API reference](docs/references/API.md) |
| NPC allies and information-flow controls | [Gameplay style](docs/references/gameplay_style.md) |
| COC7e rules versus the current implementation | [Rules reference](docs/references/rules_reference.md) |
| Carried-item plausibility checks | [Carry-audit specification](docs/references/carry_audit.md) |
| Comparison with the manual [coc-kp-host](https://github.com/SumanasJ/coc-kp-host) preparation workflow | [Preparation and persistence](docs/references/prep_persistence.md) |

## Local Codex OAuth experiment

[Local Codex OAuth experiment](docs/guides/codex_oauth_testing.md)
