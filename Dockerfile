# Multi-stage Dockerfile for MMS4 Toolkit (mms4tk)
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim AS runner

WORKDIR /app

# Non-root user setup (appuser UID 1000)
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /bin/bash appuser

COPY --from=builder /install /usr/local
COPY . /app

# Ensure directories for persistence exist with proper permissions
RUN mkdir -p /app/.agents/swarm && \
    chown -R appuser:appuser /app

USER appuser

ENV PYTHONPATH=/app/src
ENV PORT=8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

EXPOSE 8000

ENTRYPOINT ["uvicorn", "src.mms4tk.api:app", "--host", "0.0.0.0", "--port", "8000"]
