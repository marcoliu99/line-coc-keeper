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

# Safety limits
MAX_SCENARIO_CHARS = 90_000  # ~ keeps prompt within a sane token budget
MAX_LOG_TURNS = 40  # how many recent chat turns to keep in the Keeper's context
MAX_TOOL_ITERATIONS = 8  # guard against runaway tool-use loops
