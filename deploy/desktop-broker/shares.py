"""Folders from this host, mounted live into one routine's desktop.

A share is three things kept in step: a `virtiofsd` serving the folder (read-only when asked —
enforced by the daemon, so not even the guest's root can write through it), the virtio-fs device
Cloud Hypervisor hot-plugs into the RUNNING VM (`ch-remote add-fs`, no reboot), and the mount the
guest makes at ~/mnt/<name>. They end together: on `unmount`, and when the VM stops.

The sidecar sees the shareable homes at the paths the engine sees them (compose mirrors the
engine's binds), so a path a run names is the same file here. Whether the run may name it is
`proof`'s question, answered by the run's own sandbox before anything here runs.

Two refusals are this module's own. A read-write share may not contain a routine's root: the
guest would then hold its routine.yaml writable, which no action may write (mount a subfolder,
such as artifacts/). And only the homes the sidecar mirrors can be shared at all.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from vms import VM, agent_call

NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}")
SOCKET_WAIT_S = 10
SCAN_LIMIT = 500


class ShareError(ValueError):
    """A share request turned down (400), with the reason."""


@dataclass
class Share:
    name: str
    tag: str
    path: Path
    rw: bool
    proc: subprocess.Popen

    def describe(self) -> dict:
        return {"name": self.name, "path": str(self.path), "rw": self.rw,
                "guest_path": f"/home/mark/mnt/{self.name}"}


def mount_name(path: Path, wanted: str | None, taken: set[str]) -> str:
    """The folder's name under ~/mnt: the one asked for, else its basename, made safe and made
    unique among this VM's shares.
    """
    base = (wanted or path.name or "share").lower()
    base = re.sub(r"[^a-z0-9._-]+", "-", base).strip("-.") or "share"
    base = base[:40]
    if not NAME.fullmatch(base):
        base = "share"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def holds_routine_root(path: Path, homes: list[Path]) -> bool:
    """True when `path` is a routine (or conversation) root, a home holding them or any folder
    above one, or a folder whose direct children include a routine root — anything whose share
    would expose a routine.yaml.
    """
    if (path / "routine.yaml").exists() or any(h.resolve().is_relative_to(path) for h in homes):
        return True
    try:
        with os.scandir(path) as entries:
            for i, entry in enumerate(entries):
                if i >= SCAN_LIMIT:
                    return True             # too big to vouch for: refuse rather than guess
                if entry.is_dir(follow_symlinks=False) and (
                        Path(entry.path) / "routine.yaml").exists():
                    return True
    except OSError:
        return True
    return False


def check_shareable(path: Path, rw: bool, shareable: list[Path], homes: list[Path]) -> None:
    if not path.is_dir():
        raise ShareError(f"{path} is not a folder the desktop host can see")
    if not any(path == root.resolve() or path.is_relative_to(root.resolve())
               for root in shareable):
        raise ShareError(f"{path} is outside the folders the desktop can share ("
                         + ", ".join(str(r) for r in shareable) + ")")
    if rw and holds_routine_root(path, homes):
        raise ShareError(f"{path} holds a routine's own root (its routine.yaml); share a "
                         "subfolder such as artifacts/ read-write instead, or share it "
                         "read-only")


def mount(vm: VM, path: Path, *, rw: bool, wanted: str | None) -> Share:
    for share in vm.shares.values():
        if share.path == path:
            if share.rw == rw:
                return share
            raise ShareError(f"{path} is already shared as ~/mnt/{share.name} "
                             f"({'read-write' if share.rw else 'read-only'}); unmount it first")
    name = mount_name(path, wanted, set(vm.shares))
    tag = f"fs-{name}"[:48]
    sock = vm.dir / f"{tag}.sock"
    sock.unlink(missing_ok=True)
    argv = ["/usr/libexec/virtiofsd", "--shared-dir", str(path), "--socket-path", str(sock),
            "--sandbox", "none", "--cache", "auto", "--announce-submounts"]
    if not rw:
        argv.append("--readonly")
    log = (vm.dir / f"{tag}.log").open("ab")
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                            start_new_session=True)
    share = Share(name=name, tag=tag, path=path, rw=rw, proc=proc)
    try:
        deadline = time.monotonic() + SOCKET_WAIT_S
        while not sock.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise ShareError(f"the file server for {path} did not start (see {tag}.log)")
            time.sleep(0.1)
        done = subprocess.run(["ch-remote", "--api-socket", str(vm.api), "add-fs",
                               f"tag={tag},socket={sock},id={tag}"],
                              capture_output=True, text=True, timeout=30, check=False)
        if done.returncode != 0:
            raise ShareError(f"the VM refused the folder device: {done.stderr.strip()[:300]}")
        time.sleep(0.5)                 # let the guest's PCI hotplug settle before mounting
        agent_call(vm, "mount_share", {"tag": tag, "name": name}, timeout=40)
    except Exception:
        _end(vm, share)
        raise
    vm.shares[name] = share
    return share


def unmount(vm: VM, name: str) -> Share:
    share = vm.shares.get(name)
    if share is None:
        raise ShareError(f"no share named {name!r} (shares: {', '.join(vm.shares) or 'none'})")
    try:
        agent_call(vm, "unmount_share", {"tag": share.tag, "name": name}, timeout=40)
    finally:
        _end(vm, share)
        vm.shares.pop(name, None)
    return share


def _end(vm: VM, share: Share) -> None:
    if vm.alive():
        subprocess.run(["ch-remote", "--api-socket", str(vm.api), "remove-device", share.tag],
                       capture_output=True, timeout=30, check=False)
    if share.proc.poll() is None:
        share.proc.terminate()
        try:
            share.proc.wait(5)
        except subprocess.TimeoutExpired:
            share.proc.kill()


def end_all(vm: VM) -> None:
    """The VM is going away: stop every file server it had (the devices go with the VMM)."""
    for share in list(vm.shares.values()):
        if share.proc.poll() is None:
            share.proc.terminate()
            try:
                share.proc.wait(5)
            except subprocess.TimeoutExpired:
                share.proc.kill()
    vm.shares.clear()
