"""What a branch IS RIGHT NOW — the reading D306 point 2's rail section is fed by.

The `/lineage` route has always answered the structural question (who forked from whom, at which
turn). The operator's diagnosis of why branches went unused was the console's — *"unused only
because the UI is bad"* — and point 2 names the gap precisely: branches are "a lineage LINE in the
header: names joined by commas, no state, no way to tell a branch working from one long finished."

So this module answers the LIVE question about each one: what state its newest run is in, when it
last moved, and whether it has HANDED BACK. Those three are what turn a list of names into
something a person can act on.

Its own module rather than more of `api_branches.py` because it is a different kind of work: that
file owns three ROUTES (fork, hand-back, lineage) and the decisions they encode, while this reads
the registry and the inbox archive to say what became of a conversation. The route keeps composing
the answer; this says what one row carries.

HANDED BACK IS READ, NEVER STORED. `branches.hand_back` files an inbox message into the parent
with `via="branch"` and a name of the form `branch-<slug>-<ts>`, and the parent's run consumes it
by renaming it into `<run_dir>/consumed/`. So the fact lives in two places depending on timing —
still queued in `inbox/`, or archived under a run — and both are read here. A flag written into
the branch's own config would be a third copy of a fact the filesystem already carries, and the
one most likely to drift.
"""

from __future__ import annotations

import json
from pathlib import Path

#: The inbox `via` a hand-back travels under (`branches.hand_back`). A message with any other via
#: is somebody else's freight — a user injection, a detached task's delivery, another routine's
#: report — and must not read as a branch having handed back.
HANDBACK_VIA = "branch"


def _message_via(path: Path) -> str:
    """One inbox message's `via`, or "" when the file is unreadable or not a message.

    Never raises: this is a READ for a rail section, and a half-written message (the filing is an
    atomic rename, but an operator can put anything in that directory) must not take a page down.
    """
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(row.get("via") or "") if isinstance(row, dict) else ""


def handed_back(parent_dir: Path, slug: str) -> bool:
    """Has branch `slug` handed back to the conversation at `parent_dir`?

    True when a `via: branch` message naming that slug is either still queued in the parent's
    inbox or archived in one of its runs' `consumed/` directories. The name carries the slug
    (`branch-<slug>-<ts>`), which is why this needs no message body: a hand-back's TEXT is the
    branch's own summary and matching on it would be matching on prose.
    """
    stem = f"branch-{slug}-"
    queued = parent_dir / "inbox"
    if queued.is_dir():
        for path in queued.glob(f"{stem}*"):
            if _message_via(path) == HANDBACK_VIA:
                return True
    runs = parent_dir / "runs"
    if runs.is_dir():
        # Newest first: a hand-back is usually recent, and a conversation with hundreds of runs
        # should not read every one of them to answer yes.
        for run in sorted(runs.iterdir(), reverse=True):
            consumed = run / "consumed"
            if not consumed.is_dir():
                continue
            for path in consumed.glob(f"{stem}*"):
                if _message_via(path) == HANDBACK_VIA:
                    return True
    return False


def live_fields(registry_rows: dict, slug: str) -> dict:
    """`state` and `updated` for one conversation, from the catalog the page already has.

    `registry_rows` is `slug -> row` out of the conversations listing (`conversations_common`'s
    own `_item` shape), so this adds no filesystem read of its own: the catalog is memoized and
    single-flighted, and the rail must not make the lineage read more expensive than point 5 of
    the same order just made it cheaper. A slug the catalog does not carry — a branch whose
    directory was deleted — reads as `new` with no stamp, which is what the row should say.
    """
    row = registry_rows.get(slug) or {}
    return {"state": str(row.get("state") or "new"), "updated": str(row.get("updated") or "")}


def branch_row(base: dict, parent_dir: Path, registry_rows: dict) -> dict:
    """One branch's lineage row: its structure (slug, name, fork turn) plus what became of it.

    `base` is what the route already composed from the config — slug, name, turn, forked — and
    this adds the three fields the rail needs. Composed rather than replaced, so a reader of the
    old shape keeps working: the route's answer gains keys and loses none.
    """
    slug = str(base.get("slug") or "")
    return {**base, **live_fields(registry_rows, slug),
            "handed_back": handed_back(parent_dir, slug)}
