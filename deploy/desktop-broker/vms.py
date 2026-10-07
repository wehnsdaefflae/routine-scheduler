"""The fleet: one VM per routine, booted on its first call, stopped when idle.

RAM is the limit, so the fleet has a fixed number of SLOTS (DESKTOP_MAX_VMS). A slot is a tap
device the entrypoint made at boot (`vmtap<i>`, the guest's /30 link) — the broker runs
unprivileged and could not make one later. A routine asking for a desktop when every slot is
held is told which desktops hold them and how long each has been idle, and gets on with work
that needs no screen; a run is never blocked waiting for a slot.

Each VM's SYSTEM disk is a qcow2 overlay on the image's read-only base — instant to create, and
thrown away when the VM stops, so every boot is the image's system. Its HOME disk is the
routine's own and persists (`homes/<name>.img`). Everything a VM differs in — address, gateway,
zone, name — is on its kernel command line; the guest applies it before its network comes up.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

IMAGES = Path("/opt/desktop")
AGENT_PORT = 8790
BOOT_TIMEOUT_S = 120
STOP_GRACE_S = 25


@dataclass
class Settings:
    state: Path
    max_vms: int = 2
    cpus: int = 2
    mem_mb: int = 3072
    idle_s: int = 20 * 60
    home_gb: int = 16
    root_gb: int = 12
    tz: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        def num(name: str, default: int) -> int:
            raw = os.environ.get(name, "")
            return int(raw) if raw.isdigit() and int(raw) > 0 else default
        tz = ""
        try:
            tz = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        return cls(state=Path(os.environ.get("DESKTOP_STATE", "/home/mark/desktop-vm")),
                   max_vms=num("DESKTOP_MAX_VMS", 2), cpus=num("DESKTOP_CPUS", 2),
                   mem_mb=num("DESKTOP_MEM_MB", 3072),
                   idle_s=num("DESKTOP_IDLE_MIN", 20) * 60,
                   home_gb=num("DESKTOP_HOME_GB", 16), root_gb=num("DESKTOP_ROOT_GB", 12), tz=tz)


def slot_net(slot: int) -> tuple[str, str]:
    """(guest address, gateway) of a slot's /30: slot 0 is .2 behind .1, slot 1 .6 behind .5."""
    return f"192.168.249.{4 * slot + 2}", f"192.168.249.{4 * slot + 1}"


def cmdline(name: str, slot: int, tz: str) -> str:
    guest, gw = slot_net(slot)
    parts = ["console=ttyS0", "root=/dev/vda", "rw", "net.ifnames=0", "quiet",
             f"rsched.ip={guest}/30", f"rsched.gw={gw}", f"rsched.name={name}"]
    if tz:
        parts.append(f"rsched.tz={tz}")
    return " ".join(parts)


def vnc_tokens(vms: dict[str, VM]) -> str:
    """Websockify's token file: each desktop's screen is reached by its own RANDOM token, never
    its name — a name is guessable, and the screen is a keyboard on that routine's desktop.
    """
    return "".join(f"{vm.vnc}: {slot_net(vm.slot)[0]}:5900\n"
                   for _, vm in sorted(vms.items()))


@dataclass
class VM:
    name: str
    slot: int
    dir: Path
    proc: subprocess.Popen | None = None
    started: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    ready: threading.Event = field(default_factory=threading.Event)
    failed: str = ""
    stopping: bool = False
    vnc: str = field(default_factory=lambda: secrets.token_hex(16))
    lock: threading.Lock = field(default_factory=threading.Lock)
    shares: dict = field(default_factory=dict)

    @property
    def api(self) -> Path:
        return self.dir / "api.sock"

    @property
    def guest(self) -> str:
        return slot_net(self.slot)[0]

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def serial_tail(self, lines: int = 15) -> str:
        """The end of the VMM's own log and of the guest's console — what a failed boot says."""
        out = []
        for log in ("vmm.log", "serial.log"):
            try:
                text = (self.dir / log).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            out += [f"[{log}] {line}" for line in text.splitlines()[-lines:]]
        return "\n".join(out)


class BusyError(RuntimeError):
    """Every slot is held — the message names by whom and for how long."""


