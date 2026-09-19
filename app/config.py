import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

# Public HTTPS base URL this FastAPI server is reachable at (your ngrok URL while
# testing, your real domain in production) — WITHOUT a trailing slash. Only
# needed for /coc showpage and the Keeper's show_scenario_image tool on LINE:
# LINE's image message type can't take raw bytes, it needs a real URL, which
# app/main.py builds by pointing back at its own /images/... route. Changes
# every time your ngrok tunnel restarts, same as the Webhook URL — see the
# README. Discord doesn't need this at all; it attaches image bytes directly.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# Discord bot token (discord.com/developers/applications > your app > Bot > Reset
# Token). Only needed if you're running app/discord_bot.py.
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

DATA_DIR = Path(os.environ.get("DATA_DIR", "data/groups"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# SQLite database file — see app/db.py. Replaces the previous "one flat JSON
# file per group_id" storage (data/groups/*.json) for group state, character
# index mirrors, the scenario RAG index cache, and memory RAG chunks; scenario
# page images (PNG) still live as plain files under DATA_DIR, unaffected by
# this. Defaults to sitting next to DATA_DIR rather than inside it, so it's
# obviously a different kind of thing than the per-group image folders.
DB_PATH = Path(os.environ.get("DB_PATH", str(DATA_DIR.parent / "coc_bot.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

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
MAX_SCENARIO_CHARS = int(os.environ.get("MAX_SCENARIO_CHARS", 240_000))

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
MAX_LOG_TURNS = int(os.environ.get("MAX_LOG_TURNS", 40))

MAX_TOOL_ITERATIONS = 8  # guard against runaway tool-use loops

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
SCENARIO_RAG_TOP_K = int(os.environ.get("SCENARIO_RAG_TOP_K", 5))

# Hybrid search: BM25 (always on, zero cost) blended with OpenAI embeddings
# (skipped automatically if OPENAI_API_KEY isn't set — falls back to pure
# BM25, no error). SCENARIO_RAG_EMBEDDING_WEIGHT is how much weight the
# semantic (cosine similarity) score gets vs. BM25's lexical score in the
# 0-1 combined ranking; 0.5 means an even split. Double-check the current
# embedding model name at platform.openai.com/docs/models before relying on
# this default — same caveat as OPENAI_MODEL.
SCENARIO_RAG_EMBEDDING_MODEL = os.environ.get("SCENARIO_RAG_EMBEDDING_MODEL", "text-embedding-3-small")
SCENARIO_RAG_EMBEDDING_WEIGHT = float(os.environ.get("SCENARIO_RAG_EMBEDDING_WEIGHT", 0.5))

# Sampling temperature for the Keeper's own narration (app/keeper.py's
# run_conversation call only — not analyze_image/analyze_text, which are
# forced single-tool-call extraction and stay at each provider's default so
# structured output doesn't get noisier). Lower than each provider's own
# default (usually ~1.0) on purpose: the cold-observer persona in
# docs/keeper_skill.md needs the Keeper to hold a consistent tone and not
# drift off it turn to turn, and dice/rule outcomes it restates (rolls,
# tiers, damage) shouldn't get creative embellishment. 0.5-0.7 is the
# requested range; 0.6 sits in the middle.
KEEPER_TEMPERATURE = float(os.environ.get("KEEPER_TEMPERATURE", 0.6))

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

# Reusable parsed PDF scenarios (separate from per-conversation state).
SCENARIO_LIBRARY_DIR = Path(os.environ.get('SCENARIO_LIBRARY_DIR', str(DATA_DIR.parent / 'scenarios')))
SCENARIO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
IMPORT_DIR = Path(os.environ.get("IMPORT_DIR", "imports")).resolve()
IMPORT_DIR.mkdir(parents=True, exist_ok=True)
