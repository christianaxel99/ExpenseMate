"""Tests for the agent loop, using a scripted fake LLM instead of Ollama.

They check the loop's logic (tool calls, observations, step limit, errors),
not the quality of the real model. Real-model behaviour is checked with
tests/run_eval.py.
"""
from datetime import date

from expensemate import db
from expensemate.agent import ExpenseAgent, LLMReply, format_trace
from expensemate.tools import Toolbox

TODAY = date(2026, 11, 19)


class ScriptedLLM:
    """Returns pre-written replies in order and records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def chat(self, messages, tools):
        self.seen.append([dict(m) for m in messages])
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def call(name, **args):
    return {"name": name, "arguments": args}


def make_agent(replies, max_steps=5):
    llm = ScriptedLLM(replies)
    tb = Toolbox(db.connect(":memory:"), today_fn=lambda: TODAY,
                 rate_fetcher=lambda base: {"IDR": 500.0, "TWD": 1.0, "USD": 0.031})
    return ExpenseAgent(llm, tb, max_steps=max_steps, today_fn=lambda: TODAY), llm, tb


def test_two_items_then_answer():
    agent, llm, tb = make_agent([
        LLMReply("", [call("add_expense", amount=120, category="Food", note="lunch"),
                      call("add_expense", amount=30, category="Transport", note="MRT")]),
        LLMReply("Saved lunch 120 TWD and MRT 30 TWD.", []),
    ])
    reply = agent.handle("lunch 120, MRT 30")
    assert reply == "Saved lunch 120 TWD and MRT 30 TWD."
    assert tb.run("query_expenses", {})["totals"] == {"TWD": 150}
    # the second LLM call must see both tool results as observations
    tool_msgs = [m for m in llm.seen[1] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and '"ok": true' in tool_msgs[0]["content"]
    assert agent.last_stats["llm_calls"] == 2 and agent.last_stats["tool_calls"] == 2


def test_multi_step_query_then_convert():
    agent, llm, tb = make_agent([
        LLMReply("", [call("add_expense", amount=200, category="Food", note="dinner")]),
        LLMReply("Saved.", []),
        LLMReply("", [call("query_expenses", period="this_week", category="Food")]),
        LLMReply("", [call("convert_currency", amount=200, from_currency="TWD", to_currency="IDR")]),
        LLMReply("You spent 200 TWD on food this week, about 100,000 IDR.", []),
    ])
    agent.handle("dinner 200")
    reply = agent.handle("food this week in rupiah?")
    assert "100,000 IDR" in reply
    kinds = [s.kind for s in agent.last_trace]
    assert kinds == ["user", "decision", "tool_call", "observation",
                     "decision", "tool_call", "observation", "final"]
    # short-term memory: the second request still contains the first one
    assert any(m.get("content") == "dinner 200" for m in llm.seen[-1])


def test_tool_error_is_given_back_to_llm():
    agent, llm, _ = make_agent([
        LLMReply("", [call("add_expense", amount=-5, category="Food", note="x")]),
        LLMReply("The amount must be positive. How much was it?", []),
    ])
    reply = agent.handle("food -5")
    assert "positive" in reply
    assert agent.last_stats["tool_errors"] == 1
    assert "greater than 0" in [m for m in llm.seen[1] if m["role"] == "tool"][0]["content"]


def test_step_limit_stops_loops():
    looping = [LLMReply("", [call("query_expenses")]) for _ in range(10)]
    agent, llm, _ = make_agent(looping, max_steps=3)
    reply = agent.handle("how much?")
    assert "too many steps" in reply
    assert agent.last_stats["tool_calls"] == 3 and agent.last_stats["llm_calls"] == 4


def test_llm_down_gives_friendly_message_and_forgets_turn():
    agent, _, _ = make_agent([ConnectionError("refused")])
    reply = agent.handle("lunch 120")
    assert "could not reach the language model" in reply
    assert agent.history == []


def test_history_is_trimmed_at_user_turns():
    replies = []
    for _ in range(4):
        replies += [LLMReply("", [call("query_expenses")]), LLMReply("ok", [])]
    agent, _, _ = make_agent(replies)
    agent.max_history_turns = 2
    for i in range(4):
        agent.handle(f"q{i}")
    assert agent.history[0] == {"role": "user", "content": "q2"}
    assert sum(m["role"] == "user" for m in agent.history) == 2


def test_format_trace_has_all_parts():
    agent, _, _ = make_agent([
        LLMReply("Need to save it.", [call("add_expense", amount=80, category="Food", note="coffee")]),
        LLMReply("Saved coffee 80 TWD.", []),
    ])
    agent.handle("coffee 80")
    text = format_trace(agent.last_trace, agent.last_stats)
    for part in ["[User Input]", "[Decision 1]", "[Tool Call]", "[Observation]",
                 "[Final Answer]", "[Stats]", "Need to save it."]:
        assert part in text
