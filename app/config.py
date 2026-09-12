import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

# Which LLM plays the Keeper: "anthropic" (Claude) or "gemini" (Google Gemini).
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Double-check the current flash model name at https://ai.google.dev before relying
# on this default — Google renames/retires model ids over time.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

DATA_DIR = Path(os.environ.get("DATA_DIR", "data/groups"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

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

# MAX_LOG_TURNS: how many recent chat turns stay in the Keeper's context (and, at
# 2x this, how many are kept on disk before older ones are trimmed). Raised from
# 40 now that app/providers/anthropic_provider.py caches the conversation-history
# prefix too (not just the scenario text), so a longer history costs much less
# per turn than it used to — see the cache_control comment there. This delays,
# but doesn't eliminate, the Keeper "forgetting" very early plot points in a long
# campaign; a real fix would need periodic summarization, which isn't built yet.
MAX_LOG_TURNS = int(os.environ.get("MAX_LOG_TURNS", 80))

MAX_TOOL_ITERATIONS = 8  # guard against runaway tool-use loops
