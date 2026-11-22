# HW2 – ExpenseMate: Working Agent + Tool / ReAct

ExpenseMate turns the HW1 plan into a running agent: a hand-written ReAct loop in
Python, a local LLM served by Ollama (`qwen2.5:7b`), five Python tools on SQLite
and an exchange-rate API, and a Podman container for the agent application.

## 1. Agent source code

| File | What it does |
|---|---|
| `src/expensemate/agent.py` | ReAct loop: send messages + tool schemas to the LLM, run requested tools, return results as observations, repeat (max 5 tool steps). Also records the execution trace and stats (latency, LLM calls, tool calls, tool errors). |
| `src/expensemate/tools.py` | The five tools, their input validation, and their JSON schemas for the LLM. |
| `src/expensemate/db.py` | SQLite storage. Amounts are stored as integer cents, so totals are exact. |
| `src/expensemate/prompts.py` | System prompt (today's date, categories, rules: always use tools for numbers, one call per item, ask when amount is missing). |
| `src/expensemate/main.py` | Command-line chat; `--trace` prints every step; every request is saved to `logs/trace-*.jsonl`. |
| `Containerfile` | Podman image for the agent; the database lives in the `/data` volume; Ollama stays on the host. |

### How one request flows

```
User Input
   ↓
Agent loop sends: system prompt + chat history + tool schemas → LLM
   ↓
LLM Decision: final answer?  ── yes ──► Final answer to user
   │ no (tool calls)
   ↓
Tool Call (Python function) → Observation {"ok": ..., ...}
   ↓
Observation added to the messages → back to LLM Decision
(stops after 5 tool steps)
```

**Why ReAct:** some requests need several dependent steps — e.g. "food this week
in rupiah" needs `query_expenses` first, then `convert_currency` with the total
it returned. A single LLM call cannot know the total in advance.

**Memory:** long-term = the SQLite database; short-term = the chat history of the
current session (last 10 user turns), so follow-ups like "sorry, it was 130"
refer to the record just saved.

### Changes compared with the HW1 plan

- `update_expense` and `delete_expense` were planned for HW4 but are already working.
- `query_expenses` takes a `period` name (`this_week`, `last_month`, …) that the
  tool turns into exact dates, so the LLM does not have to calculate dates.
  It also accepts `date` for a single day. In testing (2026-11-22) the model kept
  sending `{"date": "today"}`, which first failed with "unexpected keyword argument"
  and needed a retry (3 LLM calls, 7.1 s). After adding `date`, the same question
  takes 2 LLM calls (about 4.5 s, 3 runs) with no tool error.
- Currency conversion uses a three-level fallback: live API → last cached live
  rate (saved in SQLite) → built-in approximate rate, and warns when not live.
- The PEAS section of HW1 was corrected after the teacher's feedback (read-only
  tools are sensors, not actuators; the LLM server and container are part of the
  agent, not its environment).

## 2. Tool definitions

All tools return `{"ok": true, ...}` on success or `{"ok": false, "error": "..."}`.
They never raise exceptions into the agent loop; the error text is written for
the LLM so it can correct the call or ask the user.

| Tool | Purpose | Input | Output | Error handling |
|---|---|---|---|---|
| `add_expense` | Save one expense | `amount` (number), `category` (one of 8), `note`, `currency` (TWD/IDR/USD, default TWD; "NTD" accepted), `date` (`today`, `yesterday` or YYYY-MM-DD; default today) | saved record with id | amount ≤ 0 or not a number; unknown category; unsupported currency; invalid or future date → error, nothing saved |
| `query_expenses` | Find records and exact totals | `period` (`today`, `yesterday`, `this_week`, `last_week`, `this_month`, `last_month`, `all`) or `start_date`/`end_date`, or `date` for one day; optional `category`, `keyword` (searches notes) | `count`, `totals` per currency, `by_category`, up to 20 `records` | no match → `count: 0`, empty totals (not an error); bad period/date/category → error |
| `update_expense` | Correct a record | `record_id` + any of amount, category, note, currency, date | updated record | id not a number / not found; no fields given; same validation as add |
| `delete_expense` | Remove a record | `record_id` | the deleted record | id not a number / not found |
| `convert_currency` | Convert an amount | `amount`, `from_currency`, `to_currency` | `converted`, `rate`, `rate_source` (`live`/`cached`/`fallback`), `rate_as_of`, optional `warning` | API down → cached rate → built-in rate, with a warning; bad amount/currency → error |

