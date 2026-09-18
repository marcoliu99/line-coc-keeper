# API Reference

This document describes the interfaces exposed by the current application. The
bot is primarily an event-driven LINE/Discord application, not a general REST
service. Values and credentials shown below are placeholders only.

## 1. HTTP endpoints

### `POST /callback`

LINE webhook entry point. Configure this URL in LINE Developers Console.

| Item | Requirement |
| --- | --- |
| Authentication | `X-Line-Signature` request header; validated with `LINE_CHANNEL_SECRET` |
| Request body | Raw LINE Messaging API webhook JSON |
| Success | `200 OK` with body `"OK"` |
| Invalid signature | `400` with `{ "detail": "Invalid signature" }` |

Only LINE `MessageEvent` messages are handled. Text goes to the command/Keeper
router; uploaded files are treated as PDF candidates. The endpoint relies on
LINE's webhook schema and does not define a separate application JSON schema.

### `GET /images/{conversation_id}/{page_number}.png`

Returns a rendered PDF page image currently stored for the conversation.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `conversation_id` | string | Internal platform-prefixed conversation ID, for example `line-group-...` |
| `page_number` | integer | 1-based PDF page number |

Success is `200 image/png`; missing images return `404` with
`{ "detail": "Image not found" }`.

This route currently has **no request-level authorization**. It must therefore
only be served through an unguessable/private `PUBLIC_BASE_URL`, or be protected
by infrastructure access controls. It is used because LINE image messages need
a public HTTPS URL; Discord receives the bytes through its gateway adapter.

## 2. Chat command interface

Commands are the public application API for players and KP. `scenario` commands
are supplied through `/coc`:

| Command | Current behavior |
| --- | --- |
| `/coc scenario list` | Lists parsed reusable PDF scenarios and playable chapter titles; marks the scenario selected in this conversation. |
| `/coc scenario use <scenario-id>` | KP Assistant only. Selects a library scenario, sets the first playable chapter as active, and loads the active plus next chapter into `scenario_text`. It keeps campaign state but resets the OpenAI response-chain ID. |
| `/coc scenario clean <scenario-id>` | Deletes a library entry unless this conversation is currently using it. |
| `/coc scenario reparse` | Parses the staged similar upload after similarity detection. |
| `/coc scenario cancel` | Drops the staged similar upload. |
| `/coc kp` / `/coc kp quit` | Register or remove the conversation's KP Assistant. |
| `/coc pdf new` / `/coc pdf fix` | Resolves a normal PDF replacement as a new scenario or a correction of the active scenario. |

`scenario-id` is the stable library directory ID returned by `list`, not a title
or chapter ID. The current implementation uses whitespace splitting, so IDs
must not contain spaces.

## 3. PDF upload lifecycle

LINE file events and Discord attachments call the internal coroutine below;
there is no standalone upload HTTP endpoint.

```python
async def handle_pdf_upload(
    conversation_id: str,
    reply: Reply,
    push: Reply,
    pdf_bytes: bytes,
    file_name: str,
    skip_similarity: bool = False,
) -> None
```

`reply` is for the immediate platform acknowledgement. `push` delivers the
long-running result after extraction. The implementation first extracts a short
preview and compares it with scenario-library previews. A similar document is
staged until the KP enters `reparse` or `cancel`; otherwise it extracts text,
page images/maps, NPC/location index, pregenerated characters, and persists a
library entry.

## 4. Internal provider adapter contract

Each provider in `app/providers/` implements the same synchronous interface:

```python
def run_conversation(
    static_system: str,
    dynamic_system: str,
    tools: list[dict],
    history: list[dict],
    new_message: str,
    execute_tool: Callable[[str, dict], dict],
    max_iterations: int,
) -> str

def analyze_image(png_bytes: bytes, tool: dict, prompt_text: str) -> dict | None

def analyze_text(text: str, tool: dict, prompt_text: str) -> dict | None
```

`run_conversation` returns final Keeper narration. The analysis functions force
a single structured tool call for PDF-map/character extraction and return its
arguments, or `None` on failure. Provider selection is controlled by
`LLM_PROVIDER` (`anthropic`, `gemini`, or `openai`). These are internal Python
interfaces, not network APIs.

## 5. Internal scenario-library contract

`app.scenario_library` stores reusable parsed scenarios beneath
`SCENARIO_LIBRARY_DIR` and exposes these functions:

