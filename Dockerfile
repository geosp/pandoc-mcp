# pandoc-mcp: MCP server with pandoc, XeLaTeX for PDF output and mermaid-cli for diagrams
FROM python:3.12-slim

ARG PANDOC_VERSION=3.12.1
ARG MERMAID_CLI_VERSION=11.17.0
ARG PUPPETEER_VERSION=24.43.1
ARG TARGETARCH=amd64

# git: uv fetches mcp-service-kit from GitHub. texlive: PDF output via xelatex.
# librsvg2-bin: SVG images in PDF. nodejs/chromium: mermaid-cli renders in headless Chromium.
# Pandoc comes from its release .deb, Debian's lags behind.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git \
        texlive-xetex texlive-latex-recommended texlive-latex-extra texlive-fonts-recommended \
        lmodern fonts-dejavu-core fonts-liberation2 librsvg2-bin \
        nodejs npm chromium \
    && curl -fsSL -o /tmp/pandoc.deb \
        "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-${TARGETARCH}.deb" \
    && apt-get install -y /tmp/pandoc.deb \
    && PUPPETEER_SKIP_DOWNLOAD=1 npm install --global --no-fund --no-audit \
        "@mermaid-js/mermaid-cli@${MERMAID_CLI_VERSION}" "puppeteer@${PUPPETEER_VERSION}" \
    && npm cache clean --force \
    && rm -rf /tmp/pandoc.deb /var/lib/apt/lists/*
# Debian's Chromium; its own sandbox needs privileges the container does not have
RUN printf '%s\n' '{"executablePath": "/usr/bin/chromium",' \
        '"args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]}' \
        > /etc/puppeteer.json
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY assets ./assets
RUN uv sync --frozen --no-dev

RUN useradd --uid 1000 --create-home pandoc && mkdir -p /data/documents \
    && chown pandoc /data/documents
USER pandoc

ENV PATH=/app/.venv/bin:$PATH \
    MCP_PORT=8301 \
    PANDOC_MCP_ASSETS_DIR=/app/assets \
    PANDOC_MCP_OUTPUT_DIR=/data/documents \
    PANDOC_MCP_PUPPETEER_CONFIG=/etc/puppeteer.json

EXPOSE 8301
# --mode is required: the CLI default (stdio) overrides MCP_TRANSPORT
CMD ["pandoc-mcp", "--mode", "http"]
