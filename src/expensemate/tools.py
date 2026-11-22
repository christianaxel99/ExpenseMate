"""The agent's tools.

Every tool returns a dict. On success it contains "ok": True plus the result.
On failure it contains "ok": False and an "error" message written for the LLM,
so the agent can read the problem and try again or ask the user.
Tools never raise exceptions to the agent loop.
"""
import json
import urllib.request
from datetime import date, datetime, timedelta

from . import config, db


# ---------------------------------------------------------------- helpers

class ToolError(Exception):
    """A problem with the tool's input. The message is shown to the LLM."""


def normalize_currency(value) -> str:
    code = str(value or config.DEFAULT_CURRENCY).strip().upper()
    code = config.CURRENCY_ALIASES.get(code, code)
    if code not in config.CURRENCIES:
        raise ToolError(
            f"Unsupported currency '{value}'. Supported: {', '.join(config.CURRENCIES)}."
        )
    return code


def normalize_category(value) -> str:
    if not value:
        return "Other"
    for name in config.CATEGORIES:
        if str(value).strip().lower() == name.lower():
            return name
    raise ToolError(
        f"Unknown category '{value}'. Use one of: {', '.join(config.CATEGORIES)}."
    )


def parse_amount(value) -> int:
    try:
        cents = db.to_cents(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        raise ToolError(f"Amount '{value}' is not a number.")
    if cents <= 0:
        raise ToolError("Amount must be greater than 0.")
    return cents


def resolve_date(value, today: date) -> str:
    """Accept 'today', 'yesterday' or an ISO date (YYYY-MM-DD)."""
    if value is None or str(value).strip() == "":
        return today.isoformat()
    text = str(value).strip().lower()
    if text == "today":
        return today.isoformat()
    if text == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ToolError(
            f"Date '{value}' is not valid. Use 'today', 'yesterday' or YYYY-MM-DD."
        )
    if parsed > today:
        raise ToolError(f"Date {parsed} is in the future.")
    return parsed.isoformat()


PERIODS = ["today", "yesterday", "this_week", "last_week",
           "this_month", "last_month", "all"]


def resolve_period(period, today: date):
    """Turn a period name into (start, end) ISO dates. Weeks start on Monday."""
    p = str(period or "all").strip().lower().replace(" ", "_")
    if p == "today":
        return today.isoformat(), today.isoformat()
    if p == "yesterday":
        d = today - timedelta(days=1)
        return d.isoformat(), d.isoformat()
    if p == "this_week":
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat()
    if p == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        return start.isoformat(), (start + timedelta(days=6)).isoformat()
    if p == "this_month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if p == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1).isoformat(), end.isoformat()
    if p == "all":
        return None, None
    raise ToolError(f"Unknown period '{period}'. Use one of: {', '.join(PERIODS)}.")


def fetch_live_rates(base: str) -> dict:
    """Ask the exchange-rate API for rates from `base` to other currencies."""
    url = config.EXCHANGE_API_URL.format(base=base)
    with urllib.request.urlopen(url, timeout=5) as resp:
        data = json.load(resp)
    if data.get("result") != "success" or "rates" not in data:
        raise RuntimeError(f"Exchange-rate API returned an error: {data.get('error-type')}")
    return data["rates"]


# ---------------------------------------------------------------- toolbox

class Toolbox:
    def __init__(self, conn, today_fn=date.today, rate_fetcher=fetch_live_rates):
        self.conn = conn
        self.today_fn = today_fn
        self.rate_fetcher = rate_fetcher

    # Each public tool wraps its body so input errors become {"ok": False}.
    def run(self, name: str, arguments: dict) -> dict:
        func = getattr(self, f"tool_{name}", None)
        if func is None:
            return {"ok": False, "error": f"Unknown tool '{name}'. "
                    f"Available tools: {', '.join(TOOL_NAMES)}."}
        try:
            return func(**(arguments or {}))
        except ToolError as e:
            return {"ok": False, "error": str(e)}
        except TypeError as e:  # wrong or missing argument names
            return {"ok": False, "error": f"Bad arguments for {name}: {e}"}
        except Exception as e:  # never crash the agent loop
            return {"ok": False, "error": f"{name} failed: {type(e).__name__}: {e}"}

    def tool_add_expense(self, amount, category="Other", note="",
                         currency=None, date=None):
        cents = parse_amount(amount)
        rec = dict(
            amount_cents=cents,
            currency=normalize_currency(currency),
            category=normalize_category(category),
            date=resolve_date(date, self.today_fn()),
            note=str(note or "").strip(),
        )
        new_id = db.insert(self.conn, **rec)
        return {"ok": True, "record": db.row_to_record(db.get(self.conn, new_id))}

    def tool_query_expenses(self, period="all", start_date=None, end_date=None,
                            category=None, keyword=None, date=None):
        today = self.today_fn()
        # Models often send "date" for a single day (or a period name), so accept it.
        if date and not (start_date or end_date):
            d = str(date).strip().lower().replace(" ", "_")
            if d in PERIODS:
                period = d
            else:
                start_date = end_date = date
        if start_date or end_date:
            start = resolve_date(start_date, today) if start_date else None
            end = resolve_date(end_date, today) if end_date else today.isoformat()
        else:
            start, end = resolve_period(period, today)
        cat = normalize_category(category) if category else None
        rows = db.search(self.conn, start, end, cat, keyword or None)

        totals, by_category = {}, {}
        for r in rows:
            totals[r["currency"]] = totals.get(r["currency"], 0) + r["amount_cents"]
            c = by_category.setdefault(r["category"], {})
            c[r["currency"]] = c.get(r["currency"], 0) + r["amount_cents"]
        return {
            "ok": True,
            "filters": {"start_date": start, "end_date": end,
                        "category": cat, "keyword": keyword or None},
            "count": len(rows),
            "totals": {k: db.from_cents(v) for k, v in totals.items()},
            "by_category": {k: {cur: db.from_cents(v) for cur, v in d.items()}
                            for k, d in by_category.items()},
            "records": [db.row_to_record(r) for r in rows[-20:]],
            "records_shown": min(len(rows), 20),
        }

    def tool_update_expense(self, record_id, amount=None, category=None,
                            note=None, currency=None, date=None):
        rid = self._existing_id(record_id)
        fields = {}
        if amount is not None:
            fields["amount_cents"] = parse_amount(amount)
        if category is not None:
            fields["category"] = normalize_category(category)
        if note is not None:
            fields["note"] = str(note).strip()
        if currency is not None:
            fields["currency"] = normalize_currency(currency)
        if date is not None:
            fields["date"] = resolve_date(date, self.today_fn())
        if not fields:
            raise ToolError("No fields to change were given.")
        db.update(self.conn, rid, fields)
        return {"ok": True, "record": db.row_to_record(db.get(self.conn, rid))}

    def tool_delete_expense(self, record_id):
        rid = self._existing_id(record_id)
        record = db.row_to_record(db.get(self.conn, rid))
        db.delete(self.conn, rid)
        return {"ok": True, "deleted": record}

    def tool_convert_currency(self, amount, from_currency, to_currency):
        value = parse_amount(amount) / 100
        src = normalize_currency(from_currency)
        dst = normalize_currency(to_currency)
        rate, source, as_of = self._rate(src, dst)
        return {
            "ok": True,
            "amount": value, "from_currency": src,
            "converted": round(value * rate, 2), "to_currency": dst,
            "rate": rate, "rate_source": source, "rate_as_of": as_of,
            **({"warning": "Live rate unavailable; this rate may be outdated."}
               if source != "live" else {}),
        }

    # ------------------------------------------------------------ internals

    def _existing_id(self, record_id) -> int:
        try:
            rid = int(record_id)
        except (TypeError, ValueError):
            raise ToolError(f"Record id '{record_id}' is not a number.")
        if db.get(self.conn, rid) is None:
            raise ToolError(f"Record {rid} not found. Use query_expenses to find the id.")
        return rid

    def _rate(self, src: str, dst: str):
        if src == dst:
            return 1.0, "live", "n/a"
        # 1) live API, then save to cache
        try:
            rates = self.rate_fetcher(src)
            now = datetime.now().isoformat(timespec="seconds")
            self.conn.execute(
                "INSERT OR REPLACE INTO rate_cache (base, rates_json, fetched_at) VALUES (?,?,?)",
                (src, json.dumps(rates), now))
            self.conn.commit()
            return float(rates[dst]), "live", now
        except Exception:
            pass
        # 2) last successful live rate saved in the database
        row = self.conn.execute(
            "SELECT rates_json, fetched_at FROM rate_cache WHERE base = ?", (src,)).fetchone()
        if row and dst in json.loads(row["rates_json"]):
            return float(json.loads(row["rates_json"])[dst]), "cached", row["fetched_at"]
        # 3) built-in approximate rates
        per_usd = config.FALLBACK_RATES_PER_USD
        return per_usd[dst] / per_usd[src], "fallback", config.FALLBACK_RATES_DATE


# ---------------------------------------------------------------- schemas
# Tool descriptions sent to the LLM (Ollama / OpenAI function-calling format).

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "add_expense",
        "description": "Save ONE expense. Call once per item. Only call when the amount is known.",
        "parameters": {"type": "object", "properties": {
            "amount": {"type": "number", "description": "Positive amount, e.g. 120"},
            "category": {"type": "string", "enum": config.CATEGORIES},
            "note": {"type": "string", "description": "What was bought, e.g. 'lunch'"},
            "currency": {"type": "string", "enum": config.CURRENCIES,
                         "description": "Default TWD if the user did not say"},
            "date": {"type": "string",
                     "description": "'today', 'yesterday' or YYYY-MM-DD. Default today."},
        }, "required": ["amount", "category", "note"]}}},
    {"type": "function", "function": {
        "name": "query_expenses",
        "description": "Find expenses and get exact totals. Use for every spending question, "
                       "and to find a record id before updating or deleting.",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string", "enum": PERIODS,
                       "description": "Time range. Ignored if start_date/end_date are given."},
            "start_date": {"type": "string", "description": "YYYY-MM-DD (optional)"},
            "end_date": {"type": "string", "description": "YYYY-MM-DD (optional)"},
            "date": {"type": "string",
                     "description": "One single day: 'today', 'yesterday' or YYYY-MM-DD (optional)"},
            "category": {"type": "string", "enum": config.CATEGORIES},
            "keyword": {"type": "string", "description": "Text to search in notes, e.g. 'coffee'"},
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "update_expense",
        "description": "Change fields of an existing expense by its id.",
        "parameters": {"type": "object", "properties": {
            "record_id": {"type": "integer"},
            "amount": {"type": "number"},
            "category": {"type": "string", "enum": config.CATEGORIES},
            "note": {"type": "string"},
            "currency": {"type": "string", "enum": config.CURRENCIES},
            "date": {"type": "string"},
        }, "required": ["record_id"]}}},
    {"type": "function", "function": {
        "name": "delete_expense",
        "description": "Delete an existing expense by its id. Only when the user asks to remove it.",
        "parameters": {"type": "object", "properties": {
            "record_id": {"type": "integer"},
        }, "required": ["record_id"]}}},
    {"type": "function", "function": {
        "name": "convert_currency",
        "description": "Convert an amount between currencies with an exchange rate.",
        "parameters": {"type": "object", "properties": {
            "amount": {"type": "number"},
            "from_currency": {"type": "string", "enum": config.CURRENCIES},
            "to_currency": {"type": "string", "enum": config.CURRENCIES},
        }, "required": ["amount", "from_currency", "to_currency"]}}},
]

TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]
