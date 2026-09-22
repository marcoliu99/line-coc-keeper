import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

INVALID_LOG_SETTINGS: list[tuple[str, str, str]] = []


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    INVALID_LOG_SETTINGS.append((name, "boolean", str(default).lower()))
    return default


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        INVALID_LOG_SETTINGS.append((name, "integer", str(default)))
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        INVALID_LOG_SETTINGS.append((name, "float", str(default)))
        return default

# Discord bot token (discord.com/developers/applications > your app > Bot > Reset
# Token). Required by the Discord runtime.
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

# Which LLM plays the Keeper: "anthropic" (Claude), "gemini" (Google Gemini), or
# "openai" (ChatGPT).
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Double-check the current flash model name at https://ai.google.dev before relying
# on this default — Google renames/retires model ids over time.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
# "gpt-5.6-luna" is a best-effort transcription of what the user asked for, not a
# verified model id — double-check the exact string at platform.openai.com/docs
# before relying on this default; OpenAI model ids/aliases change over time, same
# caveat as GEMINI_MODEL above.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")

DATA_DIR = Path(os.environ.get("DATA_DIR", "data/groups")).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# SQLite database file — see app/db.py. Replaces the previous "one flat JSON
# file per group_id" storage (data/groups/*.json) for group state, character
# index mirrors, the scenario RAG index cache, and memory RAG chunks; scenario
# page images (PNG) still live as plain files under DATA_DIR, unaffected by
# this. Defaults to sitting next to DATA_DIR rather than inside it, so it's
# obviously a different kind of thing than the per-group image folders.
DB_PATH = Path(os.environ.get("DB_PATH", str(DATA_DIR.parent / "coc_bot.db"))).expanduser().resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", str(DB_PATH.parent / "backups"))).expanduser().resolve()
BACKUP_INTERVAL_MINUTES = max(1, int(os.environ.get("BACKUP_INTERVAL_MINUTES", "60")))
BACKUP_KEEP_COUNT = max(1, int(os.environ.get("BACKUP_KEEP_COUNT", "48")))
SCENE_DIGEST_TURN_INTERVAL = max(1, int(os.environ.get("SCENE_DIGEST_TURN_INTERVAL", "12")))

# Safety limits. Both are heuristics, not measured against a real token count —
# tune them down if you're on a model with a smaller context window than Claude
# Sonnet's ~200K tokens, or up if you've checked your model comfortably fits more.
#
# MAX_SCENARIO_CHARS: raised from the original 90K after a real 43-page scenario
# PDF (~78K chars after OCR) came uncomfortably close to that cap — a longer
# campaign book would have silently lost its later chapters. 240K chars is
# roughly 60K tokens for English-heavy text or ~140K tokens for CJK-heavy text
# (Chinese runs closer to 2 chars/token), which still leaves headroom under a
# 200K-token context once the system prompt, character sheets, tool definitions,
# conversation history, and the response itself are all accounted for.
MAX_SCENARIO_CHARS = int(os.environ.get("MAX_SCENARIO_CHARS", "240000"))

# MAX_LOG_TURNS: how many recent chat turns stay verbatim in the Keeper's
# context (trim triggers at 4x this many log entries — see app/keeper.py's
# run_turn — keeping 2x after trimming). app/providers/anthropic_provider.py
# caches the conversation-history prefix too (not just the scenario text), so
# a longer history costs much less per turn than a naive re-send would.
# Trimmed content isn't just dropped — the same trim point folds it into
# GroupState.campaign_summary (rolling summarization) and indexes its
# original wording into app/memory_rag.py for semantic recall later, so
# lowering this only affects how much RECENT detail stays verbatim, not
# whether old plot points are remembered at all. 40 (trim at 160 entries,
# keep 80) — lowered from 80 to summarize more often, trading a bit more
# per-summarization LLM cost for less compression buildup in any one
# campaign_summary pass.
MAX_LOG_TURNS = int(os.environ.get("MAX_LOG_TURNS", "40"))

MAX_TOOL_ITERATIONS = _env_int("MAX_TOOL_ITERATIONS", 8, minimum=1)  # guard against runaway tool-use loops

