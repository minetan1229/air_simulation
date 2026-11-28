# Multi-stage Dockerfile for Paper Airplane CFD Simulator
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY api ./api
COPY core ./core
COPY geometry ./geometry
COPY infrastructure ./infrastructure
COPY optimization ./optimization
COPY physics ./physics
COPY tools ./tools

# Install runtime dependencies only (omitting dev/test/lint packages) plus gunicorn WSGI server
RUN pip install --no-cache-dir --user . gunicorn

# Final runtime image
FROM python:3.11-slim AS runner

WORKDIR /app

# Create a non-root system user and group
RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

# Copy installed python packages from builder with proper ownership
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local

# Copy application source code
COPY --chown=appuser:appuser . /app

# Ensure appuser's Python finds the copied user-site packages.
ENV PATH=/home/appuser/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENV=production \
    HOME=/app \
    PYTHONUSERBASE=/home/appuser/.local

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz/live', timeout=4)" || exit 1

# Single worker with threads preserves in-memory session/job state without Flask's dev server
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
