"""Command-line chat for ExpenseMate.

Usage:  python run.py [--trace]
Type 'quit' to exit, '/trace' to turn the execution trace on or off.
"""
import argparse
import json
import os
from datetime import datetime

from . import config, db
from .agent import ExpenseAgent, OllamaLLM, format_trace
from .tools import Toolbox


def save_trace(agent, reply):
    """Append the request's full trace to logs/trace-YYYY-MM-DD.jsonl."""
    os.makedirs(config.LOG_DIR, exist_ok=True)
    path = os.path.join(config.LOG_DIR, f"trace-{datetime.now():%Y-%m-%d}.jsonl")
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "model": config.OLLAMA_MODEL,
        "steps": [{"kind": s.kind, **s.data} for s in agent.last_trace],
        "reply": reply, "stats": agent.last_stats,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="ExpenseMate expense tracker agent")
    parser.add_argument("--trace", action="store_true", help="print the execution trace")
    args = parser.parse_args()

    conn = db.connect(config.DB_PATH)
    agent = ExpenseAgent(OllamaLLM(), Toolbox(conn))
    show_trace = args.trace

    print(f"ExpenseMate (model: {config.OLLAMA_MODEL}, database: {config.DB_PATH})")
    print("Type an expense like 'lunch 120, MRT 30' or a question. 'quit' to exit.\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in {"quit", "exit"}:
            break
        if text == "/trace":
            show_trace = not show_trace
            print(f"(trace {'on' if show_trace else 'off'})")
            continue

        reply = agent.handle(text)
        save_trace(agent, reply)
        if show_trace:
            print(format_trace(agent.last_trace, agent.last_stats))
        print(f"ExpenseMate> {reply}\n")


if __name__ == "__main__":
    main()
