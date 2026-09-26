# COC7e Keeper Bot for Discord

**English** | [繁體中文](README_zh.md)

Upload a *Call of Cthulhu, Seventh Edition* (COC7e) scenario PDF to a Discord channel and let an LLM act as the Keeper. Players describe their actions in chat; the Keeper reads the scenario, narrates events, and decides when checks are needed. Python code handles dice rolls, skill checks, sanity, character resources, and combat state.

The bot supports Anthropic Claude, Google Gemini, and OpenAI. See the [setup guide](docs/setup.md) for provider configuration. The detailed guides and many in-game messages are currently in Traditional Chinese.

## Quick start

### Setting up the bot

Follow [Installation and setup](docs/setup.md) to create a Discord application, configure permissions and `.env`, and start the bot locally.

### Playing with an existing bot

1. **Upload a scenario.** Attach a COC7e scenario PDF to the channel. Image-heavy PDFs take longer to process; the bot acknowledges the upload before returning the extracted content. Complete any scenario-selection or replacement prompts it presents.
2. **Create a character.** Each player can use:

   ```text
   /coc pc Alex
   ```

   The command also accepts an optional occupation: `/coc pc <name> [occupation]`. Use the occupation labels shown by the bot. For interactive character creation or a scenario's pregenerated investigators, see the [gameplay guide](docs/gameplay.md).
3. **Start the game.** Once at least one investigator is ready, use `/coc start`. The bot uses the scenario's read-aloud opening when available; otherwise, it generates an opening grounded in the scenario. Ordinary chat does not advance the story before the game starts.
4. **Describe your actions.** Write what your character does in ordinary channel messages. The Keeper narrates, requests checks, and updates game state through tools.
5. **Check commands and progress.** Use `/coc help` for categorized help, `/coc status` for session progress, and `/coc sheet` for your character sheet. See the [player command reference](docs/player_command_reference.md) for commands to copy, and the [gameplay guide](docs/gameplay.md) for detailed rules and workflows.

## Architecture

```text
Discord channel
  -> app/discord_bot.py
  -> app/commands/router.py
       |
       +-- Commands / PDF uploads
       |     -> app/commands/handlers/*.py
       |     -> app/legacy_commands.py (shared command implementations)
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
  app/keeper.py                  Prompts, tool definitions, tool execution
  app/services/turn_context.py   Current-state and historical projections
  app/services/turn_resolution.py  Deterministic handoff validation
  app/scenario_rag.py            Scenario retrieval
  app/memory_rag.py              Retrieval of older conversation history
  app/providers/*_provider.py    Anthropic / Gemini / OpenAI adapters

Persistence:
  data/coc_bot.db                Session state, character indexes, RAG caches
  data/groups/                  Extracted scenario page images
  data/scenarios/               Reusable scenario library
```

### Commands and message routing

- **`app/discord_bot.py`** maintains the Discord gateway connection and forwards events to the command router.
- **`app/commands/router.py`** is the platform-independent routing entry point. It dispatches commands to domain handlers and ordinary messages to the Supervisor.
- **`app/commands/handlers/`** separates character, combat, system/scenario, and map commands into `character.py`, `combat.py`, `system.py`, and `map_handler.py`. Handlers reuse existing command logic where appropriate.
- **`app/legacy_commands.py`**, formerly the single `app/commands.py` module, contains shared implementations such as PDF uploads, player checks and Luck decisions, pregen merging, and away/back handling. Resolved-check narration now enters the unified Supervisor flow rather than a separate `keeper.run_turn` path.

### Agentic Keeper and the unified turn flow

- **`app/agents/supervisor.py`** coordinates player turns in Python. It handles ordinary actions, resolved-check follow-ups, and opening fallback through explicit entry modes, reconciles authoritative state, and commits the resulting narration.
- **`app/agents/context_builder.py`** gathers scenario and memory context. Scenario retrieval is gated by `SCENARIO_RAG_ENABLED`; proactive scenario and memory retrieval are skipped during active combat. Retrieval can use embeddings, so it is not necessarily an entirely local operation.
- **`app/agents/intent_router.py`** uses rules rather than an LLM call to classify ordinary messages as `OOC_ASSISTANT`, `PURE_ROLEPLAY`, or `GAMEPLAY_ACTION`.
- **`app/agents/executor.py`** and **`tool_gateway.py`** handle gameplay mechanics using `keeper.TOOLS` and `keeper._execute_tool`. Tools perform real calculations and persist real changes. The existing final completion carries a structured decision to the validation layer.
- **`app/services/turn_resolution.py`** checks that decision against current state and observed tool effects. An Executor claim alone does not prove completion. Deferred and cancelled decisions cannot hide unrelated committed mutations; unvalidated free-form reasons are excluded from Narrator authority.
- **`app/agents/assistant.py`** is the independent KP Assistant agent for out-of-character discussion. It owns its provider, tool, guard, and history path while preserving the rules for explicitly established canonical events.
- **`app/agents/narrator.py`** produces player-facing narration. Ordinary gameplay narration has no tools. Resolved-check follow-ups and opening fallback receive restricted tool sets suitable for those entry modes.
- **`app/agents/rule_validator.py`** and **`guard.py`** check narrative formatting and prohibited system wording. When enabled, the Guard attempts up to two repairs only after validation fails, then falls back if the output remains invalid. There is no fixed extra LLM review call on every turn.
- **`app/agents/state_reducer.py`** is a recording stage, not a second mutation stage: Executor tools have already applied and saved their changes.
- **`app/services/prompt_config.py`** builds stage-specific prompts around the shared Keeper instructions and enforces consistent next-action instructions.
- **`app/domain/models.py`** defines internal messages and results, including `AgentMessage`, `MechanicResult`, `StateDelta`, and `TurnResolution`.

