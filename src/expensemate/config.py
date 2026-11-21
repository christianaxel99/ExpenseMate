"""Settings read from environment variables, with defaults for local use."""
import os

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
DB_PATH = os.environ.get("EXPENSEMATE_DB", "data/expenses.db")
LOG_DIR = os.environ.get("EXPENSEMATE_LOG_DIR", "logs")
EXCHANGE_API_URL = os.environ.get(
    "EXCHANGE_API_URL", "https://open.er-api.com/v6/latest/{base}"
)
MAX_TOOL_STEPS = int(os.environ.get("EXPENSEMATE_MAX_STEPS", "5"))
DEFAULT_CURRENCY = "TWD"

CATEGORIES = [
    "Food", "Transport", "Shopping", "Bills",
    "Education", "Entertainment", "Health", "Other",
]

# Currencies the agent supports. "NTD" is accepted as another name for TWD.
CURRENCIES = ["TWD", "IDR", "USD"]
CURRENCY_ALIASES = {"NTD": "TWD", "NT": "TWD", "RP": "IDR", "RUPIAH": "IDR"}

# Fallback rates (units per 1 USD), used only when the live API cannot be
# reached. They are approximate and may be outdated; the agent says so.
FALLBACK_RATES_PER_USD = {"USD": 1.0, "TWD": 33.0, "IDR": 18250.0}
FALLBACK_RATES_DATE = "2026-11-19 (approximate)"
