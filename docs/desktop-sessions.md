# Agent desktops — a computer of its own for every routine

Some work exists only as a graphical application: a desktop program with no API, a settings
dialog, a file manager, a web app that has to be clicked through rather than fetched. The
`desktop` compose service gives every routine that needs one **a desktop computer of its own**:
a Cloud Hypervisor microVM running Debian with the **XFCE** desktop, which the routine drives with
the `desktop` util — every mouse, keyboard, window and clipboard operation a person can make,
the screen's elements by name, and the routine's granted folders mounted live inside it. The
operator watches every desktop from the console and can take any of them over.

It is a sibling of the `chrome` sidecar and borrows its structural decisions (its own service, a
fixed address on the `browser` network, bearer-token doors in front of every port). It is not a
second signed-in browser: a desktop's Firefox holds none of the operator's sessions, and the
`browser-sessions` permission stays the way into those.

## Why one VM per routine — and why the browser is different

A desktop has ONE pointer and ONE keyboard focus. Two runs driving the same screen would type
into each other's windows, and anything mounted into a shared VM would be visible to every
routine using it. So each routine (each conversation, each background task) gets its own VM,
its own home disk and only its own folders.

The shared browser does not need this: there, each caller opens a tab of its own and acts only
in it over CDP, which drives background tabs without focus — and sharing is the browser's
purpose, since every routine inherits the logins the operator made once.

RAM is the limit on VMs, so the sidecar runs a fixed number at once (`RSCHED_DESKTOP_MAX_VMS`,
default 2, `RSCHED_DESKTOP_MEM_MB` each, default 3072). A desktop starts on its routine's first
`desktop` command (about 5 s to a ready desktop), and stops after `RSCHED_DESKTOP_IDLE_MIN`
(default 20) without a command, or at once with `gu desktop stop`. A routine asking while every
slot is held is told which desktops hold them and how long each has been idle, and carries on
with work that needs no screen — a run never blocks on a slot.

## Why a VM, why XFCE, why X11

**A VM, not a container.** A desktop is a whole operating system an agent clicks around in — it
installs things, renders arbitrary web pages, opens files from anywhere. A container shares this
host's kernel; a KVM guest has its own. Cloud Hypervisor because it is a small Rust VMM (the
class Firecracker belongs to) with what this needs and Firecracker lacks: qcow2 overlays, virtio-fs
and device hot-plug. The desktop needs **no display device**: Xvnc renders into memory inside the
guest and serves the picture as VNC, so the VMM only provides disks, a network link and a serial
line.

**X11, not GNOME on Wayland.** Programmatic access decides it. On X11 every input a person makes
is an XTEST event (`xdotool`): a click is a click to every application, a drag between two windows
is a real drag-and-drop, a chord is the chord. Window management is EWMH (`wmctrl`). And the
accessibility tree reports every widget's box in SCREEN coordinates. On Wayland, synthesized
input needs a portal and a consent dialog, and AT-SPI can only report boxes relative to their
window, because a Wayland client does not know where its window is. Current GNOME ships no X11
session; XFCE does, and its applications are GTK with a complete accessibility tree. GNOME
applications run inside it unchanged.

One X11 detail that cost a round of testing: `wmctrl -G` and `xdotool getwindowgeometry` both
add the decoration offset twice under a reparenting window manager (a window drawn at 5,56 is
reported at 10,85). The agent reads `xwininfo` plus `_NET_FRAME_EXTENTS` instead and reports each
window's OUTER box, which is what `move` takes; `resize` converts back, because wmctrl sizes the
client area.

## Pointing at things: names first, pixels last

A model that sees a screenshot has to come back with a pixel. The util offers three ways to get
one, in order of reliability, and none of them hides another model call behind the run:

