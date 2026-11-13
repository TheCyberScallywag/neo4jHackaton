# One image for the web app and the Streamlit admin; SERVICE (web|admin) picks the process.
# The Relate engine is the embedded relate-db wheel from uv.lock, so no Relate installer runs here.

ARG PYTHON_VERSION=3.14

FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first (cached layer), project second.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project --extra admin --extra aura
COPY pyproject.toml uv.lock README.md ./
COPY brand ./brand
COPY data ./data
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --extra admin --extra aura

FROM python:${PYTHON_VERSION}-slim AS runtime
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 HOST=0.0.0.0 DATA_DIR=/data
RUN useradd --system --uid 10001 app && mkdir -p /data && chown app /data
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
USER app
VOLUME /data
ENTRYPOINT ["python", "-m", "app.serve"]
