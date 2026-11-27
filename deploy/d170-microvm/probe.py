"""D170 phase-1 PROBE — what one util call costs in a per-call micro-VM vs the Landlock jail.

NOT A LIVE PATH. Nothing in the engine imports this file and nothing should: it exists to
answer one question with numbers — if the per-call boot cost of a cloud-hypervisor micro-VM
is affordable, replacing `sandbox.py`'s Landlock jail becomes a design question; if it is
not, the idea stops here. The measurement it produced is written up in
`deploy/d170-microvm/README.md`, which also records where the guest kernel and rootfs came
from, since the figures cannot be reproduced without them.

It is deliberately throwaway quality. It does not mount the caller's granted roots, inject
declared secrets, stream stdout/stderr, honour a timeout, support abort, or write the
`.util_outputs/` spill — all of which a real implementation must do, and each of which can
only make the VM arm slower than measured here. The README names them one by one.

Both arms run the SAME command: the host interpreter against one real util's `main.py`.
The jail arm wraps it exactly as `sandbox.wrap` does (`python landlock.py <spec> -- cmd`);
the VM arm boots a kernel with an initramfs carrying that interpreter, its stdlib and that
util, runs the command as PID 1's child and powers off. The difference between the two
medians is the answer.

Verbs (`--help` for the arguments of each):

    checksums   size + sha256 of the kernel and rootfs tarball, for provenance
    image       build the fat guest initramfs (and a do-nothing floor image)
    boot        boot one image once, verbosely, and print the guest's serial output
    measure     N calls per arm: bare, jail, vm, vm floor — median/p90 per arm
    footprint   one VM boot whose hypervisor peak RSS is read from its own /proc
    concurrency launch K VMs at once and report per-VM wall time and batch total
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import stat
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LANDLOCK = REPO / "src" / "rsched" / "landlock.py"
CH = Path("/usr/local/bin/cloud-hypervisor")

#: The guest userland taken from the Alpine minirootfs tarball: busybox and the musl loader
#: it links against. Omitting the loader boots to `Failed to execute /init (error -2)` and a
#: triple-fault loop, which is what this list exists to remember.
GUEST_BASE = ("bin/busybox", "lib/ld-musl-x86_64.so.1", "lib/libc.musl-x86_64.so.1")
APPLETS = ("bin/sh", "bin/mount", "bin/echo", "sbin/poweroff", "bin/cat")
CHAR_NODES = (("dev/console", 5, 1), ("dev/null", 1, 3), ("dev/ttyS0", 4, 64))

#: The host files the guest needs to run the engine's own interpreter. The stdlib tree comes
#: with them; everything else a util might import does not, which is one reason the measured
#: image is a floor rather than a production image.
HOST_FILES = ("/usr/local/bin/python3.12", "/usr/local/lib/libpython3.12.so.1.0",
              "/lib/x86_64-linux-gnu/libc.so.6", "/lib/x86_64-linux-gnu/libm.so.6",
              "/lib/x86_64-linux-gnu/libz.so.1", "/lib64/ld-linux-x86-64.so.2")
HOST_TREES = ("/usr/local/lib/python3.12",)

GUEST_PAYLOAD = "/payload.json"
INIT = ("#!/bin/busybox sh\n"
        "/bin/busybox mount -t proc proc /proc 2>/dev/null\n"
        "{}\n"
        "/bin/busybox poweroff -f\n")


class Cpio:
    """A newc cpio archive assembled in memory.

    Written by hand because the measurement host has no cpio, no busybox and no root: device
    entries are DECLARED in the archive header rather than mknod'd, which needs no privilege.
    """

    def __init__(self) -> None:
        self.buf = io.BytesIO()
        self.ino = 1
        self.names: set[str] = set()

    def _entry(self, name: str, mode: int, data: bytes = b"",
               rdev: tuple[int, int] = (0, 0)) -> None:
        if name in self.names:
            return
        self.names.add(name)
        raw = name.encode() + b"\0"
        fields = (self.ino, mode, 0, 0, 1, 0, len(data), 0, 0, rdev[0], rdev[1], len(raw), 0)
        self.ino += 1
        self.buf.write(b"070701" + b"".join(b"%08X" % f for f in fields))
        self.buf.write(raw)
        self._pad()
        self.buf.write(data)
        self._pad()

    def _pad(self) -> None:
        rem = self.buf.tell() % 4
        if rem:
            self.buf.write(b"\0" * (4 - rem))

    def add_dir(self, name: str) -> None:
        self._entry(name, stat.S_IFDIR | 0o755)

    def add_dirs(self, rel: str) -> None:
        parts = Path(rel).parts
        for depth in range(1, len(parts) + 1):
            self.add_dir(str(Path(*parts[:depth])))

    def add_file(self, name: str, data: bytes, mode: int = 0o755) -> None:
        self._entry(name, stat.S_IFREG | mode, data)

    def add_symlink(self, name: str, target: str) -> None:
        self._entry(name, stat.S_IFLNK | 0o777, target.encode())

    def add_chardev(self, name: str, major: int, minor: int) -> None:
        self._entry(name, stat.S_IFCHR | 0o600, b"", (major, minor))

    def finish(self) -> bytes:
        self._entry("TRAILER!!!", 0)
        return self.buf.getvalue()


def _tar_members(tarball: Path, wanted: set[str]) -> dict[str, tuple[tarfile.TarInfo, bytes]]:
    """The named members of the rootfs tarball, following symlinks to their targets."""
    found: dict[str, tuple[tarfile.TarInfo, bytes]] = {}
    with tarfile.open(tarball) as tar:
        members = {m.name.lstrip("./"): m for m in tar.getmembers()}
        pending = set(wanted)
        while pending:
            name = pending.pop()
            member = members.get(name)
            if member is None:
                continue
            if member.issym():
                target = member.linkname
                nxt = target.lstrip("/") if target.startswith("/") else str(
                    Path(name).parent / target).lstrip("./")
                if nxt not in found:
                    pending.add(nxt)
                found[name] = (member, b"")
            elif member.isfile():
                handle = tar.extractfile(member)
                found[name] = (member, handle.read() if handle else b"")
    return found


def build_image(tarball: Path, util_main: Path, payload: bytes, workload: str,
                out: Path) -> dict:
    """Write one initramfs carrying the guest base, the host interpreter and one util."""
    cpio = Cpio()
    for d in ("bin", "sbin", "lib", "lib64", "dev", "proc", "sys", "tmp", "util"):
        cpio.add_dir(d)
    for name, (member, data) in sorted(_tar_members(tarball, set(GUEST_BASE)).items()):
        if member.issym():
            cpio.add_symlink(name, member.linkname)
        else:
            cpio.add_file(name, data, member.mode & 0o7777 or 0o755)
    for applet in APPLETS:
        cpio.add_symlink(applet, "/bin/busybox")
    for name, major, minor in CHAR_NODES:
        cpio.add_chardev(name, major, minor)
    added = 0
    for host in HOST_FILES:
        rel = host.lstrip("/")
        cpio.add_dirs(str(Path(rel).parent))
        cpio.add_file(rel, Path(host).resolve().read_bytes())
        added += 1
    for tree in HOST_TREES:
        for root, _dirs, names in os.walk(tree):
            if "__pycache__" in root:
                continue
            rel_dir = Path(root).relative_to("/")
            cpio.add_dirs(str(rel_dir))
            for n in names:
                src = Path(root) / n
                if n.endswith(".pyc") or src.is_symlink() or not src.is_file():
                    continue
                cpio.add_file(str(rel_dir / n), src.read_bytes(), 0o644)
                added += 1
    cpio.add_file("util/main.py", util_main.read_bytes())
    cpio.add_file(GUEST_PAYLOAD.lstrip("/"), payload, 0o644)
    cpio.add_file("init", INIT.format(workload).encode())
    raw = cpio.finish()
    out.write_bytes(raw)
    return {"image": str(out), "bytes": len(raw), "entries": len(cpio.names),
            "host_files": added}


def ch_cmd(kernel: Path, image: Path, memory: str, *, quiet: bool = True) -> list[str]:
    """The cloud-hypervisor command line for one direct-kernel boot of `image`."""
    cmdline = "console=ttyS0 reboot=t panic=-1" + (" quiet loglevel=0" if quiet else "")
    return [str(CH), "--kernel", str(kernel), "--initramfs", str(image),
            "--cmdline", cmdline, "--cpus", "boot=1", "--memory", f"size={memory}",
            "--serial", "tty", "--console", "off"]


def _run(cmd: list[str], timeout: int = 120) -> tuple[float, int, str, str]:
    started = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    return time.perf_counter() - started, proc.returncode, proc.stdout, proc.stderr


def jail_cmd(host_cmd: list[str], roots: tuple[str, ...]) -> list[str]:
    """`host_cmd` wrapped the way sandbox.wrap wraps every util subprocess today."""
    spec = {"ro": sorted(roots), "rw": [tempfile.gettempdir()], "ls": [], "net": False}
    return [sys.executable, str(LANDLOCK), json.dumps(spec, separators=(",", ":")),
            "--", *host_cmd]


def stats(samples: list[float]) -> dict:
    """Median, p90 and the extremes of a sample of wall times, in milliseconds."""
    ordered = sorted(samples)
    idx = max(0, min(len(ordered) - 1, round(0.9 * (len(ordered) - 1))))
    return {"n": len(ordered), "median_ms": round(1000 * statistics.median(ordered), 2),
            "p90_ms": round(1000 * ordered[idx], 2), "min_ms": round(1000 * ordered[0], 2),
            "max_ms": round(1000 * ordered[-1], 2)}


def vm_footprint(cmd: list[str]) -> dict:
    """One VM boot whose hypervisor peak RSS is read from its own /proc/<pid>/status.

    `ru_maxrss` over RUSAGE_CHILDREN is a high-water mark across every child the process ever
    reaped, so it cannot attribute memory to one VM; VmHWM polled on the hypervisor's own pid
    can. A spike between samples is still missed, which is why the sample count is reported.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    status = Path(f"/proc/{proc.pid}/status")
    hwm = samples = 0
    started = time.perf_counter()
    while proc.poll() is None:
        try:
            for line in status.read_text().splitlines():
                if line.startswith("VmHWM:"):
                    hwm = max(hwm, int(line.split()[1]))
                    samples += 1
        except OSError:
            break
        time.sleep(0.005)
    out, _ = proc.communicate(timeout=30)
    return {"wall_s": round(time.perf_counter() - started, 4), "peak_vmhwm_kb": hwm,
            "poll_samples": samples, "exit": proc.returncode, "out": (out or "")[-120:]}


