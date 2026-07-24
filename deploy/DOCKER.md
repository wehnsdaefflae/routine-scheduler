# Running rsched in Docker + migrating to another host

The container is the **engine environment only** — Python + `uv` + `git` + Node + the `claude` CLI.
Everything mutable (the source tree, `config.yaml`, `~/.credentials`, `~/routines`, and the
library repo) is **bind-mounted**, so the whole system moves as a tarball of those directories and
the container itself stays disposable.

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
- **models:** anything bound to a host-local Ollama endpoint must be repointed to a reachable
  provider in the model catalog (Settings → Models), since Ollama doesn't come along into the
  container. An unused endpoint definition can stay in `config.yaml` if you re-add Ollama later.
- **restart:** `restart: unless-stopped` + `stop_grace_period: 20s` reproduce the old
  `Restart=always` / `TimeoutStopSec=20`. Self-audit's drain-and-exit restart just exits 0 and Docker
  relaunches it — same as before.

## Caveats

- **Credentials are set in the UI**, not on the host — see [SETUP.md](SETUP.md). All keys, tokens,
  and util secrets go in **Settings → Secrets** (one store, injected at run time). The Claude
  subscription token is minted once elsewhere with `claude setup-token` and pasted in as
  `CLAUDE_CODE_OAUTH_TOKEN`; the container's `claude` CLI uses it via the environment and never logs
  in. It's long-lived — when it expires, re-run `claude setup-token` and update the Secrets value
  (no restart). Only `self-audit` (orchestrator) and `workflow-curator` (subcall) use `claude-cli` by
  default; most setups can use API keys instead.
- **Headless browsing works out of the box.** The image carries Chromium's system libraries;
  the `page-fetch` util downloads Playwright's Chromium itself on first use (once — the
  browser cache `~/.cache/ms-playwright` is bind-mounted). The personal CDP utils
  (freelance/gulp/xing/…) additionally need a logged-in profile — out of scope here.
- **Dependency changes** committed by self-audit are picked up on the next restart (`uv run`
  re-syncs from the mounted `pyproject.toml`), exactly like the systemd unit.
- **Host mounts (`/mnt`) are bind-mounted with `rslave` propagation** so the fs-roots picker
  can offer USB disks / NAS mounts, including ones mounted on the host AFTER the container
  started (F190: without the bind, the daemon's mount namespace has no `/mnt` at all and the
  picker shows an explained empty state). Takes effect on the next `docker compose up -d`;
  drop the volume line if the host has no `/mnt`.

## HTTPS via Tailscale (Web Push needs a secure context)

The console serves plain HTTP on the LAN. For HTTPS — required for Web Push notifications
and generally nicer — front it with the `tailscale/tailscale` container that already runs
on the server with `network_mode: host` (so `127.0.0.1:8321` inside it IS the published
rsched port):

```
# one-time, per tailnet: enable the Serve feature (and HTTPS certificates when prompted)
# in the admin console — `tailscale serve` prints the exact approval URL if it's off.
docker exec tailscale tailscale serve --bg 8321
docker exec tailscale tailscale serve status      # shows the https URL it now fronts
```

The console then lives at `https://<node>.<tailnet>.ts.net` (here:
`https://ubuntuserver.taild5768c.ts.net`) with a Let's Encrypt certificate Tailscale
provisions and renews itself — reachable from every tailnet device (phone included),
invisible to everyone else. SSE and Web Push work through it unchanged; subscribe each
device under **Settings → Notifications**. Undo with
`docker exec tailscale tailscale serve reset`.
