"""A git INDEX LOCK in a repo this instance commits to: whose it is and whether it is
provably stale.

Git writes the index by creating `<gitdir>/index.lock` exclusively, writing the new index into
it and renaming it over `index`. A process that dies holding it leaves the file behind. From
then on EVERY write in that repo — `add`, `commit`, `rm`, `checkout` — fails with "Unable to
create '…/index.lock': File exists" while every read keeps working. That asymmetry is why
nothing noticed: on 2026-09-30 two routines' migration edits sat staged under an empty lock a
killed boot-time commit had left. Each routine's run-end autocommit would have failed the
same way until a person looked.

`libgit` removes a lock only when it is PROVABLY stale, which takes all four of:

- the caller holds the repo's commit lock (`paths.repo_lock_path`), so no cooperative writer —
  the daemon, an engine run, the web layer, the `git` util's `sync` — is between its `add` and its
  `commit`;
- no git process this process can see works in the repo: none has its working directory, a
  `-C`, a `--git-dir`, a `--work-tree` or a `GIT_DIR` inside it. A git that hides its working
  directory (another uid's) cannot be ruled out, so it counts;
- the lock is EMPTY. An interactive `git commit -a` writes the new index into the lock and
  keeps it while its editor is open — the one legitimate long hold — so a lock with content is
  a person's to judge, never this module's;
- it is older than `STALE_AFTER_S`.

The age is the margin for the holder the process scan cannot see: git run on the host outside
the container, or over the sshfs mount from another machine. Each git call of an rsched commit
ends within its timeout plus its termination grace (`procgroup`), a minute; ten cover a person's
git on a disk that stalls single commands for thirty seconds.

Everything here is lexical about OTHER processes' paths — nothing is resolved through the
filesystem — because a git whose working directory sits on a hung share would otherwise hang
the scan that was looking for it.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

#: How old an empty, unheld, unworked lock must be before it is removed (module docstring).
STALE_AFTER_S = 600

_PROC = Path("/proc")

#: git's global options whose value is the NEXT argument when not given as `--opt=value`.
#: Reading them stops at the first word that is not an option: the subcommand.
_VALUED = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"})


@dataclass(frozen=True)
class IndexLock:
    """One `index.lock` as found. `holders` are the visible git processes working in its repo;
    `blind` means there was no process table to read at all. `ino`/`mtime_ns` identify the file,
    so a removal can refuse a lock that was replaced after it was inspected.
    """

    path: Path
    age_s: float
    size: int
    holders: tuple[int, ...]
    blind: bool
    ino: int
    mtime_ns: int

    def kept_because(self, *, held: bool) -> str:
        """Why this lock must stay — '' when it is provably stale. `held` is whether the caller
        holds the repo's commit lock. The four conditions, in the module docstring's order.
        """
        if not held:
            return "another writer holds this repo's commit lock"
        if self.blind:
            return "there is no process table to rule out a running git"
        if self.holders:
            pids = ", ".join(str(p) for p in self.holders)
            return f"a git process works in this repo (pid {pids})"
        if self.size:
            return (f"it holds {self.size} bytes of index, which is what an interactive `git "
                    "commit` keeps while its editor is open; once no git is running, remove it "
                    "by hand")
        if self.age_s < STALE_AFTER_S:
            return (f"it is younger than {STALE_AFTER_S // 60} min, so an unseen git may still "
                    "hold it; the next commit after that removes it")
        return ""

    def describe(self) -> str:
        content = f"{self.size} bytes" if self.size else "empty"
        return f"{self.path} ({age_words(self.age_s)} old, {content})"


def age_words(seconds: float) -> str:
    """45 s · 12 min · 2 h 3 min · 3 d — how the reports say a lock's age."""
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60} min"
    return f"{s // 86400} d"


