# Matchlens backend: FastAPI + agents. Replays the recorded demo match by default.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code changes reuse this layer.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY demo ./demo
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    MATCHLENS_CASSETTE=/app/demo/hero-match.json

RUN useradd --system --uid 10001 matchlens
USER matchlens

EXPOSE 8765
CMD ["uvicorn", "matchlens.api.app:app", "--host", "0.0.0.0", "--port", "8765", "--proxy-headers"]