class Fleet:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.vms: dict[str, VM] = {}
        self.lock = threading.Lock()
        for sub in ("homes", "run", "logs"):
            (self.s.state / sub).mkdir(parents=True, exist_ok=True)
        # No VM outlives the broker, so a run dir found at start is a container that was killed
        # rather than stopped: its overlay and sockets belong to nothing.
        for stale in (self.s.state / "run").iterdir():
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)

    # -- lifecycle ---------------------------------------------------------------------------
    def ensure(self, name: str) -> VM:
        """The routine's running VM, booting it first if needed (the call waits for it)."""
        with self.lock:
            vm = self.vms.get(name)
            if vm is not None and vm.stopping:
                raise BusyError("this routine's desktop is shutting down; try again in half a "
                           "minute")
            if vm is not None and not vm.alive() and vm.ready.is_set():
                vm.stopping = True
                self._power_off(vm, "it had exited")       # already dead: nothing to wait for
                self._release(vm)
                vm = None
            if vm is None:
                vm = VM(name=name, slot=self._free_slot(), dir=self.s.state / "run" / name)
                self.vms[name] = vm
                threading.Thread(target=self._boot, args=(vm,), daemon=True).start()
            vm.last_used = time.time()
        if not vm.ready.wait(BOOT_TIMEOUT_S) or vm.failed:
            reason = vm.failed or f"no answer within {BOOT_TIMEOUT_S}s"
            tail = vm.serial_tail()
            self._discard(vm, reason)
            raise RuntimeError(f"the desktop did not start: {reason}\n{tail}")
        return vm

    def _free_slot(self) -> int:
        held = {vm.slot for vm in self.vms.values()}
        for slot in range(self.s.max_vms):
            if slot not in held:
                return slot
        now = time.time()
        who = ", ".join(f"{vm.name} (idle {int(now - vm.last_used) // 60} min)"
                        for vm in sorted(self.vms.values(), key=lambda v: v.last_used))
        raise BusyError(f"all {self.s.max_vms} desktops are in use: {who}. A desktop is stopped "
                   f"after {self.s.idle_s // 60} min idle; carry on with work that needs no "
                   "screen and try again later.")

    def _boot(self, vm: VM) -> None:
        try:
            if vm.dir.exists():
                shutil.rmtree(vm.dir)
            vm.dir.mkdir(parents=True)
            root = vm.dir / "root.qcow2"
            subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", "-F", "raw", "-b",
                            str(IMAGES / "root.img"), str(root), f"{self.s.root_gb}G"],
                           check=True, capture_output=True, timeout=60)
            home = self.s.state / "homes" / f"{vm.name}.img"
            if not home.exists():
                with home.open("wb") as fh:
                    fh.truncate(self.s.home_gb * 2**30)
                subprocess.run(["mkfs.ext4", "-q", "-L", "desktop-home", "-E",
                                f"root_owner={os.getuid()}:{os.getgid()}", str(home)],
                               check=True, capture_output=True, timeout=120)
            log = (vm.dir / "vmm.log").open("ab")
            vm.proc = subprocess.Popen([
                "cloud-hypervisor", "--api-socket", f"path={vm.api}",
                "--kernel", str(IMAGES / "vmlinuz"), "--initramfs", str(IMAGES / "initrd.img"),
                "--cmdline", cmdline(vm.name, vm.slot, self.s.tz),
                "--disk", f"path={root},image_type=qcow2,backing_files=on",
                f"path={home},image_type=raw",
                "--cpus", f"boot={self.s.cpus}",
                # shared: virtio-fs folders need guest memory the file daemons can map. (It
                # rules out `mergeable` — Cloud Hypervisor refuses KSM on shared memory.)
                "--memory", f"size={self.s.mem_mb}M,shared=on",
                "--net", f"tap=vmtap{vm.slot},mac=52:54:00:d5:00:{vm.slot + 2:02x}",
                "--rng", "src=/dev/urandom",
                "--serial", f"file={vm.dir / 'serial.log'}", "--console", "off",
            ], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            self._write_tokens()
            log_line(f"{vm.name}: booting in slot {vm.slot} ({vm.guest})")
            deadline = time.monotonic() + BOOT_TIMEOUT_S - 5
            while time.monotonic() < deadline:
                if not vm.alive():
                    vm.failed = "the VMM exited during boot"
                    break
                try:
                    agent_call(vm, "health", {}, timeout=3)
                    log_line(f"{vm.name}: ready")
                    break
                except (OSError, RuntimeError):
                    time.sleep(1)
            else:
                vm.failed = "the desktop agent never answered"
        except (OSError, subprocess.SubprocessError) as exc:
            vm.failed = f"{type(exc).__name__}: {exc}"
        finally:
            vm.ready.set()

    def stop(self, name: str, why: str) -> bool:
        with self.lock:
            vm = self.vms.get(name)
        if vm is None:
            return False
        self._discard(vm, why)
        return True

    def _discard(self, vm: VM, why: str) -> None:
        """Power the VM off and give its slot back. The slow part runs WITHOUT the fleet lock —
        a 25-second shutdown must not stall every other routine's calls — while the VM stays
        listed as `stopping`, because its tap is still open until the VMM has exited.
        """
        with self.lock:
            if vm.stopping:
                return                  # another caller is already powering it off
            vm.stopping = True
        self._power_off(vm, why)
        with self.lock:
            self._release(vm)

    def _power_off(self, vm: VM, why: str) -> None:
        """Button first, kill after the grace; end the shares; keep the serial log."""
        from shares import end_all
        log_line(f"{vm.name}: stopping ({why})")
        if vm.alive():
            subprocess.run(["ch-remote", "--api-socket", str(vm.api), "power-button"],
                           capture_output=True, timeout=10, check=False)
            try:
                vm.proc.wait(STOP_GRACE_S)  # type: ignore[union-attr]
            except subprocess.TimeoutExpired:
                vm.proc.kill()  # type: ignore[union-attr]
                vm.proc.wait(5)  # type: ignore[union-attr]
        end_all(vm)
        for log in ("serial.log", "vmm.log"):
            if (vm.dir / log).exists():
                shutil.copyfile(vm.dir / log, self.s.state / "logs" / f"{vm.name}.{log}")
        shutil.rmtree(vm.dir, ignore_errors=True)

    def _release(self, vm: VM) -> None:
        """Caller holds the fleet lock."""
        if self.vms.get(vm.name) is vm:
            del self.vms[vm.name]
        self._write_tokens()

    def reap(self) -> None:
        """Stop every desktop idle past the limit. Runs on the broker's timer."""
        now = time.time()
        with self.lock:
            idle = [vm for vm in self.vms.values()
                    if vm.ready.is_set() and not vm.stopping
                    and now - vm.last_used > self.s.idle_s and not vm.lock.locked()]
        for vm in idle:
            self._discard(vm, f"idle for {int(now - vm.last_used) // 60} min")

    def stop_all(self) -> None:
        with self.lock:
            threads = [threading.Thread(target=self._discard, args=(vm, "shutting down"))
                       for vm in list(self.vms.values())]
        for t in threads:
            t.start()
        for t in threads:
            t.join(STOP_GRACE_S + 10)

    def status(self, *, operator: bool = False) -> list[dict]:
        """One row per desktop. The VNC token rides only on the OPERATOR's view: with it, the
        console's relay can open that screen; a routine's own status never carries one.
        """
        now = time.time()
        with self.lock:
            rows = []
            for vm in self.vms.values():
                row = {"name": vm.name, "slot": vm.slot,
                       "ready": vm.ready.is_set() and not vm.failed and not vm.stopping,
                       "stopping": vm.stopping, "up_s": int(now - vm.started),
                       "idle_s": int(now - vm.last_used), "shares": sorted(vm.shares)}
                if operator:
                    row["vnc"] = vm.vnc
                    row["folders"] = [s.describe() for s in vm.shares.values()]
                rows.append(row)
            return rows

    def _write_tokens(self) -> None:
        path = self.s.state / "run" / "vnc-tokens"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(vnc_tokens(self.vms), encoding="utf-8")
        tmp.replace(path)


def agent_call(vm: VM, op: str, payload: dict | bytes, *, timeout: float = 120) -> dict:
    """POST one operation to the guest's agent; a refusal comes back as RuntimeError text."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    req = urllib.request.Request(f"http://{vm.guest}:{AGENT_PORT}/{op}", data=body,
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — guest link
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("error", detail)
        except ValueError:
            pass
        raise AgentRefusedError(exc.code, detail) from None


class AgentRefusedError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


def log_line(msg: str) -> None:
    print(f"[broker] {msg}", flush=True)
