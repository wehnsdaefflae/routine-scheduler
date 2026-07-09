# rsched runtime image — the ENGINE ENVIRONMENT only. No app code, no state, no secrets are
# baked in: the source repo, config, ~/.credentials, ~/routines and the three libraries are all
# bind-mounted (see docker-compose.yml), so the container is disposable and the whole system
# migrates as a tarball of those directories. Rebuild the image on any dependency/tooling change.
FROM python:3.12-slim-bookworm

# Runtime tools the routines + setup need:
#   git       — libraries + routines are git repos; git-sync / git-restore / pytest-run utils
#   gh        — GitHub CLI: users run `gh auth login` at setup to clone/pull/push their (private) repos
#   node + @anthropic-ai/claude-code — the `claude-cli` transport (self-audit) and the `gu claude` util
#   curl/ca-certificates/gnupg — uv download, apt keys, HTTPS to OpenRouter/Anthropic
# (No browser: none of the routines drive one. To enable the personal CDP utils later, add a
#  Chromium/Playwright layer here — see deploy/DOCKER.md.)
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
    && apt-get install -y --no-install-recommends nodejs gh \
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
USER mark

# Pre-bake the daemon's dependencies (incl. dev → pytest, for self-audit's test gate) into the
# image from the lockfile alone. The package itself installs editable from the bind-mounted source
# at run time, so `uv run` still re-syncs on a self-audit dependency change, exactly like systemd did.
COPY --chown=${UID}:${GID} pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

CMD ["uv", "run", "rsched", "daemon"]
