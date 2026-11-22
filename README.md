# ExpenseMate — a natural-language expense tracker agent

Type expenses the way you would say them (`lunch 120, MRT 30`) and ask questions
(`How much did I spend on food this week, in rupiah?`). ExpenseMate turns each
sentence into records in a local SQLite database, answers with exact totals and
converts currencies.

It is a single tool-using **ReAct agent**: a local LLM (served by Ollama) decides
which tool to call, the agent runs the tool, gives the result back to the LLM,
and repeats until it can answer. All arithmetic is done by tools, never by the LLM.

```
User (CLI) ──► Agent loop (ReAct, max 5 tool steps) ──► Ollama LLM (host)
                   │            │
                Tools      Chat history (short-term memory)
                   │
     SQLite database (volume)   Exchange-rate API (+ cached / fallback rate)
```

## Repository layout

```
README.md            this file
requirements.txt     Python dependencies
Containerfile        Podman image for the agent application
run.py               starts the command-line chat
src/expensemate/
    agent.py         agent loop, Ollama wrapper, execution trace
    tools.py         the 5 tools + their schemas for the LLM
    db.py            SQLite storage (amounts stored as integer cents)
    prompts.py       system prompt
    config.py        settings (environment variables)
    main.py          command-line chat
tests/
    test_tools.py    unit tests for the tools (no LLM needed)
    test_agent.py    agent-loop tests with a scripted fake LLM
    eval_cases.json  13 end-to-end test cases for the real model
    run_eval.py      runs the cases against the real model and scores them
documents/
    HW2_report.md    tool definitions, execution trace, failure cases
```

## Tools

| Tool | Purpose | Input | Output | Error handling |
|---|---|---|---|---|
| `add_expense` | Save one expense | amount, category, note, currency (default TWD), date (default today) | the saved record with its id | rejects amount ≤ 0, non-numbers, unknown category/currency, invalid or future dates |
| `query_expenses` | Totals and lists | period (`today`, `this_week`, `last_month`, …), start/end date or one `date`, category, keyword | count, totals per currency, totals per category, up to 20 records | empty result returns count 0; bad period/date returns an error |
| `update_expense` | Correct a record | record_id + fields to change | the updated record | "record not found", "no fields to change" |
| `delete_expense` | Remove a record | record_id | the deleted record | "record not found" |
| `convert_currency` | Convert an amount | amount, from_currency, to_currency (TWD, IDR, USD) | converted amount, rate, rate source | live API → last cached rate → built-in approximate rate, with a warning |

Every tool returns `{"ok": true, ...}` or `{"ok": false, "error": "..."}`; errors
are given back to the LLM as an observation so it can fix the call or ask the user.

## System requirements

- Windows, macOS or Linux, 16 GB RAM recommended
- [Ollama](https://ollama.com) with a tool-calling model (default `qwen2.5:7b`, about 4.7 GB)
- [Podman](https://podman.io) to run the agent in a container
- Python 3.12 only if you run it without the container

## Installation

1. Install Ollama and download the model:
   ```
   winget install Ollama.Ollama
   ollama pull qwen2.5:7b
   ```
2. Install Podman and start its virtual machine (Windows/macOS):
   ```
   winget install RedHat.Podman
   podman machine init
   podman machine start
   ```
3. Get the code:
   ```
   git clone <this repository URL>
   cd ExpenseMate
   ```

## Build and run (Podman)

```
podman build -t expensemate .
podman run -it --rm -v expensemate-data:/data expensemate
```

Add `--trace` at the end to print every step (decision → tool call → observation):

```
podman run -it --rm -v expensemate-data:/data expensemate --trace
```

The database is kept in the `expensemate-data` volume, so your expenses survive restarts.

## Run without a container (for development)

```
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python run.py --trace
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` (container: `http://host.containers.internal:11434`) | where Ollama runs |
| `OLLAMA_MODEL` | `qwen2.5:7b` | model name |
| `EXPENSEMATE_DB` | `data/expenses.db` (container: `/data/expenses.db`) | database file |
| `EXPENSEMATE_LOG_DIR` | `logs` (container: `/data/logs`) | where traces are saved |
| `EXPENSEMATE_MAX_STEPS` | `5` | maximum tool steps per request |
| `EXCHANGE_API_URL` | `https://open.er-api.com/v6/latest/{base}` | exchange-rate API (free, no key) |

Example: `podman run -it --rm -e OLLAMA_MODEL=llama3.1:8b -v expensemate-data:/data expensemate`

## Test

Unit and agent-loop tests (fast, no Ollama needed):

```
podman run --rm --entrypoint python expensemate -m pytest -q
```
or locally: `python -m pytest -q`

End-to-end evaluation with the real model (needs Ollama running):

```
podman run --rm --entrypoint python expensemate tests/run_eval.py
```
or locally: `python tests/run_eval.py`. Results are saved in `tests/results/`.

Automatic check on GitHub: every push runs `.github/workflows/podman.yml` on a
GitHub Ubuntu server. It builds the image with `podman build`, runs the tests inside
the container, and starts the chat once to check that it handles a missing LLM
server without crashing. The result is shown on the repository's **Actions** tab.

## Expected output

```
you> lunch 120, bubble tea 65, MRT 30
ExpenseMate> Saved 3 expenses for 2026-11-19: lunch 120 TWD (Food), bubble tea 65 TWD (Food), MRT 30 TWD (Transport).

you> how much did I spend on food this week in rupiah?
ExpenseMate> You spent 185 TWD on food this week, which is about 102,300 IDR (live rate).
```

(The exact wording depends on the model; the numbers come from the tools.)
Every request's full trace is also saved to `logs/trace-YYYY-MM-DD.jsonl`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `could not reach the language model` | Start Ollama (open the Ollama app or run `ollama serve`) and check `ollama list` shows the model. |
| Works locally but not in the container | Ollama may only listen on 127.0.0.1. Set the Windows environment variable `OLLAMA_HOST=0.0.0.0`, restart Ollama, and try again. |
| `model "qwen2.5:7b" not found` | Run `ollama pull qwen2.5:7b`, or set `OLLAMA_MODEL` to a model you have. |
| Very slow answers | Use a smaller model, e.g. `-e OLLAMA_MODEL=qwen2.5:3b` (accuracy may drop). |
| Conversion says the rate may be outdated | The exchange-rate API could not be reached; the agent used a cached or built-in rate. |
| `podman: command not found` / machine not running | Run `podman machine start`. |
