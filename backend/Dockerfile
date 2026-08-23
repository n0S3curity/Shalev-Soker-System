# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Jerusalem

# Build tools are needed for argon2-cffi wheels on some architectures only.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential curl tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
 && apt-get purge -y --auto-remove build-essential

COPY backend/app /app/app
COPY frontend /app/frontend

# Run as an unprivileged user (OWASP A05 - security misconfiguration)
RUN groupadd --system --gid 10001 appuser \
 && useradd --system --uid 10001 --gid appuser --home /app appuser \
 && mkdir -p /app/logs \
 && chown -R appuser:appuser /app

USER appuser

ENV FRONTEND_DIR=/app/frontend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# X-Forwarded-For is deliberately NOT trusted: the real client address comes
# from Cloudflare's CF-Connecting-IP header, which the tunnel sets itself.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", \
     "--no-server-header", \
     "--timeout-keep-alive", "30"]
