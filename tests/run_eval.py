"""Run the agent with the REAL model on tests/eval_cases.json and score each case.

Usage:  python tests/run_eval.py [--cases tests/eval_cases.json] [--only 01,08]

Each case starts with a fresh in-memory database (plus optional seed records).
Dates in the cases are day offsets from today (0 = today, -1 = yesterday).
Results are printed as a table and saved to tests/results/.
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from expensemate import config, db  # noqa: E402
from expensemate.agent import ExpenseAgent, OllamaLLM, format_trace  # noqa: E402
from expensemate.tools import Toolbox  # noqa: E402


def day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def numbers_in(text: str):
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def check(expect, records, reply, tools_called):
    """Return a list of failure reasons (empty list = pass)."""
    fails = []
    if "records" in expect:
        want = sorted((r["amount"], r["currency"], r["category"], day(r["date"]))
                      for r in expect["records"])
        got = sorted((r["amount"], r["currency"], r["category"], r["date"]) for r in records)
        if want != got:
            fails.append(f"records: expected {want}, got {got}")
    if "record_count" in expect and len(records) != expect["record_count"]:
        fails.append(f"record_count: expected {expect['record_count']}, got {len(records)}")
    for n in expect.get("reply_numbers", []):
        if float(n) not in numbers_in(reply):
            fails.append(f"reply is missing the number {n}")
    for t in expect.get("tools_called", []):
        if t not in tools_called:
            fails.append(f"tool {t} was not called")
    for t in expect.get("tools_not_called", []):
        if t in tools_called:
            fails.append(f"tool {t} should not be called")
    if expect.get("reply_has_question") and "?" not in reply:
        fails.append("reply does not ask a question")
    words = expect.get("reply_contains_any")
    if words and not any(w.lower() in reply.lower() for w in words):
        fails.append(f"reply contains none of {words}")
    return fails


def run_case(case, llm):
    conn = db.connect(":memory:")
    tb = Toolbox(conn)
    for s in case.get("seed", []):
        db.insert(conn, db.to_cents(s["amount"]), s.get("currency", "TWD"),
                  s["category"], day(s["date"]), s.get("note", ""))
    agent = ExpenseAgent(llm, tb)

    traces, replies, tools_called = [], [], []
    stats = {"latency_s": 0.0, "llm_calls": 0, "tool_calls": 0, "tool_errors": 0}
    for turn in case["turns"]:
        reply = agent.handle(turn)
        replies.append(reply)
        traces.append(format_trace(agent.last_trace, agent.last_stats))
        tools_called += [s.data["name"] for s in agent.last_trace if s.kind == "tool_call"]
        for k in stats:
            stats[k] += agent.last_stats.get(k, 0)

    records = tb.run("query_expenses", {})["records"]
    fails = check(case["expect"], records, replies[-1] or "", tools_called)
    return {"id": case["id"], "type": case["type"], "input": " / ".join(case["turns"]),
            "reply": replies[-1], "passed": not fails, "fails": fails,
            "stats": {k: round(v, 2) for k, v in stats.items()}, "trace": "\n\n".join(traces)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(HERE, "eval_cases.json"))
    ap.add_argument("--only", help="comma-separated case ids")
    args = ap.parse_args()

    with open(args.cases, encoding="utf-8") as f:
        cases = json.load(f)
    if args.only:
        keep = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in keep]

    llm = OllamaLLM()
    results = []
    for case in cases:
        r = run_case(case, llm)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{r['id']} {mark:4} {r['type']:<28} {r['stats']['latency_s']:>6.1f}s  "
              f"{'; '.join(r['fails'])}")

    n = len(results)
    summary = {
        "model": config.OLLAMA_MODEL, "date": datetime.now().isoformat(timespec="seconds"),
        "cases": n, "passed": sum(r["passed"] for r in results),
        "success_rate": round(sum(r["passed"] for r in results) / n, 3) if n else 0,
        "avg_latency_s": round(sum(r["stats"]["latency_s"] for r in results) / n, 2) if n else 0,
        "avg_llm_calls": round(sum(r["stats"]["llm_calls"] for r in results) / n, 2) if n else 0,
        "tool_calls": sum(r["stats"]["tool_calls"] for r in results),
        "tool_errors": sum(r["stats"]["tool_errors"] for r in results),
    }
    print("\n" + json.dumps(summary, indent=2))

    out_dir = os.path.join(HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"eval-{datetime.now():%Y%m%d-%H%M%S}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
