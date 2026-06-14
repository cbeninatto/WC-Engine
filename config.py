"""Central config. Everything local; only the agent needs an API key."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent

# Load secrets (ANTHROPIC_API_KEY, Telegram) from a local, gitignored .env.
# Real environment variables still win (load_dotenv does not override them).
load_dotenv(ROOT / ".env")
DB_PATH = os.environ.get("WC_DB_PATH", str(ROOT / "wc.db"))
SCHEMA_PATH = str(ROOT / "db" / "schema.sql")

# Model used by the runtime agents (results monitor, squad monitor, ingest).
AGENT_MODEL = os.environ.get("WC_AGENT_MODEL", "claude-sonnet-4-6")

# Path to the existing Excel engine, used once to seed the DB.
XLSX_PATH = os.environ.get(
    "WC_XLSX_PATH", str(ROOT.parent / "WorldCup2026_Analytics_Companion.xlsx")
)
