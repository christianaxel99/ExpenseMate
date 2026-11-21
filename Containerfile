# ExpenseMate agent application.
# The LLM server (Ollama) runs on the host, outside this container.
FROM docker.io/library/python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY tests/ tests/
COPY run.py pytest.ini ./

# host.containers.internal = the host machine, as seen from a Podman container
ENV OLLAMA_HOST=http://host.containers.internal:11434 \
    OLLAMA_MODEL=qwen2.5:7b \
    EXPENSEMATE_DB=/data/expenses.db \
    EXPENSEMATE_LOG_DIR=/data/logs \
    PYTHONUNBUFFERED=1

# The database and logs live in a volume so they survive container restarts.
VOLUME /data

ENTRYPOINT ["python", "run.py"]
