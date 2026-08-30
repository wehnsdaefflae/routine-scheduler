"""Watch the library's git HEAD, and turn a revision that BREAKS a routine into a decision.

The two interactive writers are gated: the engine's authoring actions carry the blast radius in
their approval question, and the Library tab's save carries a confirm digest. Neither catches
what arrives with no writer at all — the library-sync routine's `git pull`, an edit made on
disk, a container restored from a bundle. Those are legitimate ways for the library to move, and
they are exactly the ones nobody is looking at when they happen.

The library is a git repo, so every such change has a commit. This compares HEAD against the
last seen value on each scheduler tick — a `rev-parse` costs about a millisecond, which is not a
poll worth avoiding — and on a change re-resolves every routine.

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

from ..config import ServerConfig
from ..paths import atomic_write_json, read_json

log = logging.getLogger("rsched.library_watch")

# Where the last-seen HEAD lives: engine-owned derived state beside the other control files,
# never config. A missing marker means "first boot" and records the current HEAD without
# reporting — the alternative is every fresh install announcing its whole library as drift.
_MARKER = ".control/library-head.json"


def _head(repo) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo), check=False,
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _subject(repo, rev: str) -> str:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%s", rev], cwd=str(repo),
                             check=False, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


class LibraryWatch:
    """Scheduler-tick companion: notice that the library moved, and say who it broke."""

    def __init__(self, server: ServerConfig):
        self.server = server
        self._seen: str | None = None

    async def tick(self) -> None:
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
