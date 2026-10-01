# rsched runtime image — the ENGINE ENVIRONMENT only. No app code, no state, no secrets are
# baked in: the source repo, config, ~/.credentials, ~/routines and the library repo are all
# bind-mounted (see docker-compose.yml), so the container is disposable and the whole system
# migrates as a tarball of those directories. Rebuild the image on any dependency/tooling change.
#
# EVERY IMAGE THIS BUILD READS IS PINNED BY DIGEST, its tag kept beside it for the reader. A tag
# moves — `python:3.12-slim-bookworm` is re-pushed for every Debian point release and Python
# patch — so a rebuild took whatever had been pushed that day, and two builds of one commit could
# differ with nothing in the repository to say why: the drift the uv pin below was made against,
# one layer further down. Each digest names the image INDEX (every platform), so a build still
# resolves this host's own architecture. Docker reads only the digest when both are given; the
# tag records what it was resolved FROM, and a bump re-resolves that same tag (deploy/DOCKER.md,
# "The image"). The two images this build only copies from are stages of their own, so their pins
# are FROM lines like the base's and no COPY can name an unpinned image —
# tests/test_deploy_image.py holds every FROM, and every `COPY --from`, to that shape.
#
# Node 24 LTS: the official node image's own install, copied out below. Its binary is the
# nodejs.org release build, whose GPG-signed checksum that image verified when it was built, and
# the digest pins those exact bytes — no third-party apt repository and no `curl | bash`
# installer. The binary needs glibc >= 2.28 and libstdc++, both in this base (Debian 12: 2.36).
FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS node-dist
# uv — runs the daemon and each util's inline-dependency script. PINNED, because `:latest`
# made the resolver that runs this whole system whatever was newest on the day of the last
# rebuild: a `uv run` or lock-resolution change would then arrive with no diff in the
# repository, which is the one kind of failure that cannot be bisected. It had already drifted
# — 0.12.13 in the container against 0.11.28 on the host. Bump it deliberately, like any
# other dependency, and rebuild.
FROM ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 AS uv-dist

FROM python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e

# Every RUN below is bash with pipefail (hadolint DL4006). /bin/sh judges a pipeline by its LAST
# command alone: the NodeSource `curl … | bash -` this build used to run exited 0 on a FAILED
# download, because bash runs an empty script without complaint, and the failure surfaced one
# command later as an unrelated `Unable to locate package gh`. Under pipefail it is curl's 22.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Runtime tools the routines + setup need:
#   git       — the library + routines are git repos; git-sync / git-restore / pytest-run utils
#   gh        — GitHub CLI: users run `gh auth login` at setup to clone/pull/push their (private) repos
#   curl/ca-certificates/gnupg — uv download, apt keys, HTTPS to OpenRouter/Anthropic; curl is
#     also the probe of the rsched service's healthcheck (docker-compose.yml)
#   sshfs     — mount a bound remote machine's `share` into a routine (docs/remote-machines.md);
#     needs the fuse device + CAP_SYS_ADMIN at RUN time (see docker-compose.yml)
#   rsync     — deploy/backup.sh runs on the HOST, but its tests (tests/test_backup_snapshots.py)
#     drive the real script, and the gate that ships a release runs INSIDE this container
#     (CLAUDE.md, the browser suite): without rsync here every one of them skips, and a skipped
#     backup test reads exactly like a passing one
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
        git curl ca-certificates gnupg sshfs rsync build-essential php-cli \
    # GitHub CLI apt repo
    && mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    # …and READ the repo just added. The NodeSource installer this build used to pipe into bash
    # ran `apt-get update` itself, and that side effect was the only reason `gh` was ever found.
    && apt-get update \
    && apt-get install -y --no-install-recommends gh gosu \
        libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 \
        libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 \
        libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
        libxkbcommon0 libxrandr2 libfontconfig1 libfreetype6 \
        fonts-liberation fonts-noto-color-emoji fonts-unifont \
        wngerman wswiss wamerican \
        xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

# Node 24 LTS (the `node-dist` stage) for what still runs on Node in this container:
#   the claude CLI — `@anthropic-ai/claude-code` is a NATIVE binary now, placed by its npm
#     postinstall (`node install.cjs`), and the package declares `engines.node >=22`, which the
#     Node 20 this replaced was already below. The library's `claude` util runs it (`frame-fill`
#     through `gu claude`); scheduler models use CLIProxyAPI, not this CLI.
#   `node --check` — the JS syntax gates of library utils: code-search's `sym check` (self-audit's
#     pre-gate over the console's ES modules) and `html js-check` (inline scripts)
#   what a run starts itself: a routine's own JS test harnesses, and npm builds of projects whose
#     engines floor is 22 — one routine was fetching its own Node into /tmp to get past 20
# Not Playwright: its Python wheel carries its own Node driver.
# Laid out exactly as the node image lays it out. npm, npx and corepack are RELATIVE symlinks
# into lib/node_modules, made again here because COPY dereferences a symlink it is handed — an
# npm-cli.js copied into bin/ cannot find its own lib/. The headers let node-gyp build a native
# addon against build-essential above instead of downloading them first.
COPY --from=node-dist /usr/local/bin/node /usr/local/bin/node
COPY --from=node-dist /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node-dist /usr/local/include/node /usr/local/include/node
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
    && node --version && npm --version \
    # The postinstall is ALLOWED by name: npm 11 (Node 24's) warns that install scripts are "not
    # yet covered by allowScripts", the announcement of a default that blocks them — and that
    # script is the only thing that turns the package into a working CLI.
    && npm install -g --allow-scripts=@anthropic-ai/claude-code @anthropic-ai/claude-code \
    && npm cache clean --force \
    # The postinstall can fail with exit 0 and leave the package's stub in place (a platform
    # package that did not download, say). Running the CLI is what turns that into a failed
    # BUILD rather than a `claude` util that fails at its first call.
    && claude --version

# uv, from its pinned stage (the `uv-dist` FROM above says why it is pinned at all)
COPY --from=uv-dist /uv /uvx /bin/

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
