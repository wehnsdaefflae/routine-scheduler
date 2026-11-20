"""Screen ONE file for the credential values this instance stores, inside the daemon, and
return only findings and coverage — never a value (D296, operator decision 2026-11-19, a).

THE OPERATION. A caller names a PATH; the daemon opens that path itself, reads it under a byte
bound, matches the needle set `credneedles.collect` builds from the stores it alone can read,
and answers with the NAMES of what the file carries, the coverage of the comparison, and the
sha256 of the exact bytes it screened. `rsched export` can then say which files it compared by
VALUE instead of exiting 0 having compared nothing for two of its sources.

WHY IT IS NOT A CREDENTIAL ORACLE. The caller submits no bytes — it names a path the daemon
opens — so it cannot ask "does the instance hold THIS string?" by choosing content. What a
caller can still learn is whether a file ALREADY on disk inside an authorized root carries a
stored credential, which is precisely what it is asking on the export's behalf, and a value it
could only confirm by guessing it whole (needles match whole stored values of at least
`REDACT_MIN_CHARS`; no prefix and no shorter fragment ever matches). That residue is stated
here rather than papered over: this is a confirmation channel for a complete guess, not a
search channel.

THE FOUR BOUNDS, each with the failure it exists to stop:
- **Authorized roots.** The path must resolve inside one of the homes the instance is
  configured with. A mirror's screener has no business reading /etc/shadow, and a caller that
  names it gets a refusal, not a finding.
- **`O_NOFOLLOW` plus a regular-file check on the open fd.** A symlink final component is
  refused outright, and a FIFO or a device is never read (a screen that blocks forever on a
  pipe takes the daemon's thread with it). Containment is judged on the RESOLVED path, so a
  symlink pointing out of the roots cannot smuggle a target in either.
- **A byte bound** (`MAX_SCREEN_BYTES`). A file larger than the bound is screened as far as the
  bound reaches and reported `truncated` — which forces `unknown`, never `clean`.
- **A re-stat after reading** (`st_dev`, `st_ino`, `st_size`, `st_mtime_ns`). A file replaced
  while it was being read is reported `changed`, and a changed file is never `clean`: the
  verdict belongs to the bytes that were hashed, and the digest is what binds it to them.

THE DISPOSITION, and the one rule the whole interface rests on: **`clean` is said only when
coverage is full, the whole file was read, nothing matched, and nothing changed underneath.**
Every other outcome is `unknown`. A missing or unreadable path is `unknown` too and NEVER
"absent" — a discovery failure read as confirmed absence is how a mirror prunes a file nobody
screened.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .credneedles import Needles, collect, secret_names

#: As far as a screen reads one file. ABOVE the export's own 10 MiB exclusion on purpose: the
#: files that cutoff drops are exactly the ones nothing has ever compared by value.
MAX_SCREEN_BYTES = 64 * 1024 * 1024
#: One read. Big enough that the overlap is noise, small enough to bound the daemon's memory.
CHUNK_BYTES = 1024 * 1024

CLEAN, CARRIES, UNKNOWN = "clean", "carries", "unknown"


class ScreenRefusedError(ValueError):
    """The path may not be screened at all — outside every authorized root, a symlink, or not
    a regular file. A refusal is not a finding and never a verdict about content.

    It carries a stable `code` as well as its prose. The prose interpolates the path, which
    makes it useless to assert on: pytest derives `tmp_path` from the TEST's own name, so a
    test named `..._symlink_...` gets a temp directory whose name contains "symlink" and an
    assertion like `"symlink" in reason` passes for ANY refusal of a path under it. That
    exact false pass hid a real finding here on 2026-11-20 — a symlink out of the roots is
    refused by CONTAINMENT, and O_NOFOLLOW is only reached by a link whose target is inside
    them. A caller branching on the reason needs the code for the same robustness.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Screened:
    """One file's screening result. `carries` holds the NAMES a matched credential is stored
    under — the same names D171 already serves — and never a value or an offset.
    """

    path: str
    disposition: str
    carries: list[str] = field(default_factory=list)
    bytes_screened: int = 0
    digest: str = ""
    truncated: bool = False
    changed: bool = False
    coverage: str = "partial"
    unreadable_sources: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "disposition": self.disposition,
            "carries": self.carries,
            "bytes_screened": self.bytes_screened,
            "digest": self.digest,
            "truncated": self.truncated,
            "changed": self.changed,
            "coverage": self.coverage,
            "unreadable_sources": self.unreadable_sources,
            "error": self.error,
        }


