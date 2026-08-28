# Running rsched in Docker + migrating to another host

The container is the **engine environment only** — Python + `uv` + `git` + Node + the `claude` CLI.
Everything mutable is **bind-mounted**, so the whole system moves as a tarball of those directories
and the container itself stays disposable. Every data home is a bind for that reason: one that
isn't dies in the container's writable layer on the next recreate.

Compose defines two **sidecar services**, for the same reason each time: the engine image stays
engine-only and the daemon supervises no second process. `docker compose build` builds all three
images.

- **`tor`** (`deploy/Dockerfile.tor`) — the SOCKS proxy the `darknet` util egresses through,
  reachable only from the compose network. Its state is the one named volume (`tor-data`,
  regenerable guard state, so it is deliberately not part of the tarball). See `docs/darknet.md`.
- **`chrome`** (`deploy/Dockerfile.chrome`) — a headful Chrome on a virtual display holding
  LOGGED-IN site sessions, which every `--cdp` util drives. It shares the engine's network
  namespace, so CDP lands on the engine's own loopback at `127.0.0.1:9222`. Unlike tor it
  carries real state: `${RSCHED_HOME}/chrome-profile` is a **bind mount and part of the
  tarball** — lose it and every site is signed out. A person signs in over noVNC, published on
  the host's loopback only. See `docs/browser-sessions.md`.

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

`deploy/state-paths.sh` is the authority on what a migration carries, and its two lists mirror
the bind mounts in `docker-compose.yml` — a data home that is mounted but unlisted would die on
the migration instead of on the recreate, which is the same loss one host later. Both
`bundle.sh` and `backup.sh` source that one file, so they cannot drift apart. What it takes:

| Path (`${RSCHED_HOME}`-relative) | Why it must travel |
| --- | --- |
| `git-repos/routine-scheduler` | the source tree self-audit edits and the daemon runs from |
| `.config/routine-scheduler` | `config.yaml` — token, endpoints, homes, `source_repo` |
| `.credentials` | **secrets**: endpoint keys + the claude-code OAuth token |
| `routines` | the routine repos, their runs, state and ledgers |
| `conversations` | interactive sessions — routine-shaped, un-versioned, irreplaceable |
| `background` | detached background runs a conversation launched, possibly mid-flight |
| `.local/share/routine-scheduler-libraries` | the library repo: `workflows/`, `rules/`, `utils/` |

Plus four homes that exist only once a feature has been used, taken when present and reported as
skipped when not (`OPTIONAL_PATHS`): `chrome-profile` (the logged-in browser —
[docs/browser-sessions.md](../docs/browser-sessions.md)), `telegram-sessions`, `signal-sessions`
and `whatsapp-sessions` (a **linked session on disk IS the credential** — there is no API key to
re-enter, so losing one unlinks the account and someone has to re-pair by phone), and
`.config/gh` (`gh auth login`'s token, re-mintable only by another device flow).

Two mounts are deliberately left out, so their absence is a decision rather than an oversight:
`.cache/ms-playwright` is a ~170 MB browser download `page-fetch` re-fetches on first use (bound
to survive a *recreate*, worthless in a tarball), and `tor-data` is a named volume holding
regenerable guard state that means nothing on a new host.

On a **live instance the tarball is not a consistent snapshot.** `tar` exits 1 with warnings when
a file changes under it, which the chrome sidecar guarantees by rewriting its profile
continuously; `bundle.sh` distinguishes that from a real failure (exit ≥ 2), completes the
archive, and then names the unstable paths. Only `chrome-profile` actually matters — it is a
LevelDB store, and a torn copy restores as a signed-out browser. For a clean capture:

```bash
docker compose stop chrome && deploy/bundle.sh && docker compose start chrome
```

## Backups — a different job from migration

`bundle.sh` is a **one-shot migration** tool. Step 4 below decommissions the source host, which
is the only reason a frozen snapshot is acceptable: nothing writes to it afterwards. Used on a
schedule it is the wrong shape — `routines` and `conversations` are rewritten by every run
(~1600 files, ~90 MB a day on this instance), so the tarball is stale within minutes and a
nightly rebuild moves ~3.6 GB to capture ~90 MB.

`deploy/backup.sh` mirrors the same inventory incrementally instead:

```bash
deploy/backup.sh                                  # → /mnt/sshd_volume1/rsched-backup
deploy/backup.sh /path/to/some/other/mirror       # …or anywhere else
```

It refuses to run unless the destination is on a **different device** than `$HOME`. That check is
load-bearing rather than defensive: the default target is an autofs/sshfs mount of another
machine, and when that share is down its mountpoint is an ordinary empty local directory — so the
mirror would land on the very disk it is meant to survive, and report success. It also passes
`--one-file-system`, because a routine bound to a remote machine has that machine's share
sshfs-mounted at `<routine>/mnt/<name>` while it runs, and a backup firing at that moment would
otherwise copy another host's filesystem into the mirror.

`--delete` makes the mirror converge rather than accumulate, a `flock` keeps two scheduled runs
from racing, and the mirror root is created mode 700 because it contains `~/.credentials` — check
the mode the script reports, since a network share may not honour it. `chrome-profile` carries the
same torn-copy caveat as the tarball, and the script says so on every run.

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
  browser cache `~/.cache/ms-playwright` is bind-mounted). That covers every util that drives a
  THROWAWAY browser. The utils that need a **logged-in** one (`job-scrape`, `job-inbox`,
  `job-apply`, `browser-session`) are served by the `chrome` sidecar instead, at
  `http://172.30.7.10:9222` — see [docs/browser-sessions.md](../docs/browser-sessions.md). Its
  profile is a bind mount and part of the migration bundle, but the sessions inside it are not
  portable off a desktop machine: signing in is a one-time human step per host.
- **Dependency changes** committed by self-audit are picked up on the next restart (`uv run`
  re-syncs from the mounted `pyproject.toml`), exactly like the systemd unit.
- **Host mounts (`/mnt`) are bind-mounted with `rslave` propagation** so the fs-roots picker
  can offer USB disks / NAS mounts, including ones mounted on the host AFTER the container
  started (F190: without the bind, the daemon's mount namespace has no `/mnt` at all and the
  picker shows an explained empty state). Takes effect on the next `docker compose up -d`;
  drop the volume line if the host has no `/mnt`.
- **Extra host directories are opt-in, not shipped defaults.** The committed `docker-compose.yml`
  bind-mounts only what every deployment needs (the state dirs above + `/mnt`). If a specific task
  needs another host path visible in the container (e.g. `/tmp`, a project share, a document
  vault — cf. R35, where a clarify run could not read `/tmp` or `/mnt/sshd_volume1/...`), add that
  bind mount **in your local compose** (a `docker-compose.override.yml`, which Compose merges
  automatically and which is gitignored) rather than editing the tracked `docker-compose.yml` —
  so an update never clobbers it and the shipped file stays minimal. The path must ALSO be granted
  to the routine as an fs-root (Settings → the routine's Filesystem roots) before a run may read it;
  a bind mount alone makes it visible to the container, not to the sandboxed run.

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
