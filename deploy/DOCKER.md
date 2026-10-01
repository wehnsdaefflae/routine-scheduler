# Running rsched in Docker + migrating to another host

The container is the **engine environment only** — Python + `uv` + `git` + Node 24 LTS + the
`claude` CLI, every base image pinned by digest ([The image](#the-image)).
Everything mutable is **bind-mounted**, so the whole system moves as a tarball of those directories
and the container itself stays disposable. Every data home is a bind for that reason: one that
isn't dies in the container's writable layer on the next recreate.

Compose defines three **sidecar services**, for the same reason each time: the engine image stays
engine-only and the daemon supervises no second process. `docker compose build` builds the three
images of this repository (the engine, `tor`, `chrome`); `cliproxy` is a pinned upstream image.

- **`tor`** (`deploy/Dockerfile.tor`) — the SOCKS proxy the `darknet` util egresses through,
  reachable only from the compose network. Its state is the one named volume (`tor-data`,
  regenerable guard state, so it is deliberately not part of the tarball). See `docs/darknet.md`.
- **`chrome`** (`deploy/Dockerfile.chrome`) — a headful Chrome on a virtual display holding
  LOGGED-IN site sessions, which every `--cdp` util drives. It sits on its OWN network at a
  fixed address — CDP is `http://172.30.7.10:9222` — rather than in the engine's namespace,
  which the engine's drain-and-exit restarts would strand. Both its ports are behind a
  bearer-token proxy (`BROWSER_CDP_TOKEN`, step 1). Unlike tor it carries real state:
  `${RSCHED_HOME}/chrome-profile` is a **bind mount and part of the tarball** — lose it and every
  site is signed out. A person signs in over noVNC, published on the host's loopback only. See
  `docs/browser-sessions.md`.
- **`cliproxy`** (behind the `claude-proxy` profile, so a plain `docker compose up -d` leaves it
  alone) — the CLIProxyAPI transport a Claude or Codex subscription is billed through. Its
  state lives in `.config/routine-scheduler/cliproxy/`, inside the inventory below. See
  [proxy setup](../docs/claude-proxy-cutover.md).

Container paths are always `/home/mark/...` (routines and config bake absolute paths, so they must
not change). Host paths are `${RSCHED_HOME}`-relative (default `/home/mark`).

---

## 1. On this machine — build + verify

The compose file refuses every command until `BROWSER_CDP_TOKEN` is set: it is the one
credential in front of the browser's CDP and noVNC ports, which authenticate nothing of their
own. Keep it in `.env` beside `docker-compose.yml` (gitignored, and carried by the migration
bundle with the checkout), and put the SAME value in **Settings → Secrets** under the same name
once the console is up — the console's browser relay and every util that declares it read it
from there.

```bash
cd ~/git-repos/routine-scheduler
[ -f .env ] || { printf 'BROWSER_CDP_TOKEN=%s\n' "$(openssl rand -hex 32)" > .env; chmod 600 .env; }
docker compose build                       # ~2–4 min (Node + claude CLI + Python deps)
RSCHED_PORT=8322 docker compose up -d       # test on a spare port, alongside the live systemd daemon
curl -s -H "Authorization: Bearer $(grep -oP '^token:\s*"?\K[^"]+' ~/.config/routine-scheduler/config.yaml | tr -d '\"')" \
     http://127.0.0.1:8322/api/status
docker inspect --format '{{.State.Health.Status}}' rsched   # starting, then healthy ~30 s in ("Health")
docker compose down                         # stop the test container
```

> The live systemd service still owns port 8321. Only run the container on 8321 **after** you have
> decommissioned that service (step 4) — otherwise two schedulers fire the same routines and both
> push to the same git remotes.

## 2. Bundle the state

```bash
deploy/bundle.sh                            # → ~/rsched-migration-<ts>.tgz  (contains secrets!)
```

`deploy/state-paths.sh` is the authority on what a migration carries, and its lists mirror the
bind mounts in `docker-compose.yml` — a data home that is mounted but unlisted would die on the
migration instead of on the recreate, which is the same loss one host later, so
`tests/test_deploy_state.py` fails on a bind the lists neither carry nor declare left out. Both
`bundle.sh` and `backup.sh` source that one file, so they cannot drift apart. What it takes:

| Path (`${RSCHED_HOME}`-relative) | Why it must travel |
| --- | --- |
| `git-repos/routine-scheduler` | the source tree self-audit edits and the daemon runs from |
| `.config/routine-scheduler` | **secrets**: `config.yaml` (tokens, endpoints, homes, `source_repo`), the Secrets store beside it, the cliproxy keys |
| `routines` | the routine repos, their runs, state and ledgers |
| `conversations` | interactive sessions — routine-shaped, un-versioned, irreplaceable |
| `background` | detached background runs a conversation launched, possibly mid-flight |
| `.local/share/routine-scheduler-libraries` | the library repo: `workflows/`, `rules/`, `permissions/`, `patterns/`, `utils/`, … |

Plus the homes that exist only once a feature has been used, taken when present and reported as
skipped when not (`STATE_PATHS_OPTIONAL`): `.credentials` (the key files an endpoint's
`key_env_file` names — the credential ladder's last rung, below the Secrets store, so an install
that never used one has none), `chrome-profile` (the logged-in browser —
[docs/browser-sessions.md](../docs/browser-sessions.md)), `telegram-sessions`, `signal-sessions`
and `whatsapp-sessions` (a **linked session on disk IS the credential** — there is no API key to
re-enter, so losing one unlinks the account and someone has to re-pair by phone), `.config/gh`
(`gh auth login`'s token, re-mintable only by another device flow), `.claude-daemon` (the
interactive `claude /login` token — the only credential carrying the `user:profile` scope the
subscription-quota read needs), and each **project workspace** a routine works inside.

A project workspace is mounted at its HOST path, so the paths a project's own documents name
stay true here — `git-repos/LLMSecTest_agentic` and its read-only grant folder under
`/srv/ObsidianVault/` are the first pair. Two things about carrying one. It is listed even
though nearly all of it is pushed to GitHub every run, because what the list carries is the
REST: the gitignored credentials directory, un-pushed work, and gitignored OUTPUT that no clone
brings and no cheap command regenerates — 38 MB of rendered scan reports the published page is
built from, in that repo's case. And its regenerable bulk is cut by an ANCHORED exclude naming
the workspace (`git-repos/LLMSecTest_agentic/apps`, `.../venv`), never by a bare directory name
that would silently match somewhere else. Both mounts set `create_host_path: false`: docker's
default is to invent an empty root-owned directory for a missing source, and a routine would
then read an empty grant folder as an empty grant folder rather than as a broken mount. That
guard has already earned itself: on 2026-09-20 the grant folder's source vanished, the daemon's
routine drain-and-exit restart could not come back, and the mount error named the exact missing
path instead of handing a routine an empty directory to reason from.

Three mounts are deliberately left out, so their absence is a decision rather than an oversight:
`.cache/ms-playwright` is a ~170 MB browser download `page-fetch` re-fetches on first use (bound
to survive a *recreate*, worthless in a tarball), `tor-data` is a named volume holding
regenerable guard state that means nothing on a new host, and `/srv/ObsidianVault` — the grant
folder's store — is the host's own Obsidian vault rather than this instance's state: it sits
outside `RSCHED_HOME` so the HOME-relative inventory cannot express it, and it already has
syncthing replicating it and restic snapshotting it. Mount it on the new host; the tarball does
not bring it.

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

`deploy/backup.sh` keeps **dated snapshots** of the same inventory instead:

```bash
deploy/backup.sh                                  # → /mnt/sshd_volume1/rsched-backup/snapshots/<today>
deploy/backup.sh /path/to/some/other/root         # …or anywhere else
deploy/backup.sh --help                           # the layout, what is kept, how to restore
```

Every run writes `<root>/snapshots/<YYYY-MM-DD>` — the host's local date; a second run the same
day refreshes that day's — and `<root>/latest` points at the newest complete one. Each snapshot
is a whole copy on its own, but `rsync --link-dest` hard-links every file that did not change
since the newest earlier snapshot, so a night costs only what changed. Snapshots rather than one
mirror, because a mirror copies DAMAGE as faithfully as work: a run that wrecked state at 01:00
was copied over the last good copy at 03:30, and a deleted conversation lived in it only until
the next night.

A snapshot is built under a temporary name (`snapshots/.in-progress`) and takes its date only
once every home copied, so a failed or interrupted run never leaves a half snapshot that a
restore could pick or a later run could build on, and `latest` stays where it was. After a
**successful** run, retention keeps the **14 newest snapshots plus the newest of each of the 8
most recent older ISO weeks** — weeks that hold one, so nights the host was down cost no depth —
and deletes the rest: about ten weeks of history. A failed run deletes nothing, and no run
deletes the snapshot it just wrote. rsync's exit 24 — a file that vanished between its listing
and its copy, routine on a live instance — still completes the snapshot; any other failure
keeps none.

It refuses to run unless the destination is on a **different device** than `$HOME`. That check is
load-bearing rather than defensive: the default target is an autofs/sshfs mount of another
machine, and when that share is down its mountpoint is an ordinary empty local directory — so the
backup would land on the very disk it is meant to survive, and report success. It also passes
`--one-file-system`, because a routine bound to a remote machine has that machine's share
sshfs-mounted at `<routine>/mnt/<name>` while it runs, and a backup firing at that moment would
otherwise copy another host's filesystem into the snapshot.

Each home is copied with `--relative`, so rsync matches the exclude list against the same
HOME-relative names tar does: an exclude anchored to a workspace (`git-repos/LLMSecTest_agentic/apps`)
cuts the same files from both, which `tests/test_backup_snapshots.py` checks by running the two
side by side. Every snapshot starts **empty**, so an exclude takes effect the very next night,
even for a file an older snapshot still holds — the single mirror needed `--delete-excluded` for
that, after a stale Chrome `SingletonLock` survived being excluded on its first live run. A
`flock` keeps two scheduled runs from racing, and the root is created mode 700 because it
carries `config.yaml`'s bearer tokens and the Secrets store beside it — check the mode the
script reports, since a network share may not honour it. `chrome-profile` carries the same
torn-copy caveat as the tarball, and the script says so on every run.

The first run on a root that still holds the old single mirror (every home directly under the
root) **moves** it into `snapshots/<date of its last completed run>` — a rename, instant at any
size — and links that night's snapshot against it.

### Restoring

Restore with the service stopped, so nothing writes a home while it is copied back:

```bash
docker compose stop rsched          # host install: systemctl --user stop routine-scheduler
rsync -a --delete /mnt/sshd_volume1/rsched-backup/snapshots/2026-09-30/routines/ ~/routines/
docker compose start rsched
```

A snapshot holds each home at its inventory path (`routines`, `conversations`,
`.config/routine-scheduler`, …); to restore `chrome-profile`, stop the `chrome` sidecar too.
`--delete` makes the home exactly what the snapshot holds, which also removes what no snapshot
carries — `.venv`s, `__pycache__`, a workspace's excluded bulk — each of which rebuilds on first
use or by its own setup. One conversation or file is a plain `cp -a` out of the snapshot; a whole
host from nothing is `rsync -a <root>/latest/ ~/`, then step 3's `docker compose up -d --build`.
**Never edit a file inside a snapshot**: an unchanged file is ONE file, shared by every snapshot
that holds it.

### Running it nightly

`deploy/rsched-backup.{service,timer}` are systemd **user** units — 03:30 with a 15-minute
jitter, `Persistent=true` so a night the host was down runs once it is back, and a 2-hour
`TimeoutStartSec` so a wedged NAS fails the unit instead of blocking every later firing on the
lock. They are NOT installed by `deploy/install.sh`, deliberately: the backup root is
host-specific, and a default install has nowhere correct to point. The service runs the
checkout's `backup.sh`, so an update reaches the next firing by itself; re-copy the two units
only when they change.

```bash
install -m 0644 deploy/rsched-backup.service ~/.config/systemd/user/
install -m 0644 deploy/rsched-backup.timer   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now rsched-backup.timer
systemctl --user start rsched-backup.service     # prove it once, then read the journal
journalctl --user -u rsched-backup.service -n 30
```

Needs `loginctl enable-linger <user>` (which `install.sh` already does) or the timer only runs
while someone is logged in. Point it elsewhere with a drop-in — `systemctl --user edit
rsched-backup.service`, then `Environment=RSCHED_MIRROR=…` — rather than editing the tracked unit.

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
- **health:** compose probes the console every 30 s and reports `healthy` / `unhealthy` — a
  report, never a restart ([Health](#health)). systemd had no equivalent.

## Caveats

- **Credentials are set in the UI**, not on the host — see [SETUP.md](SETUP.md). All keys, tokens,
  and util secrets go in **Settings → Secrets** (one store, injected at run time). The Claude
  subscription uses the pinned CLIProxyAPI sidecar; see
  [proxy setup](../docs/claude-proxy-cutover.md). The CLI installation is retained for
  independent library utilities, not scheduler model transport.
- **Headless browsing works out of the box.** The image carries Chromium's system libraries;
  the `page-fetch` util downloads Playwright's Chromium itself on first use (once — the
  browser cache `~/.cache/ms-playwright` is bind-mounted). That covers every util that drives a
  THROWAWAY browser. The utils that need a **logged-in** one (`job-scrape`, `job-inbox`,
  `job-apply`, `browser-session`) are served by the `chrome` sidecar instead, at
  `http://172.30.7.10:9222` — see [docs/browser-sessions.md](../docs/browser-sessions.md). Its
  profile is a bind mount and part of the migration bundle, but the sessions inside it are not
  portable off a desktop machine: signing in is a one-time human step per host.
- **Dependency changes** committed by self-audit or scheduler-builder are picked up on the next restart (`uv run`
  re-syncs from the mounted `pyproject.toml`), exactly like the systemd unit.
- **A line without IPv4 needs the NAT64 override.** `deploy/nat64.sh on` restarts `rsched` and
  `chrome` with the public DNS64 resolvers in `compose.nat64.yml`, so IPv4-only hosts (GitHub)
  are reached through a translating gateway while dual-stack hosts still go direct; the Claude
  proxy is deliberately left out. `deploy/nat64.sh status` shows which resolvers each container
  uses. Turn it `off` the day IPv4 returns: a third party's gateway then sees the destination of
  every IPv4-only call for no benefit.
- **Host mounts (`/mnt`, `/srv`, `/tmp`) are bind-mounted with `rslave` propagation** so the
  fs-roots picker can offer USB disks / NAS mounts, including ones mounted on the host AFTER the
  container started (F190: without the bind, the daemon's mount namespace has no `/mnt` at all
  and the picker shows an explained empty state). Takes effect on the next `docker compose up -d`;
  drop a volume line if the host has no such directory.
- **The committed `docker-compose.yml` is this instance's**, host mounts and project workspaces
  included (`git-repos/LLMSecTest_agentic` and its grant folder). Another host's extra paths —
  a project share, a document vault (cf. R35, where a clarify run could not read
  `/mnt/sshd_volume1/...`) — and its resource ceilings go in a `docker-compose.override.yml`, which
  Compose merges automatically and which is gitignored, so an update never clobbers them. A path
  must ALSO be granted to the routine as an fs-root (the routine's Filesystem roots) before a run
  may read it; a bind mount alone makes it visible to the container, not to the sandboxed run.

## The image

Three images are built here — `Dockerfile` (the engine), `deploy/Dockerfile.chrome` and
`deploy/Dockerfile.tor` — and one is pulled as is. **Every image any of them reads is pinned by
digest, its tag kept beside it** (resolved 2026-10-01; the full digests live in the files):

| Pin | What it is | Used for |
| --- | --- | --- |
| `python:3.12-slim-bookworm@sha256:392307d2…` | Python 3.12.14 on Debian 12 | the engine's base |
| `node:24-bookworm-slim@sha256:0e0ff40c…` | Node 24.21.0, npm 11.19.0 | the engine copies `node`, npm, npx, corepack and the headers out of it |
| `ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65…` | uv 0.12.13 | the engine copies `uv` and `uvx` out of it |
| `debian:bookworm-slim@sha256:3783cc01…` | Debian 12 | the `chrome` and `tor` bases |
| `eceasy/cli-proxy-api:v7.2.156@sha256:7f434559…` | CLIProxyAPI | the `cliproxy` service (in `docker-compose.yml`) |

A tag moves — `python:3.12-slim-bookworm` is re-pushed for every Debian point release and Python
patch — so a rebuild used to start from whatever had been pushed that day, and two builds of one
commit could differ with nothing in the repository to say why. A digest makes the base a diff.
Docker reads only the digest when both are given; the tag records what it was resolved from. Each
digest is the image INDEX, which covers every platform, so the same line still builds on amd64
and arm64. The two images the engine only copies from are build stages of their own, so they are
pinned on FROM lines like the base, and `tests/test_deploy_image.py` holds every FROM, every
`COPY --from` (a stage name, never an image) and every pulled image to that shape.

Deliberately NOT pinned: the apt packages (installed from the live Debian mirror at build time,
so they carry that day's security fixes), Google Chrome (the stable channel — the sites the
browser utils read score its version) and the `claude` CLI (`npm install -g`, newest at build).

The price of a pinned base is that it no longer picks up Debian or Python security rebuilds by
itself. Bump on a schedule — monthly is plenty — and whenever a CVE in the base matters.

### Node 24 LTS

Copied out of the official `node` image instead of installed from NodeSource's apt repository: the
binary is the nodejs.org release build, whose GPG-signed checksum that image verified when it was
built, and the digest pins those bytes — no third-party repository, no `curl | bash` installer. It
lives at `/usr/local/bin/node` (NodeSource put it at `/usr/bin/node`); nothing in this repository
or the library calls it by an absolute path, and a routine's own notes that say "`/usr/bin/node` is
v20" describe the old image. What runs on it:

- **the `claude` CLI's install.** `@anthropic-ai/claude-code` is a native binary now, placed by the
  package's npm postinstall, and the package declares `engines.node >=22` — the Node 20 this
  replaced was already below it. The postinstall is allowed by name (npm 11 warns that install
  scripts are "not yet covered by allowScripts"), and the build runs `claude --version`, so a
  postinstall that silently left the package's stub in place fails the build instead of the
  `claude` util's first call.
- **`node --check`,** the JS syntax gate of library utils: `code-search` (`sym check`, which
  self-audit runs over the console's ES modules) and `html js-check` (inline scripts).
- **what runs start themselves:** a routine's own JS test harnesses, and npm builds — a project
  whose `engines` floor is 22 no longer needs its own Node fetched into `/tmp`.

Not Playwright: its Python wheel carries its own Node driver. Node 24 is maintained until April
2028; moving to the next LTS is a decision, made in the Dockerfile's tag and `NODE_MAJOR` in
`tests/test_deploy_image.py` together.

### Bumping a digest

```bash
docker buildx imagetools inspect node:24-bookworm-slim | sed -n 's/^Digest: *//p'
```

That prints the INDEX digest — the one to pin. The per-platform digests listed under `Manifests:`
are not: pinning one of those ties the build to a single architecture. Replace what follows
`@sha256:` on the FROM line (or the compose `image:` line, for cliproxy), keep the tag, then
[rebuild and verify](#rebuilding). To see every pin beside what its tag resolves to today:

```bash
grep -h '^FROM ' Dockerfile deploy/Dockerfile.* | awk '{print $2}' | sort -u | while read -r pin; do
  printf '%s\n    now %s\n' "$pin" "$(docker buildx imagetools inspect "${pin%@*}" | sed -n 's/^Digest: *//p')"
done
```

A MAJOR move changes the tag as well, and each one has a second place that moves with it: Python
with `.python-version` (`tests/test_policy.py` holds the two together), Node with `NODE_MAJOR`,
Debian (`bookworm` → `trixie`) in every Dockerfile that names it.

### Rebuilding

```bash
cd ~/git-repos/routine-scheduler
docker compose build                        # all three images: rsched, chrome, tor
docker compose up -d                        # recreates each container whose image changed
```

For a NEW IMAGE, `up -d` replaces the running `rsched` process — unlike a code change, which it
never reloads (CLAUDE.md, "Deploy") — and any routine running at that moment goes with it. Check
`active_runs` on `/api/status` first, or rebuild in a quiet hour. The browser's profile and tor's
state survive the recreate (a bind mount and a named volume). Then:

```bash
docker inspect --format '{{.State.Health.Status}}' rsched   # starting, then healthy (below)
docker compose exec -u mark rsched node --version           # v24.x
docker compose exec -u mark rsched claude --version         # the CLI answers
docker compose exec -u mark rsched uv --version             # uv 0.12.13
docker compose exec -u mark rsched gh --version             # its apt repository was read
```

`-u mark` is the runtime user: an `exec` as root is how root-owned files landed in mark's caches
before ([When the console goes slow](#when-the-console-goes-slow)).

## Health

The `rsched` service carries a compose healthcheck: every 30 s, `curl` inside the container asks
for `http://127.0.0.1:8321/manifest.webmanifest`. That route is the app's own and takes no token
by design — a browser fetches a manifest without credentials — so no credential is stored in the
compose file or the image to probe it. It lives in compose, beside the restart policy, so a retune
is a `docker compose up -d`, not a rebuild. The states:

- **`starting`** — no probe has answered yet. The port opens only at the END of a boot (`uv run`'s
  re-sync after a dependency change, the one-shot migrations, the seed sync, the library
  adoption; on a fresh host, the library clone), so misses in the first 5 minutes
  (`start_period`) do not count. A normal boot reads healthy at the first probe, ~30 s in.
- **`healthy`** — the console answered its last probe. It says the web app SERVES — not which
  code is live (`build` on `/api/status` says that) and not that the scheduler is ticking.
- **`unhealthy`** — three probes in a row failed after the start period, about 1.5 minutes of
  silence: the process is up and the console does not answer, which is a starved threadpool or a
  wedged event loop more often than a crash. **Docker does not restart it** — `restart:
  unless-stopped` acts only when the process EXITS — so read why before you bounce it:

  ```bash
  docker inspect --format '{{json .State.Health}}' rsched | python3 -m json.tool   # the last 5 probes, in curl's words
  ```

  then [measure the slowness](#when-the-console-goes-slow) — a restart throws that evidence away.
- **no health at all, `Exited`** — the container is not running. Docker gives up on a failed
  START after one try (a vanished bind source is the one that has happened): `docker ps -a`, then
  check every bind source exists before starting it again.

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

## When the console goes slow

Measure before guessing. Every read model on this instance answers in well under a second in
isolation, so a request over two seconds is queueing or contention, and the daemon now records
both halves of that:

```bash
TOKEN=$(grep -oP '^token:\s*"?\K[^"]+' ~/.config/routine-scheduler/config.yaml)   # quoted or not
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8321/api/debug/slow      # the last 50 slow requests
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8321/api/debug/threads   # every thread's stack, in-flight count, threadpool tokens
docker logs rsched --since 1h 2>&1 | grep "slow request"
```

Both debug routes take the operator's primary token only. For a native sample of the whole
process (C frames included), the container carries `SYS_PTRACE` for exactly this. It runs as
root, which ptrace needs — so with root's OWN uv cache: a root `uvx` in `mark`'s cache leaves
root-owned entries that fail every util call in the instance until the entrypoint repairs them
at the next start (2026-08-26):

```bash
docker exec -e HOME=/root rsched sh -c 'uvx py-spy dump --pid $(pgrep -f "rsched daemon" | tail -1)'
```

