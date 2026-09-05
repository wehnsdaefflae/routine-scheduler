"""A run's finish summary as an ITEM — the last thing each routine told you.

Operator order 2026-09-05: "make summaries be messages to the user that surface on the messages
subpage and remove the summary subpage". A finish summary IS a message — it is the one text a
routine writes for a person to read — and it had a page of its own that nothing else linked to,
while the Messages page next door already was the index of everything the instance has to say.

So this is a read model, not a store. It shapes what `registry.scan` already carries into the
same dict `readmodels/items.py` produces, and `api_items` merges the two before filtering. Kept
in its own module for two reasons: items.py is at the ~350-line house cap, and its four inputs are
the maintenance record (`report.json`, the changelog, the answered markers, the reports ledger) —
a run's summary is a fifth source of a completely different kind, and folding it in would blur
what that module is the authority on.

Two shape decisions worth keeping:

- **The id is the RUN id** (`<slug>:<ts>`), not a new `S<n>` namespace. There is no counter, no
  ledger and no writer to own one — and `S1` would collide visually with the stopping-condition
  accounting `[s1] met — …` that appears verbatim inside summary prose.
- **Latest per routine, not every run.** The read-marker is a WATERMARK (`{slug: newest run
  seen}`), which only works while one row per routine is shown; 31 rows of 1.6–4.6 KB is also the
  difference between a page and 1.7 MB of scrollback.

`status` reuses the existing vocabulary rather than forking it (docs/items.md is explicit that a
synonym forks it): `open` while unread, `settled` once dismissed. That is what makes the page's
existing `status=open,in_progress` default land exactly on unread summaries, reproducing the old
page's Unread-by-default behaviour with no new machinery.
"""

from __future__ import annotations

from pathlib import Path

from .. import registry
from ..config import ServerConfig
from ..paths import atomic_write_json, read_json

#: The watermark store — kept at its old path, shape and meaning. Renaming it would have bought a
#: one-shot migration for no benefit; nothing else reads or writes it.
READ_MARKER = Path(".control") / "summary-read.json"


def read_marker_path(routines_home: Path) -> Path:
    return routines_home / READ_MARKER


def _read_map(routines_home: Path) -> dict:
    data = read_json(read_marker_path(routines_home))
    return data if isinstance(data, dict) else {}


def latest_with_summary(info: registry.RoutineInfo) -> registry.RunInfo | None:
    """The run whose finish message we surface: the newest run that actually carries a summary
    (a still-running or summary-less run is not a "latest finish message"), falling back to the
    newest run of any state so a fresh routine still shows a row.
    """
    runs = info.runs or []
    if not runs:
        return None
    for r in runs:                       # info.runs is newest-first
        if (r.summary or "").strip():
            return r
    return runs[0]


def build(server: ServerConfig) -> list[dict]:
    """One item-shaped row per routine that has ever run, newest first."""
    read_map = _read_map(server.routines_home)
    rows: list[dict] = []
    for slug, info in registry.scan(server).items():
        last = latest_with_summary(info)
        if last is None:
            continue
        rows.append({
            "id": last.run_id,
            "type": "summary",
            # `open` = unread, `settled` = dismissed. A summary is never in_progress, addressed
            # or dropped: nobody works on it, they read it.
            "status": "settled" if read_map.get(slug) == last.run_id else "open",
            "title": info.cfg.name or slug,
            "detail": last.summary or "",
            "origin": {"routine": slug, "run_id": last.run_id, "ts": last.ts, "commit": ""},
            "addressed": [], "evidence": [], "refs": [],
            "archive_only": False,
            # what the run itself concluded, so the card can say more than "it ran"
            "outcome": last.outcome or "",
            "run_state": last.state,
            "updated": last.updated or last.ts,
        })
    rows.sort(key=lambda r: r["updated"], reverse=True)
    return rows


def mark_read(routines_home: Path, run_id: str, *, read: bool) -> str:
    """Dismiss (or un-dismiss) the summary identified by its RUN id. Returns the slug.

    The store is keyed by slug because it is a watermark: marking a run read means "I have seen
    this routine up to here", so a newer run resurfaces on its own without anything clearing the
    old marker.
    """
    slug = run_id.split(":", 1)[0]
    read_map = _read_map(routines_home)
    if read:
        read_map[slug] = run_id
    else:
        read_map.pop(slug, None)
    path = read_marker_path(routines_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, read_map)
    return slug


def mark_all_read(routines_home: Path, server: ServerConfig) -> int:
    """Dismiss every currently-shown summary at once. Returns how many were newly dismissed.

    Carried across from the old page, where it exists because of a shipped finding (F303): with
    one row per routine and no bulk action, clearing a backlog was 31 clicks.
    """
    read_map = _read_map(routines_home)
    changed = 0
    for row in build(server):
        slug = row["origin"]["routine"]
        if read_map.get(slug) != row["id"]:
            read_map[slug] = row["id"]
            changed += 1
    if changed:
        path = read_marker_path(routines_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, read_map)
    return changed