| Function | Purpose |
| --- | --- |
| `save_scenario(...) -> str` | Writes the source PDF, extracted text, images, indexes, pregens, chapter manifest, and image-asset catalogue; returns scenario ID. |
| `list_scenarios() -> list[dict]` | Returns library manifest summaries. |
| `find_similar(title, preview, threshold=0.82) -> list[dict]` | Scores a preview against existing scenario previews. |
| `load_context(scenario_id, active_chapter_id="") -> dict` | Returns the active playable chapter plus its successor, with filtered text, indexes, maps, page numbers, and images. |
| `stage_upload`, `read_staged_upload`, `discard_staged_upload` | Manage a pending similar PDF. |
| `search_images(scenario_id, query="", image_type="", allowed_chapter_ids=None)` | Searches the persisted image-asset catalogue, optionally restricted to the loaded chapter window. |
| `get_image_asset(scenario_id, image_id)` | Returns one catalogue record or `None`. |
| `clean_scenario(scenario_id)` | Permanently deletes the scenario directory. |

Keeper tools expose this catalogue as `search_scenario_images` and validate
`show_scenario_image` against the current chapter window.

## 6. Keeper scenario tools

These tools are internal LLM tool calls, not HTTP endpoints. KP Assistant may use
all three; image operations are constrained to `context_chapter_ids`.

| Tool | Result |
| --- | --- |
| `search_scenario_images(query="", image_type="")` | Returns matching map, portrait, illustration, or character-sheet assets only from the active two-chapter window. |
| `show_scenario_image(page_number, investigator=None)` | Queues a scoped image for public display or a named investigator. Requests for pages outside the window are rejected. |
| `advance_scenario_chapter()` | Advances exactly one playable chapter, reloads the next two-chapter text/index/map/image window, and clears the old OpenAI response chain. |

## 7. Keeper combat tools

These tools mutate `GroupState.combat` and are internal LLM tool calls, not HTTP
endpoints. Enemy combatants now carry private combat cards; player-facing
narration must not expose hidden ability names, exact armor, cooldowns, usage
limits, or weaknesses until the scenario reveals them.

| Tool | Result |
| --- | --- |
| `start_combat()` | Starts initiative using active, non-away player characters. |
| `add_npc_to_combat(name, dex, hp, is_ally=false, armor=[], attacks=[], abilities=[])` | Adds an ally or creates an enemy combat card and adds it to initiative. Existing shorthand `name/dex/hp` remains valid but creates an incomplete enemy card. |
| `plan_enemy_turn(enemy="")` | For the current or named enemy, checks special abilities, triggers, usage, cooldowns, and available attacks; returns a private plan plus safe public hint. |
| `resolve_enemy_action(plan_id)` | Marks the selected enemy plan as resolved and consumes ability usage/cooldown. Repeated calls for the same plan are idempotent. |
| `apply_combat_damage(target, raw_damage, damage_type="physical", tags=[], source_id="")` | Applies damage with a raw/armor/final breakdown and updates HP. |
| `add_combat_effect(target, label, timing, damage="", damage_type="physical", remaining_rounds=null, tags=[], source_id="", public_description="")` | Adds a fixed-timing combat effect. Damage may be a flat integer string or a dice expression; invalid expressions are reported instead of silently consuming duration. |
| `damage_combatant(name, delta)` | Legacy HP adjustment wrapper. Negative deltas now flow through armor-aware damage resolution. |
| `advance_combat_turn()` | Processes turn-end/new-round timing, resets per-round ability usage, ticks cooldowns, and advances initiative. |
| `end_combat()` | Clears combat state. |

## 8. Configuration

| Environment variable | Used for |
| --- | --- |
| `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN` | LINE webhook validation and replies. |
| `PUBLIC_BASE_URL` | Public HTTPS base used in LINE image URLs. |
| `DISCORD_BOT_TOKEN` | Discord gateway bot. |
| `LLM_PROVIDER` and provider API key/model variables | Keeper and extraction provider selection. |
| `DATA_DIR`, `DB_PATH` | Conversation state, page images, and SQLite state. |
| `SCENARIO_LIBRARY_DIR` | Reusable parsed-PDF storage. |
| `SCENARIO_RAG_*` | Scenario retrieval behavior. |

See `.env.example`, `docs/setup.md`, and `app/config.py` for the complete
configuration list and defaults.
