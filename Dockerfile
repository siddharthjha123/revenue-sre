FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system revenue-sre \
    && useradd --system --gid revenue-sre --home-dir /app revenue-sre

COPY pyproject.toml README.md ./
COPY backend ./backend

RUN python -m pip install . \
    && mkdir -p /app/.runtime \
    && chown -R revenue-sre:revenue-sre /app

USER revenue-sre

CMD ["python", "-m", "backend.app.workers"]
