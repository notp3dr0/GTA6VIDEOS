FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:/root/.cargo/bin:$PATH"

WORKDIR /app

COPY . /app

RUN uv sync --frozen --extra youtube || uv sync --extra youtube

EXPOSE 8080

CMD ["uv", "run", "gta6-agents", "daemon"]
