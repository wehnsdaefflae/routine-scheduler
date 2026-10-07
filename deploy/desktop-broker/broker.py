#!/usr/bin/env python3
"""The desktop broker — one door, one desktop per routine behind it.

The bearer-token proxy in front of it has already established that the caller holds
DESKTOP_VM_TOKEN; every holder holds the same one, so this establishes WHICH routine is calling
(`proof.identity` + the one-time file the util wrote into its own directory) and routes the
request to that routine's VM, booting it first when it is not running (`vms.Fleet`). The guest's
own operations pass through unchanged; the broker answers four of its own:

  vm_status  this routine's desktop and the fleet's slots      (never boots one)
  vm_stop    power this routine's desktop off now
  mount      share a folder into it live (`shares`), proved by the caller's sandbox
  unmount / mounts

Two more are the OPERATOR's — the console's view of every desktop, which a routine must never
have: `fleet` (every desktop, with the random token that opens its screen) and `fleet_stop`.
They need `X-Desktop-Operator: $DESKTOP_OPERATOR_TOKEN`, a second secret no routine is granted;
the same secret guards the noVNC port, so holding the routines' token reaches no screen.

It listens on loopback only; the proxy is the way in.
"""
from __future__ import annotations

import hmac
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import proof
import shares
import vms

LISTEN = ("127.0.0.1", int(os.environ.get("DESKTOP_BROKER_PORT", "8789")))
HOME = Path(os.environ.get("DESKTOP_IDENTITY_HOME", "/home/mark"))
BODY_MAX = 40 * 1024 * 1024
REAP_EVERY_S = 30
#: The guest agent's operations a caller may reach. `mount_share` / `unmount_share` are not
#: among them: a guest mount is only ever made by `shares`, after the folder's proof held.
FORWARD = frozenset({"health", "shot", "zoom", "tree", "find", "input", "element", "windows",
                     "window", "clipboard", "launch", "file_get", "file_put"})


def share_roots() -> list[Path]:
    raw = os.environ.get("DESKTOP_SHARE_ROOTS", "")
    return [Path(p) for p in raw.split(":") if p.strip()]


def identity_homes() -> list[Path]:
    return [HOME / label for label in proof.IDENTITY_HOMES]


class Handler(BaseHTTPRequestHandler):
    fleet: vms.Fleet
    server_version = "desktop-broker/1"

    def log_message(self, fmt: str, *args: object) -> None:
        pass                                         # every outcome worth keeping is logged below

    def _reply(self, status: int, payload: dict | bytes) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._reply(405, {"error": "every operation is a POST with a JSON body"})

    def do_POST(self) -> None:
        op = self.path.strip("/").replace("-", "_")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > BODY_MAX:
                raise shares.ShareError("request body too large")
            raw = self.rfile.read(length) or b"{}"
            if op in ("fleet", "fleet_stop"):
                self._reply(*self.operator(op, raw))
                return
            workdir = self.headers.get("X-Desktop-Workdir", "")
            name = proof.identity(workdir, HOME)
            proof.check_file(Path(workdir).resolve(), self.headers.get("X-Desktop-Proof", ""))
            status, reply = self.route(op, name, raw)
            self._reply(status, reply)
        except proof.ProofError as exc:
            self._reply(403, {"error": str(exc)})
        except vms.BusyError as exc:
            self._reply(503, {"error": str(exc), "busy": True})
        except vms.AgentRefusedError as exc:
            self._reply(exc.status, {"error": str(exc)})
        except (shares.ShareError, ValueError, KeyError, TypeError) as exc:
            self._reply(400, {"error": str(exc)})
        except Exception as exc:
            vms.log_line(f"/{op} failed: {type(exc).__name__}: {exc}")
            self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})

    def route(self, op: str, name: str, raw: bytes) -> tuple[int, dict | bytes]:
        fleet = self.fleet
        if op == "vm_status":
            fleet_rows = fleet.status()
            mine = next((r for r in fleet_rows if r["name"] == name), None)
            return 200, {"name": name, "vm": mine, "fleet": fleet_rows,
                         "slots": fleet.s.max_vms, "idle_limit_s": fleet.s.idle_s}
        if op == "vm_stop":
            return 200, {"stopped": fleet.stop(name, "asked by its routine")}
        if op in FORWARD:
            vm = fleet.ensure(name)
            with vm.lock:
                vm.last_used = time.time()
                result = vms.agent_call(vm, op, raw, timeout=170)
                vm.last_used = time.time()
            return 200, json.dumps(result).encode()
        req = json.loads(raw)
        if not isinstance(req, dict):
            raise TypeError("the body must be a JSON object")
        if op == "mount":
            return 200, self.mount(name, req)
        if op in ("unmount", "mounts"):
            vm = fleet.vms.get(name)
            if vm is None or vm.stopping:
                return 200, {"shares": []} if op == "mounts" else {"ok": True}
            with vm.lock:
                if op == "unmount":
                    shares.unmount(vm, str(req.get("name", "")))
                return 200, {"shares": [s.describe() for s in vm.shares.values()]}
        raise ValueError(f"no such operation: /{op}")

    def operator(self, op: str, raw: bytes) -> tuple[int, dict]:
        expected = os.environ.get("DESKTOP_OPERATOR_TOKEN", "")
        given = self.headers.get("X-Desktop-Operator", "")
        if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
            raise proof.ProofError("the fleet is the operator's view — it needs "
                                   "X-Desktop-Operator: $DESKTOP_OPERATOR_TOKEN")
        if op == "fleet":
            return 200, {"slots": self.fleet.s.max_vms, "idle_limit_s": self.fleet.s.idle_s,
                         "desktops": self.fleet.status(operator=True)}
        name = str(json.loads(raw).get("name", ""))
        return 200, {"stopped": self.fleet.stop(name, "stopped by the operator")}

    def mount(self, name: str, req: dict) -> dict:
        try:
            path = Path(str(req["path"])).resolve(strict=True)
        except (KeyError, OSError, RuntimeError):
            raise shares.ShareError(f"no folder {req.get('path')!r} on the desktop host"
                                    ) from None
        rw = bool(req.get("rw"))
        shares.check_shareable(path, rw, share_roots(), identity_homes())
        if rw:
            proof.check_file(path, self.headers.get("X-Desktop-Folder-Proof", ""))
        else:
            proof.check_digest(path, str(req.get("digest", "")))
        vm = self.fleet.ensure(name)
        with vm.lock:
            share = shares.mount(vm, path, rw=rw, wanted=req.get("name"))
            vm.last_used = time.time()
        vms.log_line(f"{name}: shared {path} as ~/mnt/{share.name} ({'rw' if rw else 'ro'})")
        return {"ok": True, **share.describe()}


def main() -> int:
    fleet = vms.Fleet(vms.Settings.from_env())
    Handler.fleet = fleet
    server = ThreadingHTTPServer(LISTEN, Handler)
    server.daemon_threads = True

    def reaper() -> None:
        while True:
            time.sleep(REAP_EVERY_S)
            try:
                fleet.reap()
            except Exception as exc:
                vms.log_line(f"reaper: {type(exc).__name__}: {exc}")

    def shutdown(_sig: int, _frame: object) -> None:
        vms.log_line("stopping every desktop (container stop)")
        fleet.stop_all()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    threading.Thread(target=reaper, daemon=True).start()
    vms.log_line(f"listening on {LISTEN[0]}:{LISTEN[1]} — {fleet.s.max_vms} slot(s), "
                 f"{fleet.s.mem_mb} MiB each, stopped after {fleet.s.idle_s // 60} min idle")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