# Scenario RAG (app/scenario_rag.py) — opt-in, defaults off. Off: the full
# scenario text is stuffed into the cached system prompt block, same as
# always, capped by MAX_SCENARIO_CHARS. On: the static prompt gets a small
# stub instead, and the Keeper is given a search_scenario tool that retrieves
# only the top-matching pages per query via local lexical (BM25-style)
# search — no embeddings API, no extra cost beyond the tool-call round trip.
# Trades away the "whole scenario visible at once" property that lets the
# Keeper freely connect clues across pages, in exchange for scenarios that
# would blow past MAX_SCENARIO_CHARS entirely. See README's 限制 section.
SCENARIO_RAG_ENABLED = os.environ.get("SCENARIO_RAG_ENABLED", "false").strip().lower() in ("1", "true", "yes")
SCENARIO_RAG_TOP_K = int(os.environ.get("SCENARIO_RAG_TOP_K", "5"))

# Scenario lifecycle authorization. Keep this off during the initial lobby so
# a player who is also helping as KP can upload/reparse/cancel a scenario while
# roles are still being arranged. Set SCENARIO_LIFECYCLE_KP_ONLY=true later to
# require the current KP Assistant or Discord Keeper role for those commands.
SCENARIO_LIFECYCLE_KP_ONLY = os.environ.get("SCENARIO_LIFECYCLE_KP_ONLY", "false").strip().lower() in (
    "1", "true", "yes",
)

# Hybrid search: BM25 (always on, zero cost) blended with OpenAI embeddings
# (skipped automatically if OPENAI_API_KEY isn't set — falls back to pure
# BM25, no error). SCENARIO_RAG_EMBEDDING_WEIGHT is how much weight the
# semantic (cosine similarity) score gets vs. BM25's lexical score in the
# 0-1 combined ranking; 0.5 means an even split. Double-check the current
# embedding model name at platform.openai.com/docs/models before relying on
# this default — same caveat as OPENAI_MODEL.
SCENARIO_RAG_EMBEDDING_MODEL = os.environ.get("SCENARIO_RAG_EMBEDDING_MODEL", "text-embedding-3-small")
SCENARIO_RAG_EMBEDDING_WEIGHT = float(os.environ.get("SCENARIO_RAG_EMBEDDING_WEIGHT", "0.5"))

# Sampling temperature for the Keeper's own narration (app/keeper.py's
# run_conversation call only — not analyze_image/analyze_text, which are
# forced single-tool-call extraction and stay at each provider's default so
# structured output doesn't get noisier). Lower than each provider's own
# default (usually ~1.0) on purpose: the cold-observer persona in
# docs/keeper_skill.md needs the Keeper to hold a consistent tone and not
# drift off it turn to turn, and dice/rule outcomes it restates (rolls,
# tiers, damage) shouldn't get creative embellishment. 0.5-0.7 is the
# requested range; 0.6 sits in the middle.
KEEPER_TEMPERATURE = float(os.environ.get("KEEPER_TEMPERATURE", "0.6"))

# Reasoning effort for the Keeper's own narration on OpenAI's Responses API
# (app/providers/openai_provider.py's run_conversation only — see
# KEEPER_TEMPERATURE above for why analyze_image/analyze_text are excluded;
# same reasoning applies here). Confirmed against this project's installed
# `openai` SDK type stubs (openai/types/shared_params/reasoning.py):
# `reasoning.effort` accepts none/minimal/low/medium/high/xhigh/max. Before
# this setting existed, the code never passed `reasoning` at all, so it
# silently inherited whatever the API's own per-model default is — not
# explicitly forced to "none", but an unpinned unknown rather than a
# deliberate choice, the same class of problem KEEPER_TEMPERATURE fixed for
# temperature. "medium" is the SDK's own listed middle value; Anthropic and
# Gemini have no equivalent concept in this project's provider adapters, so
# this only affects the openai path. If the configured model rejects this
# parameter outright, openai_provider.py detects that once per process and
# stops sending it, the same fallback pattern already used for temperature.
KEEPER_REASONING_EFFORT = os.environ.get("KEEPER_REASONING_EFFORT", "medium").strip().lower()

