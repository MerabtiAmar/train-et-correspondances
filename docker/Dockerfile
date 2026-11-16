FROM python:3.12-slim

# Pinned uv binary (multi-arch image: works on arm64 and x86_64, built on the VM itself).
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Non-root user; /app/data is where the compose volume is mounted.
RUN useradd --system --uid 10001 --create-home collector \
    && mkdir -p /app/data \
    && chown collector:collector /app/data
USER collector

# No EXPOSE and no listening socket: the collector only makes outgoing requests.
CMD ["python", "-m", "tc"]
