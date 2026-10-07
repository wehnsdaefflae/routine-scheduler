#!/usr/bin/env python3
"""The desktop agent — one small HTTP door through which the `desktop` util drives this guest.

It runs INSIDE the VM, in the X session (it needs the session's DISPLAY and D-Bus to reach the
accessibility bus), and listens on the guest's private link only. Nothing outside the sidecar
container can route to that link, and the container fronts it with the same bearer-token proxy
the browser sidecar uses — so this process carries no credential of its own.

It does have to defend against ONE neighbour: a web page open in this desktop's own browser can
fetch the guest's addresses, and a "simple" cross-origin POST needs no preflight. Every request
therefore has to be `Content-Type: application/json` (which a page can send only after a CORS
preflight nobody here answers) and must carry no `Origin` header — the util sends neither
problem, a browser always sends one of them.

Single-threaded on purpose: AT-SPI is a D-Bus client that does not want two walks interleaved,
and the util is one caller taking one turn at a time. The element numbers a listing hands out
stay valid until the next listing replaces them.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import a11y
import screen
import xctl

LISTEN = (os.environ.get("DESKTOP_AGENT_BIND", "0.0.0.0"),  # noqa: S104 — the guest's private link
          int(os.environ.get("DESKTOP_AGENT_PORT", "8790")))
HOME = Path(os.environ.get("HOME", "/home/mark")).resolve()
BODY_MAX = 40 * 1024 * 1024
FILE_MAX = 25 * 1024 * 1024
SETTLE_S = 0.4
#: How long `launch` waits for the program's first window.
LAUNCH_WAIT_S = 10.0
#: Environment every launched application gets, so it shows up in the accessibility tree.
A11Y_ENV = {"GTK_MODULES": "gail:atk-bridge", "NO_AT_BRIDGE": "0", "GNOME_ACCESSIBILITY": "1",
            "QT_ACCESSIBILITY": "1", "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1",
            "ACCESSIBILITY_ENABLED": "1"}


class RefusedError(ValueError):
    """A request this door turns away (400), with the reason the caller is shown."""


class Desktop:
    """The agent's whole state: the latest element listing and the AT-SPI refs behind it."""

    def __init__(self) -> None:
        self.display = os.environ.get("DISPLAY", ":1")
        self.elements: list[dict] = []
        self.refs: dict[int, object] = {}

    # -- looking ---------------------------------------------------------------------------
    def listing(self, req: dict) -> tuple[list[dict], bool]:
        elements, refs, truncated = a11y.walk(
            scope=str(req.get("scope") or "active"), active=xctl.active_window(),
            screen=xctl.screen_size(), everything=bool(req.get("all")))
        self.elements, self.refs = elements, refs
        return elements, truncated

    def op_health(self, _req: dict) -> dict:
        active = xctl.active_window()
        return {"ok": True, "screen": list(xctl.screen_size()), "display": self.display,
                "active": active and active["title"], "windows": len(xctl.windows())}

    def op_shot(self, req: dict) -> dict:
        img = screen.capture(self.display)
        out: dict = {"size": list(img.size)}
        if req.get("marks"):
            elements, out["truncated"] = self.listing(req)
            screen.draw_marks(img, elements)
            out["elements"] = elements
        if req.get("grid"):
            screen.draw_grid(img, max(20, int(req["grid"])))
        out["png"] = screen.png_b64(img, float(req.get("scale") or 1.0))
        return out

    def op_zoom(self, req: dict) -> dict:
        img = screen.capture(self.display)
        big, box = screen.zoom(img, int(req["x"]), int(req["y"]),
                               int(req.get("radius") or 100), int(req.get("factor") or 3))
        return {"png": screen.png_b64(big), "box": list(box),
                "factor": big.width // (box[2] - box[0])}

    def op_tree(self, req: dict) -> dict:
        elements, truncated = self.listing(req)
        return {"elements": elements, "truncated": truncated}

    def op_find(self, req: dict) -> dict:
        text = str(req.get("text") or "")
        if not text:
            raise RefusedError("find needs text to look for")
        elements, truncated = self.listing({**req, "all": True})
        hits = [e for e in elements if a11y.matches(e, text, req.get("role"))]
        return {"elements": hits, "truncated": truncated}

    # -- acting ----------------------------------------------------------------------------
    def op_input(self, req: dict) -> dict:
        op = req.get("op")
        if op == "click":
            xctl.run(xctl.click_argv(req.get("x"), req.get("y"), button=req.get("button", "left"),
                                     count=int(req.get("count") or 1), mods=req.get("mods")))
        elif op == "move":
            xctl.run(xctl.move_argv(req.get("x"), req.get("y")))
        elif op == "drag":
            xctl.run_steps(xctl.drag_argvs(req.get("x"), req.get("y"), req.get("x2"),
                                           req.get("y2"), button=req.get("button", "left"),
                                           mods=req.get("mods")))
        elif op == "scroll":
            xctl.run(xctl.scroll_argv(req.get("x"), req.get("y"), dy=int(req.get("dy") or 0),
                                      dx=int(req.get("dx") or 0)))
        elif op == "type":
            xctl.run(xctl.type_argv(req.get("text", "")), timeout=120)
        elif op == "paste":
            xctl.clipboard_set(str(req.get("text", "")))
            xctl.run(xctl.key_argv(["ctrl+v"]))
        elif op in ("key", "keydown", "keyup"):
            xctl.run(xctl.key_argv(list(req.get("keys") or []), op=op))
        elif op in ("mousedown", "mouseup"):
            xctl.run(xctl.button_argv(op, button=req.get("button", "left")))
        else:
            raise RefusedError("input op is click, move, drag, scroll, type, paste, key, keydown, "
                          "keyup, mousedown or mouseup")
        return {"ok": True}

    def _ref(self, n: object) -> object:
        try:
            return self.refs[int(n)]  # type: ignore[call-overload]
        except (KeyError, TypeError, ValueError):
            raise RefusedError(f"no element {n!r} in the latest listing — list again (tree, find, "
                          "or a shot with marks) and use a number from that") from None

    def op_element(self, req: dict) -> dict:
        node, op = self._ref(req.get("n")), req.get("op", "click")
        if op == "click":
            import pyatspi
            ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)  # type: ignore[attr-defined]
            x, y = ext.x + ext.width // 2, ext.y + ext.height // 2
            xctl.run(xctl.click_argv(x, y, button=req.get("button", "left"),
                                     count=int(req.get("count") or 1), mods=req.get("mods")))
            return {"ok": True, "clicked": [x, y]}
        if op == "action":
            return {"ok": True, "action": a11y.do_action(node, req.get("name"))}
        if op == "focus":
            a11y.grab_focus(node)
            return {"ok": True}
        if op == "set_text":
            a11y.set_text(node, str(req.get("text", "")))
            return {"ok": True}
        if op == "read":
            return {"ok": True, "text": a11y.read_text(node)}
        raise RefusedError("element op is click, action, focus, set_text or read")

    def op_windows(self, _req: dict) -> dict:
        return {"windows": xctl.windows()}

    def op_window(self, req: dict) -> dict:
        if req.get("op") == "workspace":
            xctl.run(xctl.workspace_argv(int(req.get("index", 0))))
            return {"ok": True}
        listed = xctl.windows()
        if req.get("id"):
            hit = next((w for w in listed if w["id"] == req["id"]), None)
        elif req.get("title"):
            needle = str(req["title"]).casefold()
            hit = next((w for w in listed if needle in w["title"].casefold()), None)
        else:
            raise RefusedError("name the window: id (from `windows`) or part of its title")
        if hit is None:
            raise RefusedError(f"no such window: {req.get('id') or req.get('title')!r}")
        xctl.run(xctl.window_argv(str(req.get("op")), hit["id"], x=req.get("x"),
                                  y=req.get("y"), w=req.get("w"), h=req.get("h"),
                                  extents=tuple(hit["extents"])))
        return {"ok": True, "id": hit["id"]}

    def op_clipboard(self, req: dict) -> dict:
        if "text" in req:
            xctl.clipboard_set(str(req["text"]))
            return {"ok": True}
        return {"text": xctl.clipboard_get()}

    def op_launch(self, req: dict) -> dict:
        argv = req.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise RefusedError("launch needs argv: a non-empty list of strings")
        before = {w["id"] for w in xctl.windows()}
        log = HOME / ".cache" / "desktop-agent" / "launch.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as sink:
            try:
                proc = subprocess.Popen(argv, cwd=HOME, stdin=subprocess.DEVNULL, stdout=sink,
                                        stderr=sink, start_new_session=True,
                                        env={**os.environ, **A11Y_ENV})
            except FileNotFoundError:
                raise RefusedError(f"no program {argv[0]!r} in the desktop") from None
        # Wait for the program's window, so the reply (and the screenshot after it) shows the
        # application rather than the moment before it drew. A program that opens no window, or
        # reuses one already open, just runs out the wait.
        deadline = time.monotonic() + min(float(req.get("wait", LAUNCH_WAIT_S)), 30.0)
        while time.monotonic() < deadline:
            fresh = [w for w in xctl.windows() if w["id"] not in before]
            if fresh:
                return {"ok": True, "pid": proc.pid, "window": fresh[-1]["title"]}
            if proc.poll() not in (None, 0):
                raise RefusedError(f"{argv[0]} exited with {proc.returncode} — see "
                                   ".cache/desktop-agent/launch.log in the desktop's home")
            time.sleep(0.3)
        return {"ok": True, "pid": proc.pid, "window": None}

    def op_file_get(self, req: dict) -> dict:
        path = confine(req.get("path"))
        if not path.is_file():
            raise RefusedError(f"{path} is not a file")
        if path.stat().st_size > FILE_MAX:
            raise RefusedError(f"{path} is over {FILE_MAX // 2**20} MiB")
        return {"b64": base64.b64encode(path.read_bytes()).decode("ascii"), "path": str(path)}

    def op_file_put(self, req: dict) -> dict:
        path = confine(req.get("path"))
        data = base64.b64decode(str(req.get("b64", "")), validate=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"ok": True, "path": str(path), "size": len(data)}


    # -- shared folders (the broker attaches the device; this mounts it) --------------------
    def op_mount_share(self, req: dict) -> dict:
        tag, name = str(req.get("tag", "")), str(req.get("name", ""))
        xctl.run(["sudo", "-n", "/usr/local/sbin/desktop-mount", "mount", tag, name], timeout=30)
        return {"ok": True, "path": str(HOME / "mnt" / name)}

    def op_unmount_share(self, req: dict) -> dict:
        tag, name = str(req.get("tag", "")), str(req.get("name", ""))
        xctl.run(["sudo", "-n", "/usr/local/sbin/desktop-mount", "unmount", tag, name],
                 timeout=30)
        return {"ok": True}


