"""The agent loop (ReAct style: decide -> act with a tool -> observe -> decide).

The LLM decides which tool to call; this code runs the tool, gives the
result back to the LLM as an observation, and repeats until the LLM writes
a final answer or the step limit is reached.
"""
import json
import time
from dataclasses import dataclass, field
from datetime import date

from . import config
from .prompts import build_system_prompt
from .tools import TOOL_SCHEMAS


# ---------------------------------------------------------------- LLM wrapper

@dataclass
class LLMReply:
    content: str
    tool_calls: list  # list of {"name": str, "arguments": dict}


class OllamaLLM:
    """Thin wrapper so the agent does not depend on the Ollama client directly
    (tests replace it with a scripted fake)."""

    def __init__(self, model=config.OLLAMA_MODEL, host=config.OLLAMA_HOST):
        from ollama import Client
        self.model = model
        self.client = Client(host=host)

    def chat(self, messages, tools) -> LLMReply:
        resp = self.client.chat(model=self.model, messages=messages, tools=tools,
                                options={"temperature": 0})
        msg = resp.message
        calls = []
        for tc in msg.tool_calls or []:
            args = tc.function.arguments
            if isinstance(args, str):  # some models return a JSON string
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            calls.append({"name": tc.function.name, "arguments": dict(args or {})})
        return LLMReply(content=msg.content or "", tool_calls=calls)


# ---------------------------------------------------------------- agent

@dataclass
class Step:
    """One entry of the execution trace."""
    kind: str      # "user" | "decision" | "tool_call" | "observation" | "final" | "error"
    data: dict = field(default_factory=dict)


class ExpenseAgent:
    def __init__(self, llm, toolbox, max_steps=config.MAX_TOOL_STEPS,
                 today_fn=date.today, max_history_turns=10):
        self.llm = llm
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.today_fn = today_fn
        self.max_history_turns = max_history_turns
        self.history = []        # short-term memory: earlier messages this session
        self.last_trace = []     # steps of the most recent request
        self.last_stats = {}

    def _messages(self):
        system = {"role": "system", "content": build_system_prompt(self.today_fn())}
        return [system] + self.history

    def _trim_history(self):
        """Keep only the last N user turns. Cut only at user messages so a
        tool call is never separated from its result."""
        user_idx = [i for i, m in enumerate(self.history) if m["role"] == "user"]
        if len(user_idx) > self.max_history_turns:
            self.history = self.history[user_idx[-self.max_history_turns]:]

    def handle(self, user_text: str) -> str:
        """Process one user message and return the agent's reply."""
        trace = [Step("user", {"text": user_text})]
        self.last_trace = trace
        started = time.perf_counter()
        llm_calls = tool_calls = tool_errors = 0

        start_len = len(self.history)
        self.history.append({"role": "user", "content": user_text})
        reply = None

        for step in range(1, self.max_steps + 2):  # +1 for the final answer
            try:
                out = self.llm.chat(self._messages(), TOOL_SCHEMAS)
                llm_calls += 1
            except Exception as e:
                trace.append(Step("error", {"where": "llm", "error": f"{type(e).__name__}: {e}"}))
                reply = ("Sorry, I could not reach the language model. Check that Ollama "
                         f"is running at {config.OLLAMA_HOST} and the model is pulled.")
                if step == 1:   # nothing happened yet: forget this turn
                    del self.history[start_len:]
                else:           # tools already ran: keep them so the record stays known
                    self.history.append({"role": "assistant", "content": reply})
                break

            if not out.tool_calls:
                reply = out.content.strip() or "(no answer)"
                trace.append(Step("final", {"text": reply}))
                self.history.append({"role": "assistant", "content": reply})
                break

            if step > self.max_steps:
                reply = ("Sorry, I stopped because this request needed too many steps. "
                         "Please try again with a simpler request.")
                trace.append(Step("error", {"where": "loop", "error": "step limit reached"}))
                self.history.append({"role": "assistant", "content": reply})
                break

            trace.append(Step("decision", {
                "step": step, "thought": out.content.strip(),
                "tools": [c["name"] for c in out.tool_calls]}))
            self.history.append({
                "role": "assistant", "content": out.content,
                "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}}
                               for c in out.tool_calls]})

            for call in out.tool_calls:
                trace.append(Step("tool_call", {"name": call["name"], "arguments": call["arguments"]}))
                result = self.toolbox.run(call["name"], call["arguments"])
                tool_calls += 1
                tool_errors += 0 if result.get("ok") else 1
                trace.append(Step("observation", {"name": call["name"], "result": result}))
                self.history.append({"role": "tool", "tool_name": call["name"],
                                     "content": json.dumps(result, ensure_ascii=False)})

        self._trim_history()
        self.last_stats = {
            "latency_s": round(time.perf_counter() - started, 2),
            "llm_calls": llm_calls, "tool_calls": tool_calls, "tool_errors": tool_errors,
        }
        return reply


# ---------------------------------------------------------------- trace output

def format_trace(trace, stats=None) -> str:
    """Readable trace: User Input -> Decision -> Tool Call -> Observation -> ... -> Final."""
    lines = []
    for s in trace:
        d = s.data
        if s.kind == "user":
            lines.append(f"[User Input]   {d['text']}")
        elif s.kind == "decision":
            thought = f" — {d['thought']}" if d.get("thought") else ""
            lines.append(f"[Decision {d['step']}]   call {', '.join(d['tools'])}{thought}")
        elif s.kind == "tool_call":
            lines.append(f"[Tool Call]    {d['name']}({json.dumps(d['arguments'], ensure_ascii=False)})")
        elif s.kind == "observation":
            lines.append(f"[Observation]  {json.dumps(d['result'], ensure_ascii=False)}")
        elif s.kind == "final":
            lines.append(f"[Final Answer] {d['text']}")
        elif s.kind == "error":
            lines.append(f"[Error/{d['where']}]  {d['error']}")
    if stats:
        lines.append(f"[Stats]        {json.dumps(stats)}")
    return "\n".join(lines)