def concurrency(cmd: list[str], parallel: int, marker: str) -> dict:
    """Launch `parallel` VMs at once; report per-VM wall time and the batch total."""
    started = time.perf_counter()
    procs = [(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True), time.perf_counter()) for _ in range(parallel)]
    walls, ok = [], 0
    for proc, t0 in procs:
        out, _ = proc.communicate(timeout=300)
        walls.append(time.perf_counter() - t0)
        ok += 1 if marker in (out or "") else 0
    return {"parallel": parallel, "batch_wall_s": round(time.perf_counter() - started, 3),
            "per_vm": stats(walls), "outputs_with_marker": ok}


def _args(argv: list[str]) -> argparse.Namespace:
    work = Path(tempfile.gettempdir()) / "d170"
    parser = argparse.ArgumentParser(description=__doc__ or "", add_help=True)
    parser.add_argument("verb", choices=("checksums", "image", "boot", "measure",
                                         "footprint", "concurrency"))
    parser.add_argument("--kernel", type=Path, default=work / "assets" / "vmlinuz-virt")
    parser.add_argument("--rootfs-tar", type=Path,
                        default=work / "assets" / "alpine-minirootfs.tar.gz")
    parser.add_argument("--util-main", type=Path, required=False,
                        help="the util's main.py both arms execute")
    parser.add_argument("--work", type=Path, default=work)
    parser.add_argument("--memory", default="512M")
    parser.add_argument("-n", type=int, default=25)
    parser.add_argument("--parallel", default="1,4,8,12")
    parser.add_argument("--image", type=Path, default=None)
    return parser.parse_args(argv)