Unknown tool names and wrong argument names are also caught and returned as errors.

## 3. Execution trace

Real run on 2026-11-19 with `python run.py --trace`, model `qwen2.5:7b` on an
RTX 3050 laptop GPU (fresh database). This is the HW1 user scenario.

**Request 1 — record three expenses (one decision, three tool calls):**

```
[User Input]   lunch 120, bubble tea 65, MRT 30
[Decision 1]   call add_expense, add_expense, add_expense
[Tool Call]    add_expense({"category": "Food", "note": "lunch", "amount": 120})
[Observation]  {"ok": true, "record": {"id": 1, "amount": 120, "currency": "TWD", "category": "Food", "date": "2026-11-19", "note": "lunch"}}
[Tool Call]    add_expense({"amount": 65, "category": "Food", "note": "bubble tea"})
[Observation]  {"ok": true, "record": {"id": 2, "amount": 65, "currency": "TWD", "category": "Food", "date": "2026-11-19", "note": "bubble tea"}}
[Tool Call]    add_expense({"amount": 30, "category": "Transport", "note": "MRT"})
[Observation]  {"ok": true, "record": {"id": 3, "amount": 30, "currency": "TWD", "category": "Transport", "date": "2026-11-19", "note": "MRT"}}
[Final Answer] I've recorded your expenses for today:
               - 120 TWD for lunch (Food)
               - 65 TWD for bubble tea (Food)
               - 30 TWD for MRT (Transport)
[Stats]        {"latency_s": 21.63, "llm_calls": 2, "tool_calls": 3, "tool_errors": 0}
```

(The first request includes loading the model into GPU memory; later requests take 3–8 s.)

**Request 2 — query, then convert:**

```
[User Input]   How much did I spend on food this week, and how much is that in rupiah?
[Decision 1]   call query_expenses, convert_currency
[Tool Call]    query_expenses({"category": "Food", "period": "this_week"})
[Observation]  {"ok": true, "filters": {"start_date": "2026-11-16", "end_date": "2026-11-19", "category": "Food"}, "count": 2, "totals": {"TWD": 185}, ...}
[Tool Call]    convert_currency({"amount": 185, "from_currency": "TWD", "to_currency": "IDR"})
[Observation]  {"ok": true, "amount": 185.0, "converted": 102322.59, "to_currency": "IDR", "rate": 553.095094, "rate_source": "live", ...}
[Final Answer] This week, you spent 185 TWD on food. That is approximately 102,323 rupiah (IDR).
[Stats]        {"latency_s": 7.25, "llm_calls": 2, "tool_calls": 2, "tool_errors": 0}
```

**Request 3 — correction using short-term memory (id 2 came from the chat history):**

```
[User Input]   Change the bubble tea to 55
[Decision 1]   call update_expense
[Tool Call]    update_expense({"record_id": 2, "amount": 55, "category": "Food", "note": "bubble tea"})
[Observation]  {"ok": true, "record": {"id": 2, "amount": 55, "currency": "TWD", "category": "Food", "date": "2026-11-19", "note": "bubble tea"}}
[Final Answer] I've updated the bubble tea expense to 55 TWD. ...
               Total food expenses this week are 165 TWD, which is approximately 89,357 rupiah (IDR).
[Stats]        {"latency_s": 6.76, "llm_calls": 2, "tool_calls": 1, "tool_errors": 0}
```