def authorized_roots(server) -> tuple[Path, ...]:
    """The homes a screen may read inside: every routine-shaped tree and the library. Taken
    from the server config, so a root the operator adds is screenable the day it exists and a
    path outside all of them is refused without a list to maintain here.
    """
    names = ("routines_home", "conversations_home", "background_home", "libraries_home")
    out = []
    for name in names:
        value = getattr(server, name, None)
        if value:
            out.append(Path(value).resolve())
    return tuple(out)


def _inside(resolved: Path, roots: tuple[Path, ...]) -> bool:
    return any(resolved == root or root in resolved.parents for root in roots)


def _open_regular(path: Path) -> int:
    """Open with the final component NOT followed, then prove the fd is a regular file.

    Order matters: the check is on the OPEN DESCRIPTOR, so nothing can be swapped between a
    stat and an open (the TOCTOU the brief names). O_NONBLOCK keeps a FIFO that slipped past
    the type check from blocking the open itself.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:   # the final component is a symlink and O_NOFOLLOW held
            raise ScreenRefusedError("symlink",
                                 f"{path} is a symlink — a screen follows nothing") from exc
        raise
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ScreenRefusedError("not-a-regular-file", f"{path} is not a regular file")
    return fd


def _scan(fd: int, needles: Needles) -> tuple[list[str], int, str, bool]:
    """Stream the fd, matching needles across chunk boundaries.

    The buffer keeps `longest - 1` bytes of the previous chunk, which is exactly the overlap a
    credential needs to be found when it straddles a read: anything shorter and a 40-byte token
    split 20/20 by a 1 MiB boundary goes unseen, which is the whole value of screening.
    """
    hasher = hashlib.sha256()
    overlap = max(needles.longest - 1, 0)
    carries: list[str] = []
    tail = b""
    total = 0
    truncated = False
    while total < MAX_SCREEN_BYTES:
        chunk = os.read(fd, min(CHUNK_BYTES, MAX_SCREEN_BYTES - total))
        if not chunk:
            break
        total += len(chunk)
        hasher.update(chunk)
        window = tail + chunk
        for raw in needles.by_bytes:
            if raw in window:
                for name in needles.names_for(raw):
                    if name not in carries:
                        carries.append(name)
        tail = window[-overlap:] if overlap else b""
    else:
        truncated = bool(os.read(fd, 1))
    return sorted(carries), total, hasher.hexdigest(), truncated


def screen(path: str | Path, roots: tuple[Path, ...],
           needles: Needles | None = None) -> Screened:
    """Screen one path inside `roots`. Raises ScreenRefusedError for a path that may not be
    read at all; every other failure is reported as `unknown` with its reason, because a caller
    that
    gets an exception where it expected a verdict tends to treat the absence as safety.
    """
    needles = needles if needles is not None else collect()
    asked = Path(path)
    if not asked.is_absolute():
        raise ScreenRefusedError("not-absolute", "a screened path is absolute")
    resolved = Path(os.path.realpath(asked))
    if not _inside(resolved, roots):
        raise ScreenRefusedError("outside-roots", f"{asked} is outside every authorized root")
    out = Screened(path=str(asked), disposition=UNKNOWN,
                   coverage=needles.coverage, unreadable_sources=needles.unreadable())
    try:
        fd = _open_regular(asked)
    except OSError as exc:
        # NEVER "absent": a path the daemon could not open has not been shown to be anything,
        # and a caller must not prune or publish on the strength of a failed read.
        out.error = f"{type(exc).__name__}: {exc}"
        return out
    try:
        before = os.fstat(fd)
        out.carries, out.bytes_screened, out.digest, out.truncated = _scan(fd, needles)
        after = os.fstat(fd)
        out.changed = ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                       != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns))
    except OSError as exc:
        out.error = f"{type(exc).__name__}: {exc}"
        return out
    finally:
        os.close(fd)
    if out.carries:
        out.disposition = CARRIES
    elif out.coverage == "full" and not out.truncated and not out.changed:
        out.disposition = CLEAN
    return out


def screen_report(paths: list[str], roots: tuple[Path, ...]) -> dict:
    """Screen several paths against ONE needle set — the stores are read once, not per path.

    A refused path is reported in `refused` rather than aborting the batch: one bad path in a
    mirror's file list must not cost the screening of every other file.

    The needle set's webhook-token sweep walks `roots` themselves: a routine's own
    `routine.yaml` holds the one credential the scheduler writes INTO a routine's tree, so the
    trees being screened are exactly the trees those tokens live in.
    """
    needles = collect(routines_homes=roots)
    results, refused = [], []
    for path in paths:
        try:
            results.append(screen(path, roots, needles).as_dict())
        except ScreenRefusedError as exc:
            refused.append({"path": path, "code": exc.code, "reason": str(exc)})
    return {"screened": results, "refused": refused,
            "sources": secret_names(needles),
            "max_bytes": MAX_SCREEN_BYTES}