#: Ops that change the screen: their reply can carry the screenshot taken after they settle.
ACTING = frozenset({"input", "element", "window", "launch"})


def confine(raw: object) -> Path:
    """A path inside the desktop user's home, resolved — `..` and symlinks cannot leave it."""
    if not isinstance(raw, str) or not raw:
        raise RefusedError("path is required")
    path = (HOME / raw).resolve() if not raw.startswith("/") else Path(raw).resolve()
    if path != HOME and HOME not in path.parents:
        raise RefusedError(f"{raw!r} is outside {HOME}; files move in and out of the home only")
    return path


def gate(headers) -> None:
    """Refuse anything a web page inside this desktop could have sent."""
    if headers.get("Origin") is not None:
        raise RefusedError("requests carrying an Origin header are refused (browser-originated)")
    if (headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
        raise RefusedError("Content-Type must be application/json")


class Handler(BaseHTTPRequestHandler):
    desktop = Desktop()
    server_version = "desktop-agent/1"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[desktop-agent] {self.address_string()} {fmt % args}", file=sys.stderr)

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        op = self.path.strip("/").replace("-", "_")
        try:
            gate(self.headers)
            length = int(self.headers.get("Content-Length") or 0)
            if length > BODY_MAX:
                raise RefusedError("request body too large")
            req = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(req, dict):
                raise RefusedError("the body must be a JSON object")
            method = getattr(self.desktop, f"op_{op}", None)
            if method is None:
                raise RefusedError(f"no such operation: /{op}")
            result = method(req)
            if op in ACTING and req.get("shot", True):
                time.sleep(float(req.get("settle", SETTLE_S)))
                result["after"] = self.desktop.op_shot({"scale": req.get("scale")})
                active = xctl.active_window()
                result["active"] = active and active["title"]
            self._reply(200, result)
        except (RefusedError, xctl.InputError, ValueError, KeyError) as exc:
            self._reply(400, {"error": str(exc)})
        except OSError as exc:              # a file op the guest refused: read-only, no space…
            self._reply(400, {"error": f"{exc.strerror or exc}: {exc.filename or ''}".strip()})
        except Exception as exc:
            self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_GET(self) -> None:
        self._reply(405, {"error": "every operation is a POST with a JSON body"})


def main() -> int:
    server = HTTPServer(LISTEN, Handler)
    print(f"[desktop-agent] listening on {LISTEN[0]}:{LISTEN[1]} (DISPLAY "
          f"{Handler.desktop.display})", file=sys.stderr)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
