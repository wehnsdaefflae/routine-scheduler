"""Watch the library's git HEAD, and turn a revision that BREAKS a routine into a decision.

The two interactive writers are gated: the engine's authoring actions carry the blast radius in
their approval question, and the Library tab's save carries a confirm digest. Neither catches
what arrives with no writer at all — the library-sync routine's `git pull`, an edit made on
disk, a container restored from a bundle. Those are legitimate ways for the library to move, and
they are exactly the ones nobody is looking at when they happen.

The library is a git repo, so every such change has a commit. This compares HEAD against the
last seen value and, on a change, re-resolves every routine. It runs on its own interval
(CHECK_EVERY_S) rather than on every 5s scheduler tick: a `rev-parse` is cheap but it is still
a FORK, at one per tick ~17k of them a day, on the same loop executor the machine-queue refresh
and every run's llm tailer draw from — to watch a value that moves a few times a day.

What it does with a break is deliberately NOT a new channel. A routine that can no longer reach
a secret needs a DECISION (expose it, withhold it, unbind the rule), which is what the Decisions
page already settles on entity ids from the existing vocabulary; and `pending.py` already queues
records for that page with no live run behind them. So a break becomes a pending record, and
inherits the page, the audit trail and browser push without inventing an outbound send — which
the 0.230.0 decision forbids anyway.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time

from .. import libgit
from ..config import ServerConfig
from ..paths import atomic_write_json, read_json

log = logging.getLogger("rsched.library_watch")

# Where the last-seen HEAD lives: engine-owned derived state beside the other control files,
# never config. A missing marker means "first boot" and records the current HEAD without
# reporting — the alternative is every fresh install announcing its whole library as drift.
_MARKER = ".control/library-head.json"

#: How often HEAD is actually read. The library moves a handful of times a day — a sync pull, a
#: hand edit, a restore — and what this produces is a Decisions-page record a person reads later,
#: so a minute of latency costs nothing and 11 of every 12 forks were pure tax.
CHECK_EVERY_S = 60.0


def _read(repo, *args: str) -> str:
    """One git read against the library repo, through the package's single invoker.

    The guard stays here rather than in `libgit.git`: that function is best-effort about
    git's own exit status but does not catch a MISSING repo or a missing git binary, and
    letting either reach `tick`'s catch-all would `log.exception` on every check. Both are
    ordinary states for this watcher — a fresh install has no library repo yet — so they
    are an empty string, which `_check` reads as "nothing to compare".
    """
    try:
        out = libgit.git(repo, *args)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _head(repo) -> str:
    return _read(repo, "rev-parse", "HEAD")


def _subject(repo, rev: str) -> str:
    return _read(repo, "log", "-1", "--format=%s", rev)


class LibraryWatch:
    """Scheduler-tick companion: notice that the library moved, and say who it broke."""

    def __init__(self, server: ServerConfig):
        self.server = server
        self._seen: str | None = None
        self._last_check: float | None = None   # monotonic stamp of the last HEAD read

    async def tick(self) -> None:
        now = time.monotonic()
        if self._last_check is not None and now - self._last_check < CHECK_EVERY_S:
            return
        self._last_check = now
        try:
            await asyncio.to_thread(self._check)
        except Exception:
            # A watcher must never take the scheduler down with it: the whole value here is
            # noticing a problem, and a diagnostic that can stop the daemon is a bigger one.
            log.exception("library watch tick failed")

    def _check(self) -> None:
        repo = self.server.libraries_home
        head = _head(repo)
        if not head:
            return                                 # not a git repo (or git is unavailable)
        marker = self.server.routines_home / _MARKER
        if self._seen is None:
            saved = read_json(marker)
            self._seen = str(saved.get("head") or "") if isinstance(saved, dict) else ""
        if head == self._seen:
            return
        first_boot = not self._seen
        self._seen = head
        atomic_write_json(marker, {"head": head, "subject": _subject(repo, head)})
        if first_boot:
            return          # nothing to compare against; record and stay quiet
        self._report(head)

    def _report(self, head: str) -> None:
        """Re-resolve every routine and queue a decision for each one that is now broken.

        Only BLOCKING rows queue. An interrupt already asks the user at the moment it matters,
        and queueing those too would turn a genuine signal into a list nobody reads.
        """
        from .. import pending
        from ..config import load_routine
        from ..readmodels.surface import routine_surface

        home = self.server.routines_home
        if not home.is_dir():
            return
        already = {rec.get("fields", {}).get("entity")
                   for rec in pending.load_all(home) if rec.get("kind") == "library-drift"}
        subject = _subject(self.server.libraries_home, head)
        for d in sorted(p for p in home.iterdir() if p.is_dir() and not p.name.startswith(".")):
            cfg, _ = load_routine(d)
            if cfg is None:
                continue
            try:
                surface = routine_surface(self.server, cfg)
            except (OSError, ValueError):
                continue
            for node in surface["nodes"]:
                if node["severity"] != "blocks":
                    continue
                key = f"{cfg.slug}:{node['id']}"
                if key in already:
                    continue                        # one record per gap, not one per commit
                pending.queue(
                    home, kind="library-drift", routine=cfg.slug, run_id="",
                    fields={"entity": key, "node": node, "head": head},
                    summary=(f"{cfg.slug}: {node['id']} — {node['why']}. "
                             f"After library change {head[:8]} ({subject})"))
                log.info("library drift: %s broke %s", head[:8], key)
