"""Who is calling, and may it mount that folder? Both answered by the caller's OWN sandbox.

Every desktop is one routine's, so the broker has to know which routine a request comes from —
and every caller holds the same bearer token, so the token cannot say. What differs between
callers is what the kernel lets each one touch: a util runs inside its run's Landlock jail, which
can write the routine's own directory and nothing of another routine's. So the caller PROVES its
identity by writing a one-time file into its working directory; the broker (which sees the same
homes at the same paths) reads it there, compares, and deletes it. A run cannot forge a file in a
directory its jail does not open — not through the util's arguments, not through a script that
declared the token. Identity is the directory's top: a child run working in
`routines/foo/runs/…` is `routines--foo`.

Mounting a folder is proved the same way, against the FOLDER: read-write needs a proof file
written inside it (the jail's write grant), read-only needs a digest of its listing — names,
types, sizes and nanosecond mtimes, which only a jail allowed to read the directory can produce.
An empty folder has nothing to prove read access with, so a read-only mount of one is refused.
No new grant model: whatever a run was granted is exactly what it can mount.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
from pathlib import Path

PROOF_PREFIX = ".desktop-proof-"
PROOF_NAME = re.compile(r"\.desktop-proof-[0-9a-f]{16}")
PROOF_VALUE = re.compile(r"[0-9a-f]{32,128}")
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
#: Where routine-shaped working directories live, by the label a desktop's name carries.
IDENTITY_HOMES = ("routines", "conversations", "background")


class ProofError(PermissionError):
    """A proof that does not hold — the caller is told why (403)."""


def identity(workdir: str, home: Path) -> str:
    """'routines--foo' for any directory inside <home>/routines/foo (and likewise for the
    conversation and background homes).
    """
    try:
        path = Path(workdir).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ProofError(f"working directory {workdir!r} is not visible to the desktop host"
                         ) from None
    for label in IDENTITY_HOMES:
        root = (home / label).resolve()
        if path != root and path.is_relative_to(root):
            first = path.relative_to(root).parts[0]
            if SLUG.fullmatch(first):
                return f"{label}--{first}"
    raise ProofError("a desktop belongs to a routine, a conversation or a background task; "
                     f"{workdir} is inside none of them")


def check_file(directory: Path, token: str) -> None:
    """`token` is 'NAME:VALUE'; <directory>/NAME must be a regular file (never a symlink) holding
    VALUE. It is consumed either way, so no proof is ever good twice.
    """
    name, _, value = (token or "").partition(":")
    if not PROOF_NAME.fullmatch(name) or not PROOF_VALUE.fullmatch(value):
        raise ProofError("malformed proof — the desktop util writes and sends it")
    path = directory / name
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise ProofError(f"no proof file in {directory} — the caller could not write there"
                         ) from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ProofError("the proof is not a regular file")
        got = os.read(fd, 256).decode("ascii", "replace").strip()
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)
    if not hmac.compare_digest(got, value):
        raise ProofError("the proof file does not hold the value sent")


def listing_digest(directory: Path) -> str:
    """sha256 over the sorted top-level entries (name, type, size, mtime_ns), proof files left
    out. The util computes the same function inside its jail; tests hold the two equal.
    """
    rows = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.startswith(PROOF_PREFIX):
                continue
            st = entry.stat(follow_symlinks=False)
            rows.append(f"{entry.name}\0{stat.S_IFMT(st.st_mode)}\0{st.st_size}\0"
                        f"{st.st_mtime_ns}")
    if not rows:
        raise ProofError(f"{directory} is empty, so read access to it cannot be shown — mount "
                         "it read-write, or put a file in it first")
    rows.sort()
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def check_digest(directory: Path, claimed: str) -> None:
    if not hmac.compare_digest(listing_digest(directory), claimed or ""):
        raise ProofError(f"the listing digest of {directory} does not match — the caller could "
                         "not read it")
