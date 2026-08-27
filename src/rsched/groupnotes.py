"""Intra-group notes — the light channel between routines that are already teammates (F335).

Members of a group are a team with a shared purpose, but one member reaching another went
through the same `report` machinery as reaching a stranger: a ledger row, a delivery into the
target's `inbox/`, and an open maintenance item on the Messages page until somebody closes it.
For teammates coordinating inside one chain that is heavyweight — it turns "here is the file I
staged for you" into a tracked work item a human has to close.

A NOTE is coordination. A REPORT is work somebody must act on, tracked until answered. That
distinction is the whole design, and `report` keeps its meaning unchanged.

**No approval, no ledger row, no Messages-page item.** The safety argument is the BOUNDARY, not
a gate: a note lives in the group's own shared store (`.control/group-stores/<gid>/`, D67),
which is injected into every member's fs roots and nobody else's. A non-member cannot write one
and cannot read one — reaching outside the group is not something this channel refuses, it is
something it cannot express. That is precisely why it may be approval-free, and why the
membership check below is load-bearing rather than decorative.

The engine owns the convention so it is not a filesystem protocol each group reinvents:

    <group-store>/notes/<to-slug>/<note-id>.json      {"from": slug, "ts": iso, "text": …}

A member WRITES one with an ordinary file write (the store is already writable to it — no new
action kind). The engine READS them at boot, renders them into the state digest, and DELETES
them once read, mirroring how `inbox/` drains: a note is delivered exactly once, and a run that
crashes after reading loses a note the same way it loses any other boot-time delivery. Delivery
never starts a run — the sibling picks its notes up when it next runs, which for a group chain
is the same pass or the next one.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from .groups import list_groups, member_slugs, store_dir
from .ids import now_iso, run_ts
from .paths import atomic_write_json, read_json

log = logging.getLogger("rsched.groupnotes")

NOTES_DIRNAME = "notes"
# What one boot surfaces. A note is a nudge, not a mailbox: past this the run is being handed a
# backlog it will not read, and the oldest are the least likely to still matter.
MAX_NOTES_SHOWN = 20
TEXT_CAP = 2000


def notes_dir(store: Path, to_slug: str) -> Path:
    return store / NOTES_DIRNAME / to_slug


def shared_group(routines_home: Path, a: str, b: str) -> str | None:
    """The id of a group holding BOTH slugs, or None. The membership check the whole
    approval-free argument rests on — a note may only ever cross between teammates.
    """
    for g in list_groups(routines_home):
        members = member_slugs(g)
        if a in members and b in members:
            return str(g["id"])
    return None


def write_note(routines_home: Path, *, sender: str, to: str, text: str) -> Path:
    """File a note from `sender` for sibling `to`. Raises ValueError when they share no group —
    the boundary IS the safety model, so crossing it is an error, never a silent drop.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("a note needs text")
    if sender == to:
        raise ValueError("a note goes to a SIBLING — write your own state to your own dir")
    gid = shared_group(routines_home, sender, to)
    if gid is None:
        raise ValueError(f"{sender!r} and {to!r} share no group — an intra-group note cannot "
                         "leave the group. Use a report to reach a routine outside it.")
    d = notes_dir(store_dir(routines_home, gid), to)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"note-{run_ts()}-{uuid.uuid4().hex[:6]}.json"
    atomic_write_json(path, {"from": sender, "ts": now_iso(), "text": text[:TEXT_CAP]})
    log.info("groupnotes: %s -> %s in %s", sender, to, gid)
    return path


def drain(routines_home: Path, slug: str) -> list[dict]:
    """Every note waiting for `slug`, oldest first, REMOVED as it is read.

    Read-and-drop, like `inbox/`: a note is delivered exactly once. Reading is the delivery, so
    a run that dies after boot loses its notes — the same trade `inbox/` already makes, and the
    right one here: a note that survived would be re-shown every run until someone deleted it by
    hand, which is the tracked-work-item shape this channel exists to avoid.
    """
    out: list[dict] = []
    for store in _stores_for(routines_home, slug):
        d = notes_dir(store, slug)
        if not d.is_dir():
            continue
        for path in sorted(d.glob("note-*.json")):
            rec = read_json(path)
            path.unlink(missing_ok=True)
            if isinstance(rec, dict) and str(rec.get("text") or "").strip():
                out.append({"from": str(rec.get("from") or "?"), "ts": str(rec.get("ts") or ""),
                            "text": str(rec["text"])})
    return out


def _stores_for(routines_home: Path, slug: str) -> list[Path]:
    return [store_dir(routines_home, str(g["id"])) for g in list_groups(routines_home)
            if slug in member_slugs(g)]


def digest_section(routines_home: Path, slug: str) -> str:
    """The state-digest block for this run's waiting notes — "" when there are none.

    Called ONCE per run, at boot, because it DRAINS.
    """
    notes = drain(routines_home, slug)
    if not notes:
        return ""
    shown, dropped = notes[:MAX_NOTES_SHOWN], max(0, len(notes) - MAX_NOTES_SHOWN)
    lines = [f"- from {n['from']} ({n['ts']}): {n['text']}" for n in shown]
    tail = (f"\n({dropped} older note(s) were dropped unread — this channel is a nudge between "
            "teammates, not a mailbox.)" if dropped else "")
    return ("NOTES FROM YOUR GROUP (teammates coordinating — read once, now gone; they are NOT "
            "tracked anywhere and nobody is waiting on a reply):\n" + "\n".join(lines) + tail)


def contract_line(routines_home: Path, slug: str) -> str:
    """How this run tells a SIBLING something — named in the harness contract beside the store
    root, because a channel a run does not know about is a channel that does not exist.
    Lists the actual siblings: "write to a member" is not actionable without their slugs.
    """
    siblings = sorted({m for g in list_groups(routines_home) if slug in member_slugs(g)
                       for m in member_slugs(g) if m != slug})
    if not siblings:
        return ""
    return (f"\nTo tell a group member something, write a note for them: a JSON file "
            f'{{"from": "{slug}", "text": "…"}} at '
            f"<group-store>/{NOTES_DIRNAME}/<their-slug>/note-<anything>.json. Their next run "
            f"reads it once and it is gone — no approval, no tracking, nobody waiting on a "
            f"reply. Your group members: {', '.join(siblings)}. Use it for coordination "
            f"('I staged X for you'); use `report` when someone must ACT on a problem and it "
            f"has to be tracked until they answer.")
