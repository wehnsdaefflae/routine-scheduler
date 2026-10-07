# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""desktop — drive this routine's own DESKTOP (a VM running XFCE) the way a person would.

usage: gu desktop status | stop
       gu desktop shot [--marks] [--grid [STEP]] [--scope active|all|APP] [--all] [--out PNG]
       gu desktop zoom X Y [--radius PX] [--factor N] [--out PNG]
       gu desktop tree [--scope active|all|APP] [--all] [--json]
       gu desktop find TEXT [--role ROLE] [--scope …]
       gu desktop click (X Y | --el N) [--button left|middle|right] [--double] [--mods ctrl,shift]
       gu desktop move X Y | drag X1 Y1 X2 Y2 [--mods …] | scroll X Y [--dy N] [--dx N]
       gu desktop type TEXT [--paste] | key CHORD [CHORD …] [--hold | --release]
       gu desktop act N [--action NAME] | set-text N TEXT | read N | focus N
       gu desktop windows | window OP (--id ID | --title TEXT) [--x --y --w --h] | workspace N
       gu desktop clipboard [--set TEXT] | launch [--out PNG] [--] CMD [ARG …]
       gu desktop put LOCAL REMOTE | get REMOTE LOCAL
       gu desktop mount FOLDER [--rw] [--name NAME] | unmount NAME | mounts
       gu desktop --selftest
calls: (none)
tags: desktop, gui, vm, computer-use, screenshot, accessibility
secrets: DESKTOP_VM_TOKEN
net: outbound
fs: roots

Every routine has a desktop of its OWN: a KVM guest in the `desktop` sidecar
(docs/desktop-sessions.md) running Debian + XFCE on an X11 server, so every mouse, keyboard and
window operation a person can make is available, and every GTK/Qt/Firefox widget is NAMED in the
accessibility tree with its screen box. It starts on the routine's first command (~15 s), keeps
its home folder between runs, and stops after a while idle (`stop` ends it now). When every
desktop slot is taken the command says by whom — carry on with work that needs no screen.

FOLDERS: `mount FOLDER` shows one of the run's granted folders live inside the desktop at
~/mnt/<name> (read-only; --rw to let desktop apps write it). Only folders this run may read (or,
with --rw, write) can be mounted, and never a routine's own root read-write — mount a subfolder.