See the [unified Keeper turn-flow specification](docs/unified_keeper_turn_flow_design_spec.md) for the current entry points, the [turn-consistency specification](docs/log_backed_turn_consistency_design_spec.md) for authoritative handoffs, and the [original Agentic Keeper design](docs/agentic_keeper_design_spec.md) for the design history.

### Shared game logic and infrastructure

- **`app/keeper.py`** provides provider-independent system-prompt assembly, tool definitions, and tool execution for dice, checks, combat, character resources, scenario images, and chapter progression.
- **`app/providers/anthropic_provider.py`** adapts the Anthropic Messages API, including prompt caching. **`gemini_provider.py`** and **`openai_provider.py`** provide the Google GenAI and OpenAI integrations. `LLM_PROVIDER` selects the backend.
- **`app/locks.py`** provides per-conversation locking to prevent overlapping messages from overwriting saved state, with priority handling for KP Assistant messages.
- **`app/combat.py`** manages initiative, rounds, combatant HP, effects, and enemy mechanics.
- **`app/creation.py`** implements interactive character creation, including rolled attributes and occupation/personal-interest skill allocation.
- **`app/pregen_extractor.py`** extracts pregenerated investigators from scenario text.
- **`app/intent_parser.py`** detects movement and location-entry intent with regular expressions, without another LLM call.
- **`app/scene_map.py`** builds structured room graphs and resolves destinations from location, facing, direction, and door position in code.
- **`app/scenario_rag.py`** retrieves relevant scenario passages with BM25 and optional embedding-based scoring. When `SCENARIO_RAG_ENABLED=true`, the Keeper can use `search_scenario` instead of receiving the entire scenario text in its prompt.
- **`app/memory_rag.py`** retrieves older conversation chunks that have been trimmed from the live history, preserving details beyond the rolling `campaign_summary`.
- **`app/scenario_index.py`** extracts NPC/monster and location indexes on upload or through `/coc index`, giving the Keeper a consistent reference for scenario statistics.
- **`app/scenario_library.py`** stores reusable PDF extraction results under `data/scenarios/<scenario-id>/`. It supports chapters, a current-and-next-chapter context window, image lookup, and the `/coc scenario` commands. See the [scenario-library specification](docs/scenario_library_design_spec.md).
- **`app/dice.py`** implements COC7e dice and check calculations, including d100 rolls, bonus/penalty dice, success tiers, and sanity.
- **`app/models.py`** defines character/session models and quick investigator generation.
- **`app/pdf_loader.py`** extracts PDF text using MarkItDown and OCR preprocessing, with PyMuPDF for page rendering and text fallback. Vision/OCR handles image-heavy pages; extracted images remain available for display, and map pages can be converted into structured room graphs. `extract_preview()` supports inexpensive upload matching.
- **`app/markitdown_shim.py`** connects MarkItDown's OpenAI-style vision interface to the project's configured provider, including an Anthropic adapter.
- **`app/db.py`** stores session state, character indexes, and scenario/memory retrieval caches in SQLite.
- **`app/repositories/group_state.py`**, formerly `app/state.py`, handles session persistence and character-index mirrors. Page images remain separate PNG files.

Only the Discord bot entry point needs to run. Discord receives image attachments directly; no webhook, ngrok tunnel, or public image URL is required.

### Token budgets and API admission

The OpenAI path measures input composition, applies a configurable history budget, and uses response headers to estimate request/token admission budgets with a shared process-level cooldown. A turn deadline covers LLM admission, calls, retries, and narration. Output limits can be configured per stage; they are omitted by default.

Incomplete responses do not execute their tool calls. Earlier committed effects and queued private/image outputs are retained without automatically replaying tools. See the [token-admission specification](docs/token_admission_evaluation_design_spec.md) for defaults, experiments, and limitations.

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

This README is an introduction. Detailed guides are maintained separately; most are currently in Traditional Chinese.

| Topic | Document |
|---|---|
| Discord credentials, `.env`, and local startup | [Installation and setup](docs/setup.md) |
| Commands, gameplay, and mechanics | [Gameplay guide](docs/gameplay.md) |
| Commands to copy | [Player command reference](docs/player_command_reference.md) |
| Feature history, design decisions, experiments, and known limitations | [Changelog](docs/changelog.md) |
| Keeper system-prompt rules | [Keeper instructions](docs/keeper_skill.md) |
| Original multi-agent pipeline design and history | [Agentic Keeper specification](docs/agentic_keeper_design_spec.md) |
| Unified player-turn entry points and independent KP Assistant | [Unified Keeper turn flow](docs/unified_keeper_turn_flow_design_spec.md) |
| Authoritative state and validated handoffs | [Turn-consistency specification](docs/log_backed_turn_consistency_design_spec.md) |
| History budgets, adaptive admission, and truncation handling | [Token-admission specification](docs/token_admission_evaluation_design_spec.md) |
| Reusable scenarios, chapters, context windows, and image assets | [Scenario-library specification](docs/scenario_library_design_spec.md) |
| HTTP endpoints, chat commands, PDF lifecycle, and internal interfaces | [API reference](docs/API.md) |
| NPC allies and information-flow controls | [Gameplay style](docs/references/gameplay_style.md) |
| COC7e rules versus the current implementation | [Rules reference](docs/references/rules_reference.md) |
| Carried-item plausibility checks | [Carry-audit specification](docs/references/carry_audit.md) |
| Comparison with the manual [coc-kp-host](https://github.com/SumanasJ/coc-kp-host) preparation workflow | [Preparation and persistence](docs/references/prep_persistence.md) |
