"""Which paths live on a NETWORK filesystem — decided from the mount table, never by a stat.

The files card (`api_runs.run_files`) asks of every row whether the console could serve it, and
answering that means resolving the path and statting it. On a local disk that costs microseconds;
on an sshfs share every path component is a network round trip, and a conversation
run whose card listed 307 rows on the NAS share took 73.7 s to answer it once, 4 s for a single
row. So the card has to know WHICH rows are behind such a mount before it touches them, and the
only way to know that without touching them is lexical: the path lies under a mount point whose
filesystem type is a network or FUSE one (`/proc/self/mounts`).

`autofs` counts: the NAS share is an automount (`timeout=600`), and while it is unmounted only
the autofs entry is listed — the next access is the one that pays the SSH connect. `fuseblk` does
not: it is a local block device read through FUSE (NTFS, exFAT).

A local path that is a SYMLINK into a share is not caught: telling it apart needs the very
resolution this module exists to avoid. It is checked as local, which is correct and only slower.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

#: Filesystem types whose metadata calls cross a network. `fuse.*` is any FUSE filesystem except
#: the local `fuseblk`; `autofs` is the placeholder an automounted share leaves while unmounted.
_NETWORK_TYPES = frozenset({"nfs", "nfs4", "cifs", "smb3", "smbfs", "9p", "ceph", "autofs"})

MOUNTS = Path("/proc/self/mounts")


def _is_network(fstype: str) -> bool:
    return fstype in _NETWORK_TYPES or (fstype.startswith("fuse.") and fstype != "fuseblk")


def network_mounts(mounts: Path = MOUNTS) -> tuple[Path, ...]:
    """Every mount point whose filesystem is a network or FUSE one, deepest first. Read once per
    files-card request: the table is a few dozen lines of procfs, never a network call. A table
    that cannot be read yields none, so every row is checked as local — slower, never wrong.
    """
    try:
        lines = mounts.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ()
    points = set()
    for line in lines:
        fields = line.split()
        if len(fields) >= 3 and _is_network(fields[2]):
            # procfs escapes a space in a mount point as \040
            points.add(Path(fields[1].replace("\\040", " ")))
    return tuple(sorted(points, key=lambda p: len(p.parts), reverse=True))


def on_network(path: Path, mounts: Sequence[Path]) -> bool:
    """Whether `path` lies at or under one of `mounts` — lexically, with no filesystem call."""
    return any(path == m or m in path.parents for m in mounts)
