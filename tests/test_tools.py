"""Unit tests for the tools (no LLM needed)."""
from datetime import date

import pytest

from expensemate import db
from expensemate.tools import Toolbox, resolve_period

TODAY = date(2026, 11, 19)  # a Thursday


def offline(base):
    raise OSError("no network in tests")


@pytest.fixture
def tb():
    return Toolbox(db.connect(":memory:"), today_fn=lambda: TODAY, rate_fetcher=offline)


def test_add_uses_defaults(tb):
    r = tb.run("add_expense", {"amount": 120, "note": "lunch", "category": "food"})
    assert r["ok"]
    assert r["record"] == {"id": 1, "amount": 120, "currency": "TWD",
                           "category": "Food", "date": "2026-11-19", "note": "lunch"}


def test_add_accepts_string_amount_and_ntd_alias(tb):
    r = tb.run("add_expense", {"amount": "1,250.5", "note": "shoes",
                               "category": "Shopping", "currency": "ntd", "date": "yesterday"})
    assert r["ok"] and r["record"]["amount"] == 1250.5
    assert r["record"]["currency"] == "TWD" and r["record"]["date"] == "2026-11-18"


@pytest.mark.parametrize("args, msg", [
    ({"amount": 0, "note": "x"}, "greater than 0"),
    ({"amount": -5, "note": "x"}, "greater than 0"),
    ({"amount": "abc", "note": "x"}, "not a number"),
    ({"amount": 10, "note": "x", "category": "Pets"}, "Unknown category"),
    ({"amount": 10, "note": "x", "currency": "EUR"}, "Unsupported currency"),
    ({"amount": 10, "note": "x", "date": "2026-12-25"}, "future"),
    ({"amount": 10, "note": "x", "date": "last friday"}, "not valid"),
])
def test_add_rejects_bad_input(tb, args, msg):
    r = tb.run("add_expense", args)
    assert not r["ok"] and msg in r["error"]
    assert tb.run("query_expenses", {})["count"] == 0  # nothing saved


def test_query_totals_are_exact(tb):
    for amt, cat in [(120, "Food"), (65, "Food"), (30, "Transport"), (0.1, "Food"), (0.2, "Food")]:
        tb.run("add_expense", {"amount": amt, "category": cat, "note": "x"})
    r = tb.run("query_expenses", {"period": "today", "category": "Food"})
    assert r["count"] == 4
    assert r["totals"] == {"TWD": 185.3}   # 0.1 + 0.2 is exact, not 0.30000000000000004
    all_ = tb.run("query_expenses", {})
    assert all_["by_category"] == {"Food": {"TWD": 185.3}, "Transport": {"TWD": 30}}


def test_query_separates_currencies(tb):
    tb.run("add_expense", {"amount": 100, "category": "Food", "note": "a"})
    tb.run("add_expense", {"amount": 5, "category": "Food", "note": "b", "currency": "USD"})
    assert tb.run("query_expenses", {})["totals"] == {"TWD": 100, "USD": 5}


def test_query_keyword_and_empty_result(tb):
    tb.run("add_expense", {"amount": 65, "category": "Food", "note": "bubble tea"})
    assert tb.run("query_expenses", {"keyword": "bubble"})["records"][0]["id"] == 1
    empty = tb.run("query_expenses", {"keyword": "pizza"})
    assert empty["ok"] and empty["count"] == 0 and empty["totals"] == {}


def test_periods():
    assert resolve_period("this_week", TODAY) == ("2026-11-16", "2026-11-19")
    assert resolve_period("last_week", TODAY) == ("2026-11-09", "2026-11-15")
    assert resolve_period("this_month", TODAY) == ("2026-11-01", "2026-11-19")
    assert resolve_period("last_month", TODAY) == ("2026-10-01", "2026-10-31")
    assert resolve_period("all", TODAY) == (None, None)


def test_update_and_delete(tb):
    tb.run("add_expense", {"amount": 65, "category": "Food", "note": "bubble tea"})
    r = tb.run("update_expense", {"record_id": "1", "amount": 55})
    assert r["ok"] and r["record"]["amount"] == 55 and r["record"]["note"] == "bubble tea"
    assert not tb.run("update_expense", {"record_id": 1})["ok"]          # no fields
    assert "not found" in tb.run("update_expense", {"record_id": 99, "amount": 1})["error"]
    assert tb.run("delete_expense", {"record_id": 1})["deleted"]["amount"] == 55
    assert tb.run("query_expenses", {})["count"] == 0
    assert "not found" in tb.run("delete_expense", {"record_id": 1})["error"]


def test_convert_live_then_cached_then_fallback():
    conn = db.connect(":memory:")
    live = Toolbox(conn, today_fn=lambda: TODAY,
                   rate_fetcher=lambda base: {"IDR": 500.0, "USD": 0.031, "TWD": 1.0})
    r = live.run("convert_currency", {"amount": 200, "from_currency": "TWD", "to_currency": "IDR"})
    assert r["converted"] == 100000 and r["rate_source"] == "live" and "warning" not in r

    cached = Toolbox(conn, today_fn=lambda: TODAY, rate_fetcher=offline)
    r = cached.run("convert_currency", {"amount": 200, "from_currency": "NTD", "to_currency": "IDR"})
    assert r["converted"] == 100000 and r["rate_source"] == "cached" and "warning" in r

    fresh = Toolbox(db.connect(":memory:"), today_fn=lambda: TODAY, rate_fetcher=offline)
    r = fresh.run("convert_currency", {"amount": 33, "from_currency": "TWD", "to_currency": "USD"})
    assert r["converted"] == 1.0 and r["rate_source"] == "fallback"


def test_unknown_tool_and_bad_argument_names(tb):
    assert "Unknown tool" in tb.run("make_coffee", {})["error"]
    assert "Bad arguments" in tb.run("add_expense", {"price": 10})["error"]
