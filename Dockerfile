FROM python:3.11-slim AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc g++ libffi-dev libssl-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e "." 2>/dev/null || true

COPY . .
RUN pip install --no-cache-dir -e "."

RUN mkdir -p data

EXPOSE ${PORT:-7860}

CMD ["sh", "-c", "uvicorn voiceai.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