The records saved in all three requests are correct. However, request 2 and
request 3 show two real problems, discussed as Failures 4 and 5 below: in
request 2 the model called `convert_currency` *in the same step* as
`query_expenses`, so it supplied 185 before seeing the query result (it was right
only because the numbers were visible in the chat history); in request 3 it
added a total it did not compute with a tool, and that total is wrong
(120 + 55 = 175, not 165).

## 4. Failure cases

### Failure 1 — the LLM server cannot be reached

- **Situation:** Ollama is not running, the model is not downloaded, or the
  container cannot reach the host. Observed when running without Ollama:
  ```
  [User Input]   coffee 80
  [Error/llm]    ConnectionError: Failed to connect to Ollama. ...
  ExpenseMate> Sorry, I could not reach the language model. Check that Ollama is
               running at http://localhost:11434 and the model is pulled.
  ```
- **Cause:** the agent depends on an external LLM server.
- **Solution:** the loop catches the error, shows a clear message instead of
  crashing, and removes the unanswered turn from memory (or keeps it if tools
  already ran, so saved records are not forgotten). The README's Troubleshooting
  section covers `OLLAMA_HOST=0.0.0.0` for containers.
  Tested in `test_llm_down_gives_friendly_message_and_forgets_turn`.

### Failure 2 — the model sends invalid tool input

- **Situation:** the model calls `add_expense` with a negative amount, a made-up
  category ("Pets"), an unsupported currency, a future date, or a date phrase the
  tool does not understand ("last friday").
- **Cause:** small local models do not always follow the tool schema.
- **Solution:** every tool validates its input and returns
  `{"ok": false, "error": "..."}` without saving anything. The error goes back to
  the LLM as an observation so it can retry with fixed input or ask the user.
  Tested in `test_add_rejects_bad_input` (7 cases) and
  `test_tool_error_is_given_back_to_llm`.
- **Remaining limit:** validation cannot catch a *plausible but wrong* value, e.g.
  the model guessing an amount the user never gave. This is the target of the
  HW3 enhancement (self-check before saving).

### Failure 3 — the model keeps calling tools (loop)

- **Situation:** the model repeats `query_expenses` instead of answering.
- **Cause:** unclear tool results or a weak model can make the LLM "stuck".
- **Solution:** the loop stops after 5 tool steps
  (`EXPENSEMATE_MAX_STEPS`) and tells the user to try a simpler request.
  Tested in `test_step_limit_stops_loops`.

### Failure 4 — the model invents a value instead of asking or using a tool (observed)

- **Situation:** eval case 12, input `I bought a book` (no amount):
  ```
  [Tool Call]    add_expense({"currency": "TWD", "date": "today", "amount": 500, "category": "Education", "note": "book"})
  [Final Answer] I've recorded your book purchase for today. Here's the detail: 500 TWD spent on Education.
  ```
  The same problem appears in the trace above (request 3: "165 TWD" computed by
  the model, true value 175) and in eval case 08, where the model called
  `query_expenses` with a wrong argument name (`date`), then in the same step
  called `convert_currency` with an invented amount of 1 TWD and reported
  "you spent 1 TWD on food" (true value 200).