def _workload(opts: argparse.Namespace) -> tuple[str, list[str], bytes]:
    payload = json.dumps({"name": "d170", "k": [1, 2, 3]}).encode() + b"\n"
    host_payload = opts.work / "payload.json"
    host_payload.write_bytes(payload)
    guest = f"/usr/local/bin/python3.12 /util/main.py get {GUEST_PAYLOAD} name"
    host = ["/usr/local/bin/python3.12", str(opts.util_main), "get", str(host_payload), "name"]
    return guest, host, payload


def main(argv: list[str]) -> int:
    opts = _args(argv)
    opts.work.mkdir(parents=True, exist_ok=True)
    fat, floor = opts.work / "initramfs-fat.cpio", opts.work / "initramfs-floor.cpio"
    report: dict = {}
    if opts.verb == "checksums":
        report = {str(p): {"bytes": p.stat().st_size,
                           "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                  for p in (opts.kernel, opts.rootfs_tar) if p.exists()}
    elif opts.verb == "image":
        guest, _host, payload = _workload(opts)
        report = {"fat": build_image(opts.rootfs_tar, opts.util_main, payload, guest, fat),
                  "floor": build_image(opts.rootfs_tar, opts.util_main, payload,
                                       "/bin/busybox true", floor)}
    elif opts.verb == "boot":
        wall, code, out, err = _run(ch_cmd(opts.kernel, opts.image or fat, opts.memory,
                                           quiet=False))
        report = {"wall_s": round(wall, 4), "exit": code, "serial_tail": out[-2000:],
                  "stderr_tail": err[-600:]}
    elif opts.verb == "measure":
        _guest, host, _payload = _workload(opts)
        roots = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc",
                 str(opts.util_main.parents[2]), str(Path(sys.executable).parents[1]))
        arms = {"bare": host, "jail": jail_cmd(host, roots),
                "vm": ch_cmd(opts.kernel, fat, opts.memory),
                "vm_floor": ch_cmd(opts.kernel, floor, opts.memory)}
        for arm, cmd in arms.items():
            samples, first = [], None
            for _ in range(opts.n):
                wall, code, out, err = _run(cmd)
                samples.append(wall)
                first = first or {"exit": code, "out": out[-120:], "err": err[-120:]}
            report[arm] = {**stats(samples), "first_call": first}
        (opts.work / "results.json").write_text(json.dumps(report, indent=1))
    elif opts.verb == "footprint":
        report = {"run": vm_footprint(ch_cmd(opts.kernel, opts.image or fat, opts.memory))}
    else:
        report = {"batches": [concurrency(ch_cmd(opts.kernel, fat, opts.memory), int(k),
                                          "d170")
                              for k in opts.parallel.split(",")]}
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
