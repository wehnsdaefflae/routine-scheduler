# rsched runtime image — the ENGINE ENVIRONMENT only. No app code, no state, no secrets are
# baked in: the source repo, config, ~/.credentials, ~/routines and the library repo are all
# bind-mounted (see docker-compose.yml), so the container is disposable and the whole system
# migrates as a tarball of those directories. Rebuild the image on any dependency/tooling change.
FROM python:3.12-slim-bookworm

# Runtime tools the routines + setup need:
#   git       — the library + routines are git repos; git-sync / git-restore / pytest-run utils
#   gh        — GitHub CLI: users run `gh auth login` at setup to clone/pull/push their (private) repos
#   node + @anthropic-ai/claude-code — independent library utilities (scheduler models use CLIProxyAPI)
#   curl/ca-certificates/gnupg — uv download, apt keys, HTTPS to OpenRouter/Anthropic
#   sshfs     — mount a bound remote machine's `share` into a routine (docs/remote-machines.md);
#     needs the fuse device + CAP_SYS_ADMIN at RUN time (see docker-compose.yml)
#   lib*/fonts-* — Chromium's system libraries, so the page-fetch util's Playwright browser RUNS
#     here (the ~170 MB browser itself is user-level: downloaded once by the util into the
#     bind-mounted ~/.cache/ms-playwright — image carries the stable root-owned libs only)
#   xvfb/xauth — virtual X display so a browser util can run HEADFUL Chrome on this headless
#     host when a site defeats headless mode; opt-in per util via xvfb-run, no global DISPLAY
#   wngerman/wswiss/wamerican — system word lists at /usr/share/dict/. A routine that writes
#     German prose checks its own output against them: without a list the lexical tier stands
#     DOWN and every ASCII digraph reads as a transliteration, which is 159 false positives a
#     run and a check its reader learns to ignore (R1009). ~10 MB, and the check states which
#     lists it had in its own summary line, so the evidence it ran on is never implicit.
#   php-cli   — the steward hub kit (library web/steward/: p.php, api.php, store.php, gate.php)
#     is PHP, and its maintainer routine's local gate is `php -l` plus the built-in server
#     (`php -S`) for a behaviour probe. Without an interpreter every one of its fixes stayed
#     "locally unverifiable" and eight cluster items sat blocked for a week (R1404; operator
#     decision 2026-09-11: provision a local PHP test environment, no production access).
#     The CLI only — no Apache: the kit's .htaccess rewrites are the ONE thing this cannot
#     probe, and the routine says so in its verification record.
#   build-essential — a C toolchain for util dependency installs (F341, operator choice
#     2026-08-26). A util declares its deps as PEP 723 inline metadata and uv builds them at
#     call time; a package published only as an sdist (or one whose wheel misses this
#     platform) needs a compiler right there, and without one the util fails at its FIRST run
#     with a build error no routine can act on. The full chain, not just gcc: a compiled dep
#     that needs g++ or a Makefile is exactly the case a partial toolchain still fails.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates gnupg sshfs build-essential php-cli \
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
        wngerman wswiss wamerican \
        xvfb xauth \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# uv — runs the daemon and each util's inline-dependency script. PINNED, because `:latest`
# made the resolver that runs this whole system whatever was newest on the day of the last
# rebuild: a `uv run` or lock-resolution change would then arrive with no diff in the
# repository, which is the one kind of failure that cannot be bisected. It had already drifted
# — 0.12.13 in the container against 0.11.28 on the host. Bump it deliberately, like any
# other dependency, and rebuild.
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

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
# --locked, not --frozen: `--frozen` installs the lock AS-IS and never checks it against
# pyproject.toml, so a dependency added without a re-lock builds a GREEN image that is missing
# the package and fails as an ImportError inside the daemon at run time — with no CI and a
# pre-commit that runs only ruff, mypy and test_policy, nothing else would catch it.
RUN uv sync --locked --extra headroom --no-install-project \
    && chown -R mark:mark /opt/rsched-venv /home/mark

# Entrypoint runs as ROOT to make bind mounts writable (Docker creates missing ones root-owned),
# then drops to `mark` and starts the daemon (which generates config+token on a fresh deploy).
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uv", "run", "--extra", "headroom", "rsched", "daemon"]
