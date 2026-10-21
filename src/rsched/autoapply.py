"""The AUTONOMOUS apply queue — a dial-permitted `config_patch` that needs no click (D191).

The operator's decision, 2026-10-19: *"build the server surface + D191's apply route: a
decision's config_patch may name {"server": {...}} with its own declared field list, applied
autonomously only for fields on that list, everything else still a click"*. Step 1 built the
policy (`configdial.py`), step 2 the surfaces and their apply routes. This is the half that
makes a permitted patch LAND without the operator doing anything.

**Why a queue and not a call.** The engine never writes config — every `routine.yaml` and
`config.yaml` write in this product goes through the web layer's one validated writer
(`web/routines_common.write_routine_config`, `web/settings/common.update_config`), which
validates, commits under the repo lock, rescans the fire table and tells a live run what
changed. A run is a SUBPROCESS: it cannot call that writer, and giving the engine a second
writer is exactly the "second legitimate-looking route to the same boundary" R2702 warns
about. So the ask seam does what it already does for a creation it may not perform
(`rsched/pending.py`) and for a web edit it may not land mid-run (`rsched/pending_edits.py`):
it writes a durable REQUEST and the config-writing half performs it.

**What is different from those two.** A pending creation waits for a human; this does not —
that is the whole point. The daemon drains it on the tick that follows, through an applier
injected by the web layer at startup (`web/autoapply_drain.py`), so no `daemon/` module
imports `web/` and the import direction the tree depends on is unchanged.

**The dial is judged THREE times and that is deliberate.** At the ask (`engine/interact.py`,
so the run learns at once that its patch was taken and no card was filed), here at the write
(a stale request from a routine whose dial has since been turned down must not apply), and in
the applier before it writes (the boundary; `configdial.refusals` is the one reading).
R2702's caution is that a check living only in the caller's restraint is not a boundary.

**A refused or failed request becomes an ordinary decision card**, never a silent drop: the
record the ask would have filed is written by the drainer with a note saying the autonomous
apply was refused and why. A config change that neither applied nor asked is the one state
this surface must never be in.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import spool
from .ids import now_iso
from .paths import read_json

log = logging.getLogger("rsched.autoapply")

#: The spool family under `<routines_home>/.control/`, and the entry prefix (queue-ordered).
FAMILY = "auto-apply"
PREFIX = "aa"
#: Per-routine cap. A run that files one of these per turn is a defect in that run, not a
#: reason to fill the disk; past the cap the ask falls back to an ordinary decision card.
MAX_PENDING = 32


def spool_dir(routines_home: Path, slug: str) -> Path:
    return spool.spool_dir(routines_home, FAMILY, slug)


def pending(routines_home: Path, slug: str) -> list[Path]:
    """Undrained requests for one routine, oldest first (chrono names sort in queue order)."""
    return spool.pending(routines_home, FAMILY, slug, PREFIX)


def slugs_with_pending(routines_home: Path) -> list[str]:
    return spool.slugs_with(routines_home, FAMILY, PREFIX)


def queue(routines_home: Path, slug: str, *, patch: dict[str, Any], level: str,
          qid: str, run_id: str, question: str) -> Path | None:
    """Record one autonomous-apply request, or None when the spool is full.

    `qid` and `question` ride along because a REFUSED request still owes the operator the
    decision card the ask would have filed — the drainer files it under the same qid, so the
    run's own record of what it asked still matches what the page shows.
    """
    if len(pending(routines_home, slug)) >= MAX_PENDING:
        log.warning("auto-apply spool full for %s (%d) — the ask stays a decision card",
                    slug, MAX_PENDING)
        return None
    return spool.write(routines_home, FAMILY, slug,
                       {"slug": slug, "patch": dict(patch), "level": level, "qid": qid,
                        "run_id": run_id, "question": question, "ts": now_iso()},
                       prefix=PREFIX)


def read_request(path: Path) -> dict:
    """One request record ({} when the file is gone or unreadable — a drain never raises)."""
    rec = read_json(path)
    return rec if isinstance(rec, dict) else {}
