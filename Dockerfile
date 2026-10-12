# Container for the web interface. Render builds and runs this (see render.yaml).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYDANTIC_AI_NO_BANNER=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install the exact package versions recorded in uv.lock. This layer is rebuilt only when they change.
RUN pip install --no-cache-dir uv==0.9.11
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY agent ./agent
COPY web ./web
COPY examples ./examples

# The host sets PORT (Render uses 10000). 0.0.0.0 lets it reach the app from outside the container.
ENV PATH="/app/.venv/bin:$PATH" \
    HOST=0.0.0.0 \
    PORT=10000
EXPOSE 10000

CMD ["python", "-m", "web"]
