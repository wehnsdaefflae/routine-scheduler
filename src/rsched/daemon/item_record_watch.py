"""The one writer of the item records (`rsched/item_records.py`): observe self-audit's
`report.json` every time it changes, so no finding or decision it ever carried can vanish.

A stat per tick (the file's mtime and size), and a read only when that changed — self-audit
rewrites the file a few times a night. The parse runs in a thread: the report is ~1 MB of JSON.

A row that left the report while LIVE is filed as `item_dropped_live` on the blocked-work
surface, naming the ids. The index already keeps such a row live — this is what tells someone
that self-audit's own ledger lost it, which is the part only self-audit can repair.

Never raises into the tick: the check guards against losing work and must not stop the work.
"""

from __future__ import annotations

import asyncio
import logging

from .. import item_records
from ..config import ServerConfig
from ..health_events import log_health_event
from ..paths import read_json
from ..priorities import SELF_AUDIT_SLUG

log = logging.getLogger("rsched.daemon")


class ItemRecordWatch:
    """Observe self-audit's report.json whenever its stat changes."""

    def __init__(self, server: ServerConfig) -> None:
        self.server = server
        self._stat: tuple[int, int] | None = None

    async def tick(self) -> None:
        try:
            await asyncio.to_thread(self.check)
        except Exception:
            log.exception("item-record-watch: observing report.json raised")

    def check(self) -> list[str]:
        home = self.server.routines_home
        path = home / SELF_AUDIT_SLUG / "audit" / "report.json"
        try:
            st = path.stat()
        except OSError:
            return []
        stat = (st.st_mtime_ns, st.st_size)
        if stat == self._stat:
            return []
        report = read_json(path)
        if not isinstance(report, dict):
            return []       # mid-write or hand-broken: try again when it changes
        self._stat = stat
        dropped = item_records.observe(home, report)
        if dropped:
            log_health_event(home, "item_dropped_live", routine=SELF_AUDIT_SLUG,
                             run_id=str(report.get("run_id") or ""), items=",".join(dropped),
                             detail=f"report.json was rewritten without {', '.join(dropped)} "
                                    "while each was open or in_progress; the item index keeps "
                                    "them live — restore their rows with a real status")
            log.warning("item-record-watch: live items left report.json: %s", dropped)
        return dropped
