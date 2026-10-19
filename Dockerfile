# Multi-stage Dockerfile for Paper Airplane CFD Simulator
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final runtime image
FROM python:3.11-slim AS runner

WORKDIR /app

# Create a non-root system user and group
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy installed python wheels from builder
COPY --from=builder /root/.local /home/appuser/.local

# Copy application source code
COPY --chown=appuser:appuser . /app

ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENV=production

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz/live', timeout=4)" || exit 1

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "5000"]