FINDING WHAT TO CLICK — in this order:
  1. `find TEXT` / `tree`: elements by role and name, each with a NUMBER. Then `click --el N`,
     or `act N` (the widget's own action, no coordinates at all). Exact, no vision needed.
  2. `shot --marks`: the screenshot with every listed element boxed and numbered — look at it
     and answer with a number.
  3. Only for things the tree cannot name (a canvas, an image, a game): `shot --grid`, read a
     coordinate off the ruler, then `zoom X Y` around it — a close-up with a fine grid in SCREEN
     coordinates and a crosshair on X,Y — and correct once before you click.
Numbers are valid until the next listing (tree, find, shot --marks) replaces them.

Every acting command (click, move, drag, scroll, type, key, act, set-text, focus, window,
workspace, launch) waits for the screen to settle and saves the screenshot AFTER it to
state/desktop-view.png (--out to change, --no-shot to skip) — look at it with your image-viewing
action to see what the action did. Files move through the guest user's home only (put / get).

Reaches http://172.30.7.20:8790 (DESKTOP_VM_URL overrides) with `Authorization: Bearer
$DESKTOP_VM_TOKEN`, and proves WHICH routine is calling by writing a one-time file into its own
working directory — something only this routine's sandbox can do. Every request fits inside the
call's deadline (RSCHED_UTIL_TIMEOUT_S).
--selftest is offline: it serves a fake desktop on loopback and drives every command at it."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://172.30.7.20:8790"
TOKEN_VAR = "DESKTOP_VM_TOKEN"
VIEW = "state/desktop-view.png"
ZOOM_VIEW = "state/desktop-zoom.png"
REQUEST_TIMEOUT_S = 90
REPORT_MARGIN_S = 5
LEGEND_MAX = 200


class Failure(Exception):
    """Something the caller is told in one line, with exit status 2."""


# ---- transport ---------------------------------------------------------------------------

def _timeout() -> float:
    budget = os.environ.get("RSCHED_UTIL_TIMEOUT_S", "")
    if budget.replace(".", "", 1).isdigit():
        return max(5.0, min(REQUEST_TIMEOUT_S, float(budget) - REPORT_MARGIN_S))
    return REQUEST_TIMEOUT_S


PROOF_PREFIX = ".desktop-proof-"


def _write_proof(directory: Path) -> tuple[Path, str]:
    """A one-time file only a caller allowed to write `directory` can make: 'NAME:VALUE'."""
    name = PROOF_PREFIX + secrets.token_hex(8)
    value = secrets.token_hex(32)
    path = directory / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value)
    return path, f"{name}:{value}"


def listing_digest(directory: Path) -> str:
    """The proof of READ access to a folder: sha256 over its sorted top-level entries (name,
    type, size, mtime_ns), proof files left out. The broker computes the same (proof.py)."""
    rows = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.startswith(PROOF_PREFIX):
                continue
            st = entry.stat(follow_symlinks=False)
            rows.append(f"{entry.name}\0{stat.S_IFMT(st.st_mode)}\0{st.st_size}\0"
                        f"{st.st_mtime_ns}")
    if not rows:
        raise Failure(f"{directory} is empty — read access to it cannot be shown; mount it "
                      "--rw, or put a file in it first")
    rows.sort()
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def call(op: str, payload: dict | None = None, *, folder_proof: Path | None = None) -> dict:
    """POST one operation to this routine's desktop and return its JSON reply."""
    url = os.environ.get("DESKTOP_VM_URL", DEFAULT_URL).rstrip("/") + "/" + op
    token = os.environ.get(TOKEN_VAR, "").strip()
    if not token:
        raise Failure(f"{TOKEN_VAR} is not set — the desktop's door needs it. Declare it on the "
                      "routine's secrets (it is in the store as soon as the sidecar is set up).")
    workdir = Path.cwd().resolve()
    made: list[Path] = []
    try:
        proof_path, proof = _write_proof(workdir)
        made.append(proof_path)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                   "X-Desktop-Workdir": str(workdir), "X-Desktop-Proof": proof}
        if folder_proof is not None:
            try:
                folder_path, folder = _write_proof(folder_proof)
            except OSError as exc:
                raise Failure(f"cannot write into {folder_proof} ({exc.strerror}) — this run "
                              "may not write there, so it cannot mount it read-write") from None
            made.append(folder_path)
            headers["X-Desktop-Folder-Proof"] = folder
        req = urllib.request.Request(url, data=json.dumps(payload or {}).encode(),
                                     method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:  # noqa: S310 — fixed URL
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body).get("error", body)
        except ValueError:
            detail = body.strip()
        if exc.code == 401:
            raise Failure(f"the desktop refused the token: {detail}") from None
        if exc.code == 503:
            raise Failure(f"no desktop available right now: {detail}") from None
        raise Failure(f"desktop /{op} failed ({exc.code}): {detail}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise Failure(f"the desktop sidecar is not reachable at {url}: {exc}. Is the `desktop` "
                      "compose profile running? (docs/desktop-sessions.md)") from None
    except OSError as exc:
        raise Failure(f"could not prove this routine's identity: {exc}") from None
    finally:
        for path in made:
            path.unlink(missing_ok=True)


# ---- rendering -----------------------------------------------------------------------------

def save_png(b64: str, out: str) -> str:
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(b64))
    return str(path)


def legend_line(el: dict) -> str:
    flags = [f for f in ("focused", "checked") if el.get(f)]
    if el.get("enabled") is False:
        flags.append("disabled")
    tail = f" [{', '.join(flags)}]" if flags else ""
    acts = f" actions: {', '.join(el['actions'])}" if el.get("actions") else ""
    return (f"[{el['n']}] {el['role']} {json.dumps(el.get('name', ''), ensure_ascii=False)} "
            f"@{el['x']},{el['y']} {el['w']}x{el['h']}{tail}{acts}")


def legend(elements: list[dict], truncated: bool | None = None) -> str:
    lines = [legend_line(e) for e in elements[:LEGEND_MAX]]
    if len(elements) > LEGEND_MAX:
        lines.append(f"… {len(elements) - LEGEND_MAX} more — narrow with `find` or --scope")
    if truncated:
        lines.append("(the walk hit its node/time budget — this list is PARTIAL; scope it down)")
    if not elements:
        lines.append("(no elements — the window may not expose an accessibility tree; use a "
                     "shot with --grid and zoom instead)")
    return "\n".join(lines)


def report_after(reply: dict, out: str | None) -> str:
    parts = []
    after = reply.get("after")
    if after and out:
        w, h = after.get("size", ["?", "?"])
        parts.append(f"view: {save_png(after['png'], out)} ({w}x{h})")
    if reply.get("active") is not None:
        parts.append(f"active window: {json.dumps(reply['active'], ensure_ascii=False)}")
    return "; ".join(parts)


# ---- commands ----------------------------------------------------------------------------

def _shot_opts(args) -> dict:
    return {"shot": not args.no_shot, "settle": args.settle}


def _mods(raw: str | None) -> list[str]:
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


def run(args) -> str:  # noqa: C901, PLR0911, PLR0912 — one dispatch table, read top to bottom
    cmd = args.cmd
    if cmd == "status":
        r = call("vm_status")
        used = f"{len(r['fleet'])}/{r['slots']} desktops in use"
        vm = r.get("vm")
        if not vm:
            return (f"{r['name']}: no desktop running — it starts on the first desktop "
                    f"command (~15 s); {used}")
        shared = f", folders: {', '.join(vm['shares'])}" if vm["shares"] else ""
        return (f"{r['name']}: desktop {'running' if vm['ready'] else 'starting'} (up "
                f"{vm['up_s'] // 60} min, idle {vm['idle_s'] // 60} min, stops after "
                f"{r['idle_limit_s'] // 60} min idle{shared}); {used}")
    if cmd == "stop":
        return "desktop stopped" if call("vm_stop")["stopped"] else "no desktop was running"
    if cmd == "mount":
        folder = Path(args.folder).expanduser()
        folder = (Path.cwd() / folder).resolve() if not folder.is_absolute() else folder.resolve()
        if not folder.is_dir():
            raise Failure(f"{folder} is not a folder this run can see")
        payload = {"path": str(folder), "rw": args.rw, "name": args.name}
        if args.rw:
            r = call("mount", payload, folder_proof=folder)
        else:
            try:
                payload["digest"] = listing_digest(folder)
            except PermissionError:
                raise Failure(f"this run may not read {folder}, so it cannot mount it") from None
            r = call("mount", payload)
        return (f"mounted {r['path']} at {r['guest_path']} in the desktop "
                f"({'read-write' if r['rw'] else 'read-only'})")
    if cmd in ("unmount", "mounts"):
        r = call(cmd, {"name": args.name} if cmd == "unmount" else {})
        listing = "\n".join(f"{m['name']}: {m['path']} -> {m['guest_path']} "
                            f"({'rw' if m['rw'] else 'ro'})" for m in r["shares"])
        head = f"unmounted {args.name}" if cmd == "unmount" else ""
        return "\n".join(x for x in (head, listing or "(no folders mounted)") if x)
    if cmd == "shot":
        r = call("shot", {"marks": args.marks, "grid": args.grid or 0, "scope": args.scope,
                          "all": args.all, "scale": args.scale})
        head = f"screenshot: {save_png(r['png'], args.out or VIEW)} ({r['size'][0]}x{r['size'][1]})"
        if args.marks:
            return head + "\n" + legend(r.get("elements", []), r.get("truncated"))
        return head
    if cmd == "zoom":
        r = call("zoom", {"x": args.x, "y": args.y, "radius": args.radius, "factor": args.factor})
        x0, y0, x1, y1 = r["box"]
        return (f"zoom: {save_png(r['png'], args.out or ZOOM_VIEW)} — screen box ({x0},{y0})-"
                f"({x1},{y1}) at {r['factor']}x; grid labels and the crosshair ({args.x},"
                f"{args.y}) are SCREEN coordinates")
    if cmd in ("tree", "find"):
        payload = {"scope": args.scope, "all": getattr(args, "all", False)}
        if cmd == "find":
            payload.update(text=args.text, role=args.role)
        r = call(cmd, payload)
        if args.json:
            return json.dumps(r, ensure_ascii=False)
        return legend(r.get("elements", []), r.get("truncated"))
    if cmd == "windows":
        r = call("windows")
        if args.json:
            return json.dumps(r, ensure_ascii=False)
        return "\n".join(
            f"{'*' if w['active'] else ' '} {w['id']} ws{w['desktop']} @{w['x']},{w['y']} "
            f"{w['w']}x{w['h']} {json.dumps(w['title'], ensure_ascii=False)}"
            for w in r["windows"]) or "(no windows)"
    if cmd == "clipboard":
        if args.set is not None:
            call("clipboard", {"text": args.set})
            return "clipboard set"
        return call("clipboard")["text"]
    if cmd == "get":
        r = call("file_get", {"path": args.remote})
        dest = Path(args.local)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(r["b64"]))
        return f"copied {r['path']} -> {dest} ({dest.stat().st_size} bytes)"
    if cmd == "put":
        src = Path(args.local)
        if not src.is_file():
            raise Failure(f"{src} is not a file")
        r = call("file_put", {"path": args.remote,
                              "b64": base64.b64encode(src.read_bytes()).decode("ascii")})
        return f"copied {src} -> {r['path']} ({r['size']} bytes)"

    # -- acting commands: each reply carries the screenshot taken after it --------------------
    shot = _shot_opts(args)
    if cmd == "click":
        count = 2 if args.double else args.count
        common = {"button": args.button, "count": count, "mods": _mods(args.mods), **shot}
        if args.el is not None:
            r = call("element", {"n": args.el, "op": "click", **common})
            what = f"clicked element {args.el} at {tuple(r['clicked'])}"
        else:
            if args.x is None or args.y is None:
                raise Failure("click needs X Y, or --el N from the latest listing")
            r = call("input", {"op": "click", "x": args.x, "y": args.y, **common})
            what = f"clicked ({args.x},{args.y})"
    elif cmd == "move":
        r = call("input", {"op": "move", "x": args.x, "y": args.y, **shot})
        what = f"pointer at ({args.x},{args.y})"
    elif cmd == "drag":
        r = call("input", {"op": "drag", "x": args.x1, "y": args.y1, "x2": args.x2, "y2": args.y2,
                           "button": args.button, "mods": _mods(args.mods), **shot})
        what = f"dragged ({args.x1},{args.y1}) -> ({args.x2},{args.y2})"
    elif cmd == "scroll":
        r = call("input", {"op": "scroll", "x": args.x, "y": args.y, "dy": args.dy,
                           "dx": args.dx, **shot})
        what = f"scrolled dy={args.dy} dx={args.dx} at ({args.x},{args.y})"
    elif cmd == "type":
        r = call("input", {"op": "paste" if args.paste else "type", "text": args.text, **shot})
        what = f"{'pasted' if args.paste else 'typed'} {len(args.text)} character(s)"
    elif cmd == "key":
        op = "keydown" if args.hold else "keyup" if args.release else "key"
        r = call("input", {"op": op, "keys": args.chords, **shot})
        what = f"{op} {' '.join(args.chords)}"
    elif cmd in ("act", "set-text", "read", "focus"):
        op = {"act": "action", "set-text": "set_text", "read": "read", "focus": "focus"}[cmd]
        payload = {"n": args.n, "op": op, **shot}
        if cmd == "act":
            payload["name"] = args.action
        if cmd == "set-text":
            payload["text"] = args.text
        if cmd == "read":
            payload["shot"] = False
        r = call("element", payload)
        if cmd == "read":
            return r.get("text", "")
        what = f"{cmd} element {args.n}" + (f" ({r['action']})" if r.get("action") else "")
    elif cmd == "window":
        r = call("window", {"op": args.op, "id": args.id, "title": args.title, "x": args.x,
                            "y": args.y, "w": args.w, "h": args.h, **shot})
        what = f"window {args.op} {r.get('id')}"
    elif cmd == "workspace":
        r = call("window", {"op": "workspace", "index": args.index, **shot})
        what = f"switched to workspace {args.index}"
    elif cmd == "launch":
        r = call("launch", {"argv": args.argv, **shot})
        opened = (f", window {json.dumps(r['window'], ensure_ascii=False)}" if r.get("window")
                  else ", no new window within 10 s")
        what = f"launched {args.argv[0]} (pid {r['pid']}{opened})"
    else:
        raise Failure(f"unknown command {cmd}")
    after = report_after(r, None if args.no_shot else (args.out or VIEW))
    return f"ok — {what}" + (f"; {after}" if after else "")


# ---- cli -----------------------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:  # noqa: PLR0915 — one flat CLI, declared in one place
    ap = argparse.ArgumentParser(prog="gu desktop", description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    def acting(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--out", help=f"where the after-screenshot goes (default {VIEW})")
        p.add_argument("--no-shot", action="store_true", help="skip the after-screenshot")
        p.add_argument("--settle", type=float, default=0.4, help="seconds to wait before it")
        return p

    def scoped(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--scope", default="active",
                       help="active (focused window + popups + panel), all, or an app name")
        return p

    sub.add_parser("status")
    sub.add_parser("stop")
    p = sub.add_parser("mount")
    p.add_argument("folder")
    p.add_argument("--rw", action="store_true", help="let desktop apps write it")
    p.add_argument("--name", help="the folder's name under ~/mnt (default: its own)")
    sub.add_parser("unmount").add_argument("name")
    sub.add_parser("mounts")
    p = scoped(sub.add_parser("shot"))
    p.add_argument("--marks", action="store_true", help="box and number every listed element")
    p.add_argument("--grid", type=int, nargs="?", const=100, help="ruler every STEP px")
    p.add_argument("--all", action="store_true", help="also list labels and other text")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--out")
    p = sub.add_parser("zoom")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--radius", type=int, default=100)
    p.add_argument("--factor", type=int, default=3)
    p.add_argument("--out")
    p = scoped(sub.add_parser("tree"))
    p.add_argument("--all", action="store_true")
    p.add_argument("--json", action="store_true")
    p = scoped(sub.add_parser("find"))
    p.add_argument("text")
    p.add_argument("--role")
    p.add_argument("--json", action="store_true")
    p = acting(sub.add_parser("click"))
    p.add_argument("x", type=int, nargs="?")
    p.add_argument("y", type=int, nargs="?")
    p.add_argument("--el", type=int, help="an element number from the latest listing")
    p.add_argument("--button", default="left", choices=("left", "middle", "right"))
    p.add_argument("--double", action="store_true")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--mods", help="modifiers held during the click, e.g. ctrl,shift")
    p = acting(sub.add_parser("move"))
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p = acting(sub.add_parser("drag"))
    for name in ("x1", "y1", "x2", "y2"):
        p.add_argument(name, type=int)
    p.add_argument("--button", default="left", choices=("left", "middle", "right"))
    p.add_argument("--mods")
    p = acting(sub.add_parser("scroll"))
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--dy", type=int, default=3, help="wheel clicks; positive scrolls DOWN")
    p.add_argument("--dx", type=int, default=0, help="wheel clicks; positive scrolls RIGHT")
    p = acting(sub.add_parser("type"))
    p.add_argument("text")
    p.add_argument("--paste", action="store_true", help="via the clipboard (any script/emoji)")
    p = acting(sub.add_parser("key"))
    p.add_argument("chords", nargs="+", help="e.g. ctrl+s, Return, alt+F4, ctrl+shift+t")
    hold = p.add_mutually_exclusive_group()
    hold.add_argument("--hold", action="store_true", help="press and keep held")
    hold.add_argument("--release", action="store_true", help="release a held key")
    for name in ("act", "read", "focus"):
        p = acting(sub.add_parser(name))
        p.add_argument("n", type=int)
        if name == "act":
            p.add_argument("--action", help="which of its actions (default: its first)")
    p = acting(sub.add_parser("set-text"))
    p.add_argument("n", type=int)
    p.add_argument("text")
    sub.add_parser("windows").add_argument("--json", action="store_true")
    p = acting(sub.add_parser("window"))
    p.add_argument("op", choices=("focus", "close", "minimize", "maximize", "unmaximize",
                                  "fullscreen", "move", "resize", "geometry"))
    p.add_argument("--id")
    p.add_argument("--title")
    for name in ("x", "y", "w", "h"):
        p.add_argument(f"--{name}", type=int)
    p = acting(sub.add_parser("workspace"))
    p.add_argument("index", type=int)
    p = sub.add_parser("clipboard")
    p.add_argument("--set")
    p = acting(sub.add_parser("launch"))
    p.add_argument("argv", nargs=argparse.REMAINDER)
    p = sub.add_parser("put")
    p.add_argument("local")
    p.add_argument("remote")
    p = sub.add_parser("get")
    p.add_argument("remote")
    p.add_argument("local")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.cmd:
        parser().print_help()
        return 2
    if args.cmd == "launch":
        # REMAINDER keeps a `--` separator; it is the caller's, not part of the command.
        args.argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not args.argv:
            print("launch needs a command", file=sys.stderr)
            return 2
    try:
        print(run(args))
    except Failure as exc:
        print(f"desktop: {exc}", file=sys.stderr)
        return 2
    return 0


# ---- selftest ------------------------------------------------------------------------------

# A 1x1 PNG: enough to prove a screenshot is decoded and written where it was asked to go.
PIXEL = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAA"
         "AABJRU5ErkJggg==")


def selftest() -> int:  # noqa: PLR0915 — one scenario per command, in a row
    import contextlib
    import io
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: list[tuple[str, dict]] = []
    el = {"n": 1, "role": "push button", "name": "Save", "x": 10, "y": 20, "w": 80, "h": 24,
          "actions": ["click"], "focused": True}
    after = {"png": PIXEL, "size": [1280, 800]}
    share = {"name": "data", "path": "/x/data", "rw": False, "guest_path": "/home/mark/mnt/data"}
    replies = {
        "vm_status": {"name": "routines--t", "slots": 2, "idle_limit_s": 1200,
                      "fleet": [{"name": "routines--t"}],
                      "vm": {"ready": True, "up_s": 300, "idle_s": 60, "shares": ["data"]}},
        "vm_stop": {"stopped": True},
        "mount": {"ok": True, **share},
        "mounts": {"shares": [share]},
        "unmount": {"shares": []},
        "shot": {"png": PIXEL, "size": [1280, 800], "elements": [el], "truncated": False},
        "zoom": {"png": PIXEL, "box": [540, 300, 740, 500], "factor": 3},
        "tree": {"elements": [el], "truncated": True},
        "find": {"elements": [], "truncated": False},
        "input": {"ok": True, "after": after, "active": "Mousepad"},
        "element": {"ok": True, "clicked": [50, 32], "after": after, "active": "Mousepad",
                    "text": "hello"},
        "windows": {"windows": [{"id": "0x01", "desktop": 0, "pid": 1, "x": 0, "y": 0,
                                 "w": 640, "h": 480, "title": "Mousepad", "active": True}]},
        "file_get": {"b64": base64.b64encode(b"data").decode(), "path": "/home/mark/a.txt"},
        "file_put": {"ok": True, "path": "/home/mark/b.txt", "size": 4},
        "clipboard": {"text": "clip"},
        "launch": {"ok": True, "pid": 42, "window": "Firefox", "after": after,
                   "active": "Firefox"},
    }

    class Fake(BaseHTTPRequestHandler):
        def log_message(self, *_a: object) -> None:
            pass

        def do_POST(self) -> None:
            op = self.path.strip("/")
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            ok = (self.headers.get("Authorization") == "Bearer t0ken"
                  and self.headers.get("Content-Type") == "application/json"
                  and self.headers.get("Origin") is None)
            # what the broker does: the proof file must be in the workdir, holding the value
            name, _, value = (self.headers.get("X-Desktop-Proof") or "").partition(":")
            proof = Path(self.headers.get("X-Desktop-Workdir", "/nonexistent")) / name
            ok = ok and proof.is_file() and proof.read_text() == value
            folder = self.headers.get("X-Desktop-Folder-Proof")
            if folder:
                body["_folder_proof"] = folder
            seen.append((op, body))
            status, reply = (200, replies.get(op, {"ok": True})) if ok else (401, {"error": "no"})
            if op == "boom":
                status, reply = 400, {"error": "bad input"}
            data = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["DESKTOP_VM_URL"] = f"http://127.0.0.1:{server.server_port}"
    os.environ[TOKEN_VAR] = "t0ken"

    def out(*argv: str) -> tuple[int, str]:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = main(list(argv))
        return code, buf.getvalue()

    home = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        code, text = out("status")
        assert code == 0 and "desktop running (up 5 min, idle 1 min" in text, text
        assert "folders: data" in text and "1/2 desktops in use" in text, text
        assert not list(Path.cwd().glob(PROOF_PREFIX + "*")), "a proof file was left behind"
        Path("folder").mkdir()
        code, text = out("mount", "folder")
        assert code == 2 and "is empty" in text, text
        (Path("folder") / "f.txt").write_text("x")
        code, text = out("mount", "folder")
        assert code == 0 and "read-only" in text, text
        assert seen[-1][1]["digest"] == listing_digest(Path("folder").resolve()), seen[-1]
        code, text = out("mount", "folder", "--rw", "--name", "work")
        assert seen[-1][1]["rw"] is True and seen[-1][1]["_folder_proof"], seen[-1]
        assert not list(Path("folder").glob(PROOF_PREFIX + "*")), "folder proof left behind"
        code, text = out("mounts")
        assert "data: /x/data -> /home/mark/mnt/data (ro)" in text, text
        code, text = out("unmount", "data")
        assert "unmounted data" in text and "(no folders mounted)" in text, text
        code, text = out("stop")
        assert text.strip() == "desktop stopped", text
        code, text = out("shot", "--marks")
        assert code == 0 and '[1] push button "Save" @10,20 80x24 [focused] actions: click' in text
        assert Path(VIEW).read_bytes().startswith(b"\x89PNG"), "the screenshot was not written"
        assert seen[-1] == ("shot", {"marks": True, "grid": 0, "scope": "active", "all": False,
                                     "scale": 1.0}), seen[-1]
        code, text = out("zoom", "640", "400")
        assert code == 0 and "(540,300)-(740,500) at 3x" in text and Path(ZOOM_VIEW).is_file()
        code, text = out("tree")
        assert "PARTIAL" in text, text
        code, text = out("find", "Nope")
        assert "no elements" in text, text
        code, text = out("click", "--el", "1", "--double", "--mods", "ctrl")
        assert code == 0 and "clicked element 1 at (50, 32)" in text and "view:" in text, text
        assert seen[-1][1]["count"] == 2 and seen[-1][1]["mods"] == ["ctrl"], seen[-1]
        code, text = out("click", "5", "6", "--no-shot")
        assert code == 0 and "view:" not in text and seen[-1][1]["shot"] is False, text
        code, text = out("click")
        assert code == 2 and "needs X Y" in text, text
        code, text = out("drag", "1", "2", "3", "4")
        assert seen[-1][1] | {"op": "drag", "x": 1, "y": 2, "x2": 3, "y2": 4} == seen[-1][1]
        code, text = out("key", "ctrl+s", "Return")
        assert seen[-1][1]["keys"] == ["ctrl+s", "Return"] and seen[-1][1]["op"] == "key"
        code, text = out("key", "shift", "--hold")
        assert seen[-1][1]["op"] == "keydown"
        code, text = out("type", "héllo", "--paste")
        assert seen[-1][1]["op"] == "paste" and "pasted 5" in text, text
        code, text = out("read", "1")
        assert text.strip() == "hello" and seen[-1][1]["shot"] is False, text
        code, text = out("act", "1", "--action", "click")
        assert seen[-1][1] | {"op": "action", "name": "click"} == seen[-1][1]
        code, text = out("windows")
        assert "* 0x01 ws0" in text, text
        code, text = out("window", "maximize", "--title", "Mouse")
        assert seen[-1][1]["op"] == "maximize" and seen[-1][1]["title"] == "Mouse"
        code, text = out("launch", "--", "firefox-esr", "--new-window")
        assert seen[-1][1]["argv"] == ["firefox-esr", "--new-window"], seen[-1]
        assert '(pid 42, window "Firefox")' in text, text
        Path("in.txt").write_text("data")
        code, text = out("put", "in.txt", "b.txt")
        assert code == 0 and base64.b64decode(seen[-1][1]["b64"]) == b"data", text
        code, text = out("get", "a.txt", "sub/a.txt")
        assert Path("sub/a.txt").read_bytes() == b"data", text
        code, text = out("clipboard")
        assert text.strip() == "clip"
        os.environ[TOKEN_VAR] = "wrong"
        code, text = out("windows")
        assert code == 2 and "refused the token" in text, text
        del os.environ[TOKEN_VAR]
        code, text = out("windows")
        assert code == 2 and f"{TOKEN_VAR} is not set" in text, text
        os.environ[TOKEN_VAR] = "t0ken"
        os.environ["RSCHED_UTIL_TIMEOUT_S"] = "12"
        assert _timeout() == 7.0
        del os.environ["RSCHED_UTIL_TIMEOUT_S"]
        server.shutdown()
        server.server_close()
        os.environ["DESKTOP_VM_URL"] = f"http://127.0.0.1:{server.server_port}"
        code, text = out("windows")
        assert code == 2 and "not reachable" in text, text
        os.chdir(home)
    print("selftest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