1. **The accessibility tree** (`tree`, `find TEXT`). Every widget GTK, Qt, Firefox and Electron
   expose, with role, name, state, actions and screen box, each given a NUMBER. A run clicks
   `--el N` (the element's live centre) or calls the element's own action (`act N` — no
   coordinates at all); `read N` returns an element's text and `set-text N` replaces it, so an
   editor or a form field is read and written without OCR or typing. The walk is bounded
   (4 000 nodes, 8 s) and says when it stopped early. It lists only what a person operates:
   structure (frames, documents, sections, landmarks) is left out, Firefox's `clickAncestor`
   pseudo-action on every node is ignored, a wrapper carrying its target's name is listed once as
   the target, and a nameless row takes the words of its first labelled child.
2. **Marks** (`shot --marks`). The same elements boxed and numbered ON the screenshot (the
   Set-of-Mark technique), each tag placed where no earlier tag covers it, so a run that looks at
   the picture answers with a number.
3. **Grid + zoom**, for what nothing names — a canvas, an image, a game. `shot --grid` draws a
   labelled ruler; `zoom X Y` returns a close-up of the predicted point, enlarged, with a fine
   grid labelled in SCREEN coordinates and a crosshair on the guess. Predict on the whole screen,
   correct once on the close-up: two or three looks.

**Not built, on purpose: a recursive quadrant search** (crop into overlapping quadrants, ask a
vision model "is the target in this one?", recurse until a pixel). It costs ~20 vision calls per
click, a crop at depth loses the context that says WHICH "Save" is meant, a single wrong "yes" at
depth one sends every later level into the wrong region, and the whole search would be a hidden
loop of model calls inside one action — the second agent loop this system bans. Predict-then-zoom
gets the same refinement with the run's own model on its own turns, where the transcript shows it.

Every acting command waits for the screen to settle and saves the screenshot taken after it
(`state/desktop-view.png` by default), so "what did that do?" is one image-viewing action away.

## Folders: mounted live, proved by the run's own sandbox

`gu desktop mount FOLDER [--rw] [--name N]` shows a folder inside the routine's desktop at
`~/mnt/<name>`, while the VM runs — no reboot: the broker starts a `virtiofsd` for it and
Cloud Hypervisor hot-plugs the virtio-fs device (`ch-remote add-fs`). `unmount NAME` removes it;
stopping the desktop ends every share. A read-only share is read-only in `virtiofsd` itself, so
not even the guest's root can write through it.

**What a run may mount is exactly what its sandbox may touch — no new grant model.** The util
runs in the run's Landlock jail; the broker sees the same homes at the same paths (compose
mirrors the engine's binds). A read-write mount needs a one-time proof file written INSIDE the
folder (the jail's write grant); a read-only mount needs a digest of the folder's listing — names,
types, sizes and nanosecond mtimes — which only a jail allowed to list it can compute. An empty
folder cannot prove read access, so it mounts read-write or not at all. Two refusals are the
broker's own: a read-write share may never hold a routine's root (its `routine.yaml`, which no
action may write — share `artifacts/` instead) or any folder above the routines home, and only the
mirrored homes can be shared at all (`DESKTOP_SHARE_ROOTS`).

`put` / `get` copy single files through the guest's home, for when a mount is more than needed.

## Who is calling: identity from the sandbox, not the token

Every routine holding the desktop permission holds the same `DESKTOP_VM_TOKEN`, so the token
cannot say WHICH routine is calling. The kernel can: a util runs in its run's jail, which can
write the routine's own directory and nothing of another routine's. So every call writes a
one-time file into the util's working directory and names it in a header; the broker reads it
there, compares, deletes it (no proof is good twice), and takes the identity from the directory's
top — `routines--foo` for anything inside `routines/foo` (a child run included). A run cannot
forge a proof in a directory its jail does not open — not through the util's arguments, and not
through a script that declared the token.

## The parts

```
engine container ──(util: Bearer DESKTOP_VM_TOKEN + identity proof)──▶ :8790 auth proxy ─▶ broker
console relay ───(Bearer DESKTOP_OPERATOR_TOKEN)─────────────────────▶ :6080 auth proxy ─▶ noVNC
                                    rsched-desktop container (trixie)
                       broker ── per routine: cloud-hypervisor ── virtiofsd per share
                         │ vmtap<i> (one /30 per slot), NAT, firewall, dnsmasq
                         ▼
          guest 192.168.249.(4i+2) (KVM): Xvnc :1 (5900) ── XFCE ── desktop agent (8790)
```

- **`deploy/Dockerfile.desktop`** builds the guest OS (bookworm) as an ordinary image stage,
  turns it into an ext4 base image plus the kernel and initrd (booted directly — no
  bootloader), and puts them beside the VMM, `virtiofsd` and `qemu-img` in the sidecar image
  (trixie, the first Debian that packages virtiofsd). Cloud Hypervisor is the upstream static
  release, pinned by version and sha256.
- **`deploy/desktop-entrypoint.sh`** makes one tap and /30 per slot, the NAT, the firewall and
  the DNS forwarder as root, then drops to `mark` and starts the two token proxies, noVNC and the
  broker.
- **`deploy/desktop-broker/`** — `broker.py` (the HTTP door and routing), `proof.py` (identity
  and folder proofs), `vms.py` (the fleet: slots, boot, idle stop) and `shares.py` (live
  folders). Each VM's system disk is a qcow2 overlay on the read-only base, so a boot costs no
  copy and every boot is the image's system; its home disk is `homes/<name>.img` and persists.
  Everything that differs between VMs — address, gateway, zone, name — is on the kernel command
  line, applied by the guest's `desktop-boot` before its network starts.
- **`deploy/desktop-guest/agent/`** is the control agent inside every guest: `xctl.py` (input,
  windows, clipboard), `a11y.py` (the accessibility walk), `screen.py` (capture and overlays) and
  `agent.py` (its HTTP door). It runs inside the X session, because it needs that session's
  DISPLAY and D-Bus to reach the accessibility bus. Its one root action is
  `/usr/local/sbin/desktop-mount`, by sudo, which mounts a virtio-fs tag under `~/mnt` and nothing
  else.
- **`util-seed/utils/desktop`** is the client every routine calls; **`desktop-sessions`** is the
  permission doc that reserves it.

## Security

- **Two doors, two tokens.** The broker port answers `DESKTOP_VM_TOKEN` — the routines'. The
  screens (noVNC) and the fleet listing answer only `DESKTOP_OPERATOR_TOKEN`, which the console
  holds and no routine is ever granted: with the routines' token a routine could otherwise open
  another routine's screen and bypass the broker's identity check. Each screen is reached by a
  random per-boot token, never by its routine's guessable name. The entrypoint refuses to start
  without both tokens, or with the two equal.
- **A guest reaches the internet and nothing private** — not the LAN, not this host's other
  containers, not the console, not the signed-in browser, and not another routine's desktop: the
  firewall drops every packet from a guest to 10/8, 172.16/12, 192.168/16, 100.64/10 and
  169.254/16, and accepts only DNS into the container itself.
- **The agent inside a guest refuses browser-originated requests.** A page in the desktop's own
  Firefox can reach the agent's address, and a "simple" cross-origin POST needs no preflight, so
  every request must be `application/json` and carry no `Origin` header. And it only ever answers
  the broker: the guest link is private to the sidecar.

## Disks and backups

Under `~/desktop-vm` on the host: `homes/<name>.img` (each routine's home, 16 GB sparse,
`DESKTOP_HOME_GB`), `run/<name>/` (the running VM's overlay, sockets and logs — gone when it
stops) and `logs/` (the last console and VMM log of each desktop, for a boot that failed). None of
it is carried by `backup.sh` or `bundle.sh` (`STATE_PATHS_NOT_CARRIED`): a home disk is one
multi-GB file that changes whenever its desktop is used, so a dated snapshot would store it whole
every night. A new host starts fresh homes. Delete a routine's home image (desktop stopped) to
start that routine's desktop over.

## Operating it

Enable it once per host (deploy/DOCKER.md, "The agent desktops"):

1. Put two DIFFERENT tokens in `.env` — `DESKTOP_VM_TOKEN=…` and `DESKTOP_OPERATOR_TOKEN=…` —
   and the same values in the Secrets store under the same names.
2. Add `desktop` to `COMPOSE_PROFILES` in `.env`, then `docker compose up -d desktop`.
3. Grant a routine the `desktop-sessions` permission and the `DESKTOP_VM_TOKEN` secret — never
   `DESKTOP_OPERATOR_TOKEN`.

The broker's own log is `docker logs rsched-desktop` (`[broker]` lines: boots, stops, shares).

## The console: watching and taking over

The console shows the desktops once two server settings are set (Settings → Server):
`desktop_broker_url`, the broker (here `http://172.30.7.20:8790`), and `desktop_view_url`, the
noVNC page (here `http://172.30.7.20:6080/vnc.html`); with either blank there is no Desktops link
and no dock. `GET /api/desktops` asks the broker's operator-only `/fleet` with both secrets from
the central store — `DESKTOP_VM_TOKEN` as the bearer, `DESKTOP_OPERATOR_TOKEN` as
`X-Desktop-Operator` — maps each `<home>--<slug>` name back to its routine, conversation or
background task (linking the task's conversation), and drops any screen token that is not 32 hex;
`POST /api/desktops/{name}/stop` calls `/fleet_stop`.

The **Desktops** page (`#/desktops`) is the take-over surface: one card per desktop with its
owner, slot, up and idle time, mounted folders, a Stop that asks first, and the desktop's
INTERACTIVE screen — type and click there and you are working on the routine's desktop (Xvnc runs
`-AlwaysShared`, so the page and the dock watch one desktop together). The **Desktop** dock in the
console's bottom-right `#docks` column — beside the LLM activity and browser docks, which can no
longer cover one another — is a read-only view of the most recently active desktop, with a
switcher when several run; it connects only once it is open and visible.

Every screen loads through the console's own relay, the one the browser's screen uses
(`web/screen_proxy.py`, `web/api_screen_view.py`, one `Screen` row per screen): `/desktop-view/…`,
authenticated upstream with `DESKTOP_OPERATOR_TOKEN`, forwarding only the socket's
`?token=<32 hex>`, with the operator's browser holding a pass cookie scoped to that path
(`POST /api/desktop-view/pass`). A run's `RSCHED_API_TOKEN` reaches neither the fleet nor a
screen: `/api/desktops`, `/desktop-view` and `/browser-view` are routine-token denied reads, and
the stop and the pass are mutations that token never makes. So the routines' token opens no screen
at either end — the noVNC door refuses it, and the console will not relay for it.
