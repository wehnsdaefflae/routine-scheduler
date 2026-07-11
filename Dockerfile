# rsched runtime image — the ENGINE ENVIRONMENT only. No app code, no state, no secrets are
# baked in: the source repo, config, ~/.credentials, ~/routines and the library repo are all
# bind-mounted (see docker-compose.yml), so the container is disposable and the whole system
# migrates as a tarball of those directories. Rebuild the image on any dependency/tooling change.
FROM python:3.12-slim-bookworm

# Runtime tools the routines + setup need:
#   git       — the library + routines are git repos; git-sync / git-restore / pytest-run utils
#   gh        — GitHub CLI: users run `gh auth login` at setup to clone/pull/push their (private) repos
#   node + @anthropic-ai/claude-code — the `claude-cli` transport (self-audit) and the `gu claude` util
#   curl/ca-certificates/gnupg — uv download, apt keys, HTTPS to OpenRouter/Anthropic
#   lib*/fonts-* — Chromium's system libraries, so the page-fetch util's Playwright browser RUNS
#     here (the ~170 MB browser itself is user-level: downloaded once by the util into the
#     bind-mounted ~/.cache/ms-playwright — image carries the stable root-owned libs only)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates gnupg \
    # GitHub CLI apt repo
    && mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    # Node 20 (for the claude CLI)
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs gh gosu \
        libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 \
        libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 \
        libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
        libxkbcommon0 libxrandr2 libfontconfig1 libfreetype6 \
        fonts-liberation fonts-noto-color-emoji fonts-unifont \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# uv — runs the daemon and each util's inline-dependency script
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# A non-root user whose uid/gid match the host owner of the bind mounts (default 1000), so the
# engine's commits + run files stay host-owned and the claude CLI never runs as root.
ARG UID=1000
ARG GID=1000
RUN groupadd -g "${GID}" mark 2>/dev/null || true \
    && useradd -m -u "${UID}" -g "${GID}" -s /bin/bash mark

ENV HOME=/home/mark \
    UV_PROJECT_ENVIRONMENT=/opt/rsched-venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    RSCHED_BIND=0.0.0.0

# git identity + trust the bind-mounted repos (git refuses "dubious ownership" otherwise)
RUN git config --system user.name "routine-scheduler" \
    && git config --system user.email "noreply@routine-scheduler.local" \
    && git config --system --add safe.directory '*' \
    && mkdir -p /opt/rsched-venv && chown -R "${UID}:${GID}" /opt/rsched-venv

WORKDIR /home/mark/git-repos/routine-scheduler

# Pre-bake the daemon's dependencies (incl. dev → pytest, for self-audit's test gate) from the
# lockfile alone (as root; chowned to mark after). The package itself installs editable from the
# bind-mounted source at run time, so `uv run` still re-syncs on a self-audit dependency change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project \
    && chown -R mark:mark /opt/rsched-venv /home/mark

# Entrypoint runs as ROOT to make bind mounts writable (Docker creates missing ones root-owned),
# then drops to `mark` and starts the daemon (which generates config+token on a fresh deploy).
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uv", "run", "rsched", "daemon"]
