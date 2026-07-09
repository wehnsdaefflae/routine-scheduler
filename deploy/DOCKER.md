# Running rsched in Docker + migrating to another host

The container is the **engine environment only** — Python + `uv` + `git` + Node + the `claude` CLI.
Everything mutable (the source tree, `config.yaml`, `~/.credentials`, `~/routines`, and the three
libraries) is **bind-mounted**, so the whole system moves as a tarball of those directories and the
container itself stays disposable.

Container paths are always `/home/mark/...` (routines and config bake absolute paths, so they must
not change). Host paths are `${RSCHED_HOME}`-relative (default `/home/mark`).

---

## 1. On this machine — build + verify

```bash
cd ~/git-repos/routine-scheduler
docker compose build                       # ~2–4 min (Node + claude CLI + Python deps)
RSCHED_PORT=8322 docker compose up -d       # test on a spare port, alongside the live systemd daemon
curl -s -H "Authorization: Bearer $(grep -oP '^token:\s*"?\K[^"]+' ~/.config/routine-scheduler/config.yaml | tr -d '\"')" \
     http://127.0.0.1:8322/api/status
docker compose down                         # stop the test container
```

> The live systemd service still owns port 8321. Only run the container on 8321 **after** you have
> decommissioned that service (step 4) — otherwise two schedulers fire the same routines and both
> push to the same git remotes.

## 2. Bundle the state

```bash
deploy/bundle.sh                            # → ~/rsched-migration-<ts>.tgz  (contains secrets!)
```

## 3. On the server (192.168.0.128)

```bash
# prerequisites: Docker Engine + compose plugin, and internet (OpenRouter/Anthropic + the build).
scp ~/rsched-migration-*.tgz  <user>@192.168.0.128:~/          # from this machine

# on the server:
sudo useradd -m -u 1000 mark 2>/dev/null || true               # match the bundle's uid (or set RSCHED_UID/GID)
mkdir -p /home/mark && tar xzf ~/rsched-migration-*.tgz -C /home/mark
cd /home/mark/git-repos/routine-scheduler
docker compose up -d --build                                   # builds the image, starts on :8321
```

Then browse to **http://192.168.0.128:8321** (token is in the migrated `config.yaml`).

Transferring the image instead of building on the server (offline server):
```bash
# on this machine:  docker save rsched:latest | gzip | ssh <user>@192.168.0.128 'gunzip | docker load'
# then on the server:  docker compose up -d      (no --build)
```

## 4. Decommission the dev daemon — required

Once the server is verified, stop this machine's scheduler so routines don't run twice:
```bash
systemctl --user disable --now routine-scheduler.service
```

---

## What changed for the container

- **bind:** the container sets `RSCHED_BIND=0.0.0.0` (env override in `cmd_daemon`) so it serves the
  LAN without editing the mounted `config.yaml`. The token is still the only auth — keep it on a
  trusted LAN.
- **models:** the three `ollama-local` routines (`ai-agent-papers-digest`, `demo`, `library-sync`)
  and the `cheap` role were repointed to `openrouter/z-ai/glm-5.2`, since Ollama doesn't come along.
  The `ollama-local` endpoint definition is left in `config.yaml` (unused) if you re-add Ollama.
- **restart:** `restart: unless-stopped` + `stop_grace_period: 20s` reproduce the old
  `Restart=always` / `TimeoutStopSec=20`. Self-audit's drain-and-exit restart just exits 0 and Docker
  relaunches it — same as before.

## Caveats

- **Claude token refresh is manual.** `gu claude-login` opens a browser and cannot run headless.
  When `~/.credentials/claude-code-oauth.env` expires, refresh it on a machine with a browser and
  copy the new `CLAUDE_CODE_OAUTH_TOKEN=` line into the server's file (the mount picks it up; no
  rebuild). Only `self-audit` (orchestrator) and `meta-workflows` (subcall) use `claude-cli`.
- **No browser in the image.** None of the routines drive one. The personal CDP utils
  (freelance/gulp/xing/…) won't work until you add a Chromium/Playwright layer to the `Dockerfile`
  and provide a logged-in profile — out of scope here.
- **Dependency changes** committed by self-audit are picked up on the next restart (`uv run`
  re-syncs from the mounted `pyproject.toml`), exactly like the systemd unit.