def locate(home: Path) -> tuple[Path, Path] | None:
    """(work-tree root, git dir) of the repo containing `home`, or None outside any repo. A
    `.git` FILE (a linked worktree) is followed to the git dir it names — that is where the
    worktree's own index and its lock live.
    """
    cur = Path(home).resolve()
    for d in (cur, *cur.parents):
        dot = d / ".git"
        if dot.is_dir():
            return d, dot
        if dot.is_file():
            try:
                text = dot.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            return d, (d / text.removeprefix("gitdir:").strip()).resolve()
    return None


def inspect(home: Path) -> IndexLock | None:
    """The repo's index lock as it stands now, or None when there is none (or no repo)."""
    found = locate(home)
    if found is None:
        return None
    root, gitdir = found
    path = gitdir / "index.lock"
    try:
        st = path.lstat()
    except OSError:
        return None
    holders, blind = _holders((root, gitdir))
    return IndexLock(path=path, age_s=max(0.0, time.time() - st.st_mtime), size=st.st_size,
                     holders=holders, blind=blind, ino=st.st_ino, mtime_ns=st.st_mtime_ns)


def remove(lock: IndexLock) -> bool:
    """Delete `lock` if the file is still the one inspected (same inode, mtime and size). A
    lock that changed in between is somebody's live one and stays.
    """
    try:
        st = lock.path.lstat()
        if (st.st_ino, st.st_mtime_ns, st.st_size) != (lock.ino, lock.mtime_ns, lock.size):
            return False
        lock.path.unlink()
    except OSError:
        return False
    return True


def _holders(bases: tuple[Path, ...]) -> tuple[tuple[int, ...], bool]:
    """Pids of the visible git processes working under any of `bases` — and whether the
    process table could not be read at all.
    """
    try:
        entries = [p for p in _PROC.iterdir() if p.name.isdigit()]
    except OSError:
        return (), True
    found = []
    for entry in entries:
        try:
            if (entry / "comm").read_text(encoding="utf-8").strip() != "git":
                continue
        except OSError:
            continue                     # exited between the listing and the read
        if _works_in(entry, bases):
            found.append(int(entry.name))
    return tuple(sorted(found)), False


def _works_in(entry: Path, bases: tuple[Path, ...]) -> bool:
    try:
        cwd = (entry / "cwd").readlink()
    except FileNotFoundError:
        return False                     # exited meanwhile
    except OSError:
        return True                      # another uid's git: it cannot be ruled out
    try:
        argv = [os.fsdecode(a) for a in (entry / "cmdline").read_bytes().split(b"\0") if a]
    except OSError:
        argv = []
    places = [cwd, *_option_places(cwd, argv[1:]), *_env_places(entry, cwd)]
    return any(_under(p, b) for p in places for b in bases)


def _option_places(cwd: Path, args: list[str]) -> list[Path]:
    """The directories git's own global options point one invocation at: `-C` (which composes
    with the ones before it), `--git-dir` and `--work-tree`, in either spelling.
    """
    out: list[Path] = []
    cur = cwd
    words = iter(args)
    for word in words:
        opt, eq, val = word.partition("=")
        if word in _VALUED:
            opt, val = word, next(words, "")
        elif not word.startswith("-"):
            break                        # the subcommand; what follows is not git's to read
        elif not eq:
            continue
        if opt == "-C":
            cur = cur / val
            out.append(cur)
        elif opt in ("--git-dir", "--work-tree"):
            out.append(cur / val)
    return out


def _env_places(entry: Path, cwd: Path) -> list[Path]:
    try:
        env = (entry / "environ").read_bytes().split(b"\0")
    except OSError:
        return []
    return [cwd / os.fsdecode(var.split(b"=", 1)[1]) for var in env
            if var.startswith((b"GIT_DIR=", b"GIT_WORK_TREE="))]


def _under(path: Path, base: Path) -> bool:
    p, b = os.path.normpath(path), os.path.normpath(base)
    return p == b or p.startswith(b.rstrip(os.sep) + os.sep)