# Retry/backoff for transient LLM API failures (connection drops, timeouts,
# 5xx) — see app/providers/retry.py. None of the three provider adapters
# retried these at all before this: a single network blip during
# client.messages.create/generate_content/responses.create raised straight
# out of run_conversation, through keeper.run_turn, and surfaced to the
# player as a bare "發生錯誤了：..." with their whole turn lost, even though
# the failure had nothing to do with their input and a retry a second later
# would very likely have succeeded. LLM_MAX_RETRIES=0 disables retrying
# entirely (first failure always raises immediately, same as before this
# setting existed). Exponential backoff: attempt N waits
# LLM_RETRY_BASE_DELAY_SECONDS * 2**(N-1) — with the defaults below, 1s,
# 2s, 4s (worst case ~7s added latency before giving up), which is small
# next to how slow a normal Keeper turn already is.
LLM_MAX_RETRIES = _env_int("LLM_MAX_RETRIES", 3)
LLM_RETRY_BASE_DELAY_SECONDS = _env_float("LLM_RETRY_BASE_DELAY_SECONDS", 1.0)
LLM_REQUEST_TIMEOUT_SECONDS = _env_float("LLM_REQUEST_TIMEOUT_SECONDS", 60.0, minimum=0.1)
LLM_TIMEOUT_RETRIES = _env_int("LLM_TIMEOUT_RETRIES", 1)
EMBEDDING_REQUEST_TIMEOUT_SECONDS = _env_float("EMBEDDING_REQUEST_TIMEOUT_SECONDS", 20.0, minimum=0.1)
DISCORD_REQUEST_TIMEOUT_SECONDS = _env_float("DISCORD_REQUEST_TIMEOUT_SECONDS", 10.0, minimum=0.1)
TOOL_EXECUTION_TIMEOUT_SECONDS = _env_float("TOOL_EXECUTION_TIMEOUT_SECONDS", 30.0, minimum=0.1)
PROVIDER_SHUTDOWN_GRACE_SECONDS = _env_float("PROVIDER_SHUTDOWN_GRACE_SECONDS", 5.0, minimum=0.1)

# Scenario embedding prewarm remains opt-in so deployments do not incur a
# surprise embedding request during startup/import.  The semaphore keeps a
# background rebuild lower priority than player turns.
SCENARIO_RAG_PREWARM_ENABLED = _env_bool("SCENARIO_RAG_PREWARM_ENABLED", False)
SCENARIO_RAG_PREWARM_MAX_CONCURRENT = _env_int(
    "SCENARIO_RAG_PREWARM_MAX_CONCURRENT", 1, minimum=1
)

# Observability. Structured performance events and ordinary developer text
# logs have separate toggles so a developer can enable textual diagnostics
# without paying for timers, token/byte counters, or JSON event construction.
LOG_ENABLED = _env_bool("LOG_ENABLED", False)
LOG_TEXT_ENABLED = _env_bool("LOG_TEXT_ENABLED", True)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
if LOG_LEVEL not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
    INVALID_LOG_SETTINGS.append(("LOG_LEVEL", "string", "INFO"))
    LOG_LEVEL = "INFO"
LOG_FORMAT = os.environ.get("LOG_FORMAT", "json").strip().lower()
if LOG_FORMAT not in {"json", "text"}:
    INVALID_LOG_SETTINGS.append(("LOG_FORMAT", "string", "json"))
    LOG_FORMAT = "json"
LOG_FILE = os.environ.get("LOG_FILE", "").strip()
LOG_SLOW_REQUEST_MS = _env_int("LOG_SLOW_REQUEST_MS", 3000)
LOG_SLOW_OPERATION_MS = _env_int("LOG_SLOW_OPERATION_MS", 1000)
LOG_HASH_IDENTIFIERS = _env_bool("LOG_HASH_IDENTIFIERS", True)
LOG_INCLUDE_USAGE = _env_bool("LOG_INCLUDE_USAGE", True)

# Spoiler protection hardening (docs/spoiler-protection-hardening_design_spec.md)
# — two independent switches. SPOILER_PROTECTION_ENABLED gates "劇情揭露節奏"
# mechanisms (chapter gating, KP-only/public digest split, NPC/Narrator/scenario
# spoiler prompt rules, the public-reply output guard, /coc index, /coc pregen);
# a KP may turn it off for a freeform session or debugging. PRIVACY_ISOLATION_ENABLED
# gates "資料歸屬" mechanisms (private DMs, secret goals, private handouts/images,
# combat/damage info hidden from players) — leave this on in production; it's
# only meant to be relaxed for local development. See app/spoiler_policy.py.
SPOILER_PROTECTION_ENABLED = _env_bool("SPOILER_PROTECTION_ENABLED", True)
PRIVACY_ISOLATION_ENABLED = _env_bool("PRIVACY_ISOLATION_ENABLED", True)

# Reusable parsed PDF scenarios (separate from per-conversation state).
SCENARIO_LIBRARY_DIR = Path(os.environ.get('SCENARIO_LIBRARY_DIR', str(DATA_DIR.parent / 'scenarios')))
SCENARIO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
IMPORT_DIR = Path(os.environ.get("IMPORT_DIR", "imports")).resolve()
IMPORT_DIR.mkdir(parents=True, exist_ok=True)
