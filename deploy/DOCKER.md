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
- **`cliproxy`** (behind the `claude-proxy` profile, which `.env` names on a host that runs it —
  [one compose selection per host](#one-compose-selection-per-host)) — the CLIProxyAPI
  transport a Claude or Codex subscription is billed through. Its
  state lives in `.config/routine-scheduler/cliproxy/`, inside the inventory below, and holds
  the subscription logins only a person's OAuth consent re-mints. Being a pulled image it would
  run as its own root, so compose runs it as the instance's uid (`user:`), which keeps every
  file it writes readable to the backup. See [proxy setup](../docs/claude-proxy-cutover.md).

Container paths are always `/home/mark/...` (routines and config bake absolute paths, so they must
not change). Host paths are `${RSCHED_HOME}`-relative (default `/home/mark`).

---

## 1. On this machine — build + verify

The compose file refuses every command until `BROWSER_CDP_TOKEN` is set: it is the one
credential in front of the browser's CDP and noVNC ports, which authenticate nothing of their
own. Keep it in `.env` beside `docker-compose.yml` (gitignored, and carried by the migration
bundle with the checkout), and put the SAME value in **Settings → Secrets** under the same name
once the console is up — the console's browser relay and every util that declares it read it
from there. The same file holds the host's compose selection, the files and profiles every
compose command uses ([one compose selection per host](#one-compose-selection-per-host)).

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
| `.config/routine-scheduler` | **secrets**: `config.yaml` (tokens, endpoints, homes, `source_repo`), the Secrets store beside it, the cliproxy keys and its subscription logins |
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

The closing line says **what the snapshot cost**, in two figures: the bytes `rsync` reports having
transferred (summed from its own `--stats`, free — it already computed them), and how far the
share's used space moved across the run (`df` before and after). The first is the honest cost; the
second is the check on it, and it carries the alarm: a share that stopped hard-linking would make
rsync copy instead of link, so its used space would grow by a FULL snapshot while the transfer
stayed small — the run warns when the delta exceeds four times the transfer and a gigabyte. Until
0.374.2 that line was a `du -sh <yesterday> <today>` instead, which was wrong on the default share
and nowhere else: `/mnt/sshd_volume1` is `fuse.sshfs`, which gives every path a synthetic inode, so
`du` cannot recognise a hard link and counted every linked file again — it reported "14G new on the
share" for a night that transferred ~1.7 GB and moved the share's used space 539G → 540G, and spent
about seven minutes of network stat calls to get there.

A snapshot is built under a temporary name (`snapshots/.in-progress`) and takes its date only
once every home copied, so a failed or interrupted run never leaves a half snapshot that a
restore could pick or a later run could build on, and `latest` stays where it was. After a
**successful** run, retention keeps the **14 newest snapshots plus the newest of each of the 8
most recent older ISO weeks** — weeks that hold one, so nights the host was down cost no depth —
and deletes the rest: about ten weeks of history. A failed run deletes nothing, and no run
deletes the snapshot it just wrote. rsync's exit 24 — a file that vanished between its listing
and its copy, routine on a live instance — still completes the snapshot; any other failure
keeps none.

That includes a file the backup may not read: it runs as the host user, so one file a container
wrote as root with mode 0600 fails its home and the whole night with it. Every container that
writes a carried home therefore writes as the instance's uid (`RSCHED_UID`, 1000 by default).
The engine and `chrome` images drop to `mark` in their entrypoints, and the pulled `cliproxy`
image gets compose's `user:`. `tests/test_deploy_state.py` fails on a writer that does neither.
A `docker compose exec -u root` that writes into a home brings the failure back. On a
`Permission denied` in the backup's journal, list the offenders with
`find ~/<home> ! -readable`, then find out which writer made them before you chown them.
Until 0.373.1 the proxy ran as root, and its 0600 files failed `.config/routine-scheduler` from
2026-09-14 on ([the one-shot move](../docs/claude-proxy-cutover.md#deployment)).

Symlinks are copied as links, but **not their times** (`--omit-link-times`). The default share
is sshfs, and SFTP sets a path's times by following it on the NAS, so rsync could not time a
link there: "failed to set times on <link>: No such file or directory", exit 23. `conversations`
and the LLMSecTest workspace both hold links, so both failed night after night until 0.373.1,
and with them every snapshot. To read why a night failed, `journalctl --user -u rsched-backup`
now shows each home's rsync errors under its `FAILED` line. Before 0.373.1 those lines came from
a pipeline that had exited before journald could file them under the unit, so `-u` hid them and
only a time-window query (`journalctl --user --since …`) showed them.

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
install -m 0644 deploy/rsched-backup.service        ~/.config/systemd/user/
install -m 0644 deploy/rsched-backup-failed.service ~/.config/systemd/user/
install -m 0644 deploy/rsched-backup.timer          ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now rsched-backup.timer
systemctl --user start rsched-backup.service     # prove it once, then read the journal
journalctl --user -u rsched-backup.service -n 30
```

**A failed backup now says so, twice over, and the two paths fail independently.** The backup unit's
`OnFailure=` starts `rsched-backup-failed.service`, which POSTs one high-priority ntfy notification
naming the host and the `journalctl` command to read next. It takes `NTFY_URL` and `NTFY_TOPIC` from
`~/.config/routine-scheduler/secrets.env` through `EnvironmentFile=` — the same store the `ntfy` util
reads, so there is no second credential path and no secret in the unit; with neither set it logs that
and exits 0, because failing to REPORT a failure must not look like a different fault. That half is
systemd's deliberately: it still works when the scheduler daemon is down, which is exactly when a
backup failure matters. Separately the daemon reads the `.rsched-backup-completed` stamp and files a
`backup_stale` health event when the last COMPLETE snapshot is more than three days old
(`src/rsched/daemon/backup_watch.py`), which the console's blocked-work surface renders. Before
0.374.3 neither existed: this unit failed every night from 2026-09-12 to 2026-10-01, nothing in the
product read backup state at all, and nineteen unbacked nights passed in silence until someone
happened to read the journal.

```bash
systemctl --user start rsched-backup-failed.service   # prove the push path once, on purpose
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

Then browse to **http://192.168.0.128:8321** (token is in the migrated `config.yaml`). The
bundle carries `.env`, so this host starts on the source host's compose selection:
`deploy/nat64.sh status` shows it. Where the line has IPv4 of its own, `deploy/nat64.sh off`
drops NAT64.

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

## One compose selection per host

Which files make up the project and which profiles are on belong to the HOST, so they live in the
one place Compose reads on every command run from the checkout's root — `.env` — and never in a
command's flags:

| Line in `.env` | Written by | Present while |
| --- | --- | --- |
| `COMPOSE_PROFILES=claude-proxy` | you, once, when the subscription proxy is set up ([proxy setup](../docs/claude-proxy-cutover.md#deployment)) | the host runs `cliproxy` |
| `COMPOSE_FILE=docker-compose.yml:docker-compose.override.yml:compose.nat64.yml` | `deploy/nat64.sh on`, naming the override only when it exists; `off` deletes the line | NAT64 is on ([Caveats](#caveats)) |

So `build`, `up -d`, `exec`, `logs`, `stop` and `start` take no file flag and no profile flag —
and must not be given one: either REPLACES what `.env` names instead of adding to it. A file
list — on the command line or in `COMPOSE_FILE` — also switches off Compose's auto-loading of
`docker-compose.override.yml`, which is why the list nat64.sh writes names the override itself.
A list typed by hand drops this host's memory ceilings without a word. With no `COMPOSE_FILE`
line Compose uses its own default: `docker-compose.yml`, plus the override when it exists.

Two more things undo the selection silently. A `COMPOSE_FILE` or `COMPOSE_PROFILES` exported in
the shell beats `.env`. A command run from a subdirectory finds the project by searching upward
and never reads `COMPOSE_FILE` at all.

`deploy/nat64.sh status` prints the selection and, for each container, its resolvers, its memory
ceiling and whether its config is the one the selection gives it — `config differs` means the
next `docker compose up -d` recreates it. `tests/test_deploy_selection.py` drives nat64.sh
through a stand-in `docker` that resolves the selection the way Compose does. It also fails on
any tracked file that gives a compose command a selection of its own.

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
- **A line without IPv4 needs the NAT64 override.** `deploy/nat64.sh on` writes the
  `COMPOSE_FILE` line into `.env` with `compose.nat64.yml` last
  ([one compose selection per host](#one-compose-selection-per-host)) and runs
  `docker compose up -d`, which recreates `rsched` and `chrome` with its public DNS64 resolvers:
  IPv4-only hosts (GitHub) are reached through a translating gateway while dual-stack hosts still
  go direct. The Claude proxy is deliberately left out. Every later compose command reads the
  same line, so a rebuild keeps the resolvers. The script refuses, changing nothing, while a
  service that has a container here is outside the selection — on this host, while `.env` lacks
  `COMPOSE_PROFILES=claude-proxy`. `deploy/nat64.sh status` shows the selection and each
  container's resolvers. Turn it `off` the day IPv4 returns: a third party's gateway then sees
  the destination of every IPv4-only call for no benefit.
- **Host mounts (`/mnt`, `/srv`, `/tmp`) are bind-mounted with `rslave` propagation** so the
  fs-roots picker can offer USB disks / NAS mounts, including ones mounted on the host AFTER the
  container started (F190: without the bind, the daemon's mount namespace has no `/mnt` at all
  and the picker shows an explained empty state). Takes effect on the next `docker compose up -d`;
  drop a volume line if the host has no such directory.
- **The committed `docker-compose.yml` is this instance's**, host mounts and project workspaces
  included (`git-repos/LLMSecTest_agentic` and its grant folder). Another host's extra paths —
  a project share, a document vault (cf. R35, where a clarify run could not read
  `/mnt/sshd_volume1/...`) — and its resource ceilings go in a `docker-compose.override.yml`,
  which is gitignored, so an update never clobbers them. Compose merges it by itself only while
  nothing names the file list. nat64.sh's list names it; a list typed by hand does not. Neither
  did the first version of nat64.sh: every container ran without a memory limit from 2026-09-27
  until the 0.372.1 deploy. Create or delete the override while NAT64 is on, then run
  `deploy/nat64.sh on` again, so the list follows. A path
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
deploy/nat64.sh status                      # the selection both commands read; what runs now
docker compose build                        # all three images: rsched, chrome, tor
docker compose up -d                        # recreates each container whose image or config changed
```

Both run bare, from the checkout's root: they read this host's files and profiles from `.env`
([one compose selection per host](#one-compose-selection-per-host)), so the rebuilt fleet keeps
the override's memory ceilings, the NAT64 resolvers while they are on, and `cliproxy`. A file
flag or a profile flag here would replace that selection for the one command.

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

## HTTPS for the console (Web Push needs a secure context)

The console serves plain HTTP on `:8321`. Everything works that way — the API, the live streams, the
browser screen — with ONE exception: **Web Push needs a secure context**, which is a
property of the URL, not of the network. A VPN gives you REACH, not a secure context, so
`http://<lan-ip>:8321` over a VPN still cannot subscribe a device to push. That is the
state this host is in; the rest of the console is unaffected and no deploy step is pending.

To get push working, terminate TLS in front of the port and point the instance at the
result:

1. A DNS name for the instance — a DynDNS name is enough; a public IPv4 is not
   required if the name resolves inside your VPN.
2. A TLS-terminating reverse proxy in front of `127.0.0.1:8321` with a certificate for
   that name. **This compose file ships no proxy**: which one, and where its certificate
   comes from, is host-specific, so it is yours to run.
3. Set `public_url` to that https BASE (no path) in **Settings → server process**. It is
   also the OAuth redirect target — providers are sent to `<public_url>/oauth/callback`
   and most refuse a non-https redirect URI — so OAuth connections need this same step.
4. Subscribe each device under **Settings → Notifications**.

Whatever terminates TLS must pass `X-Forwarded-Proto`: the app reads it to decide the
`secure` flag on the browser-screen cookie (`web/api_browser_view.py`), because behind a
proxy the request it sees is plain `http`.

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