- **Cause:** the 7B model sometimes ignores the system-prompt rules ("ask when
  the amount is missing", "never do arithmetic yourself") and fills gaps with
  guesses. Tool validation cannot catch this, because 500 is a valid amount.
  Calling dependent tools in parallel means the second call cannot use the
  first call's result.
- **Solution (HW3):** add a verification step before acting — check that every
  number in an `add_expense` call appears in the user's message, that a
  `convert_currency` amount comes from an earlier observation, and that every
  number in the final answer appears in a tool result; on a mismatch, send the
  problem back to the model (self-reflection) or ask the user.

### Failure 5 — the model asks the user for a record id it could look up (observed)

- **Situation:** eval cases 09 and 10 start with records already in the
  database but an empty chat history (as after restarting the program):
  ```
  [User Input]   Change the bubble tea to 55
  [Final Answer] Could you please provide me with the ID of the bubble tea expense so I can update it?
  ```
- **Cause:** the id is not in the chat history, and the model does not follow
  the prompt instruction to find it with `query_expenses(keyword=...)`. In the
  live trace above the same request worked because the id was in the history.
- **Solution:** let `update_expense` / `delete_expense` accept a `keyword`
  instead of an id (the tool finds the record and asks to clarify when several
  match), or add a planning step that runs the lookup first.

## 5. Testing evidence

**Unit and agent-loop tests** (`python -m pytest -q`): 24 tests, all passing.
They cover tool validation, exact totals (0.1 + 0.2 = 0.3), currency separation,
date periods, update/delete, the live → cached → fallback rate chain, and the
agent loop with a scripted fake LLM (multi-tool requests, multi-step
query-then-convert, error observations, step limit, LLM down, history trimming).

**End-to-end evaluation** (`python tests/run_eval.py`): 13 cases with the real
model — single add, multiple add, date, currency, Chinese input, category
query, total query, multi-step query + convert, correction, delete, follow-up
memory, missing amount (must ask), off-topic (must refuse). Each case is scored
pass/fail against the expected database state or reply, and the runner reports
success rate, average latency, average LLM calls and tool errors.
This set is also the "before" baseline for HW3.

Results on 2026-11-19, `qwen2.5:7b`, RTX 3050 6 GB laptop GPU
(full output: `tests/results/eval-20261119-153955.json`):

| ID | Type | Result | Reason for failure |
|---|---|---|---|
| 01 | Single add | Pass | |
| 02 | Multiple add | Pass | |
| 03 | Add with date | Pass | |
| 04 | Add with currency | Pass | |
| 05 | Chinese input (午餐 100) | Pass | |
| 06 | Query by category | Pass | |
| 07 | Query total | Pass | |
| 08 | Multi-step (query + convert) | **Fail** | wrong argument name, then convert called with invented amount (Failure 4) |
| 09 | Correction | **Fail** | asked the user for the id instead of looking it up (Failure 5) |
| 10 | Delete | **Fail** | same as 09 (Failure 5) |
| 11 | Follow-up (memory) | Pass | |
| 12 | Missing amount | **Fail** | invented 500 TWD instead of asking (Failure 4) |
| 13 | Off-topic | Pass | |

| Metric | Result |
|---|---|
| Cases passed | 9 / 13 |
| Success rate | 69.2% |
| Avg latency per case (s) | 3.95 |
| Avg LLM calls per case | 1.92 |
| Tool calls / tool errors | 14 / 1 |

Against the HW1 targets: latency (< 15 s) and LLM calls (≤ 3) are met; task
success (≥ 85%) and clarification correctness (100%) are not yet met. All
recording and query cases pass; the failures are in multi-step reasoning,
looking up ids, and asking instead of guessing. These results are the
"before" baseline for the HW3 enhancement.

**Podman build and container test** (GitHub Actions, `.github/workflows/podman.yml`).
Every push to the repository builds the image with Podman on a GitHub Ubuntu 24.04
server and runs three checks inside the container. Latest run (2026-11-22), all steps passed:
https://github.com/christianaxel99/ExpenseMate/actions/runs/44989755918

| Step | Command | Result |
|---|---|---|
| Build the image | `podman build -t expensemate .` | Pass |
| Unit and agent-loop tests in the container | `podman run --rm --entrypoint python expensemate -m pytest -v` | Pass (24 tests) |
| Start the chat in the container | `printf 'lunch 120\nquit\n' \| podman run -i --rm -v expensemate-data:/data expensemate` | Pass: with no LLM server on that machine the agent replies "could not reach the language model" instead of crashing, and saves the trace in the `/data` volume |

The full conversation with the real model (container talking to Ollama on the host)
was run without the container on the development laptop (sections 3 and 5 above).
The laptop could not run Podman itself yet, because a Windows system-file error
prevents installing WSL, which Podman needs on Windows. The GitHub run above is the
evidence that the Containerfile builds and the agent runs inside Podman.
