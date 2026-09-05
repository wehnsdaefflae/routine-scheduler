"""Intra-domain notes — the light channel between routines that are already teammates (F335).

Members of a domain are a team with a shared surface, but `report` treats one member reaching
another exactly as it treats reaching a stranger: a ledger row, a delivery into the target's
`inbox/`, and an open maintenance item on the Messages page until somebody closes it. For
teammates coordinating over one shared store that is heavyweight — it turns "here is the file
I staged for you" into a tracked work item a human has to close.

A NOTE is coordination. A REPORT is work somebody must act on, tracked until answered. That
distinction is the whole design — `report` stays the channel for the second kind.

**No approval, no ledger row, no Messages-page item.** The safety argument is the BOUNDARY, not
a gate: a note lives in the domain's own shared store (`.control/group-stores/<domain-id>/`,
D67 — the directory name is frozen on purpose, see `domains.STORES_DIRNAME`), which is
injected into every member's fs roots and nobody else's. A non-member cannot write one and
cannot read one — reaching outside the domain is not something this channel refuses, it is
something it cannot express. That is precisely why it may be approval-free and why the
membership check below is load-bearing rather than decorative.

This is also why the DOMAIN carries both the shared config and the shared store rather than
those being two objects (docs/lanes-domains.md): split them and the boundary this channel rests
on stops being the same thing as "close enough to share a config", so the argument above stops
holding. It is deliberately NOT the lane: routines that merely fire in sequence are not thereby
allowed into each other's files.

The engine owns the convention so it is not a filesystem protocol each domain reinvents:

    <domain-store>/notes/<to-slug>/<note-id>.json     {"from": slug, "ts": iso, "text": …}

A member WRITES one with an ordinary file write (the store is already writable to it — no new
action kind). The engine READS them at boot, renders them into the state digest, and DELETES
them once read, mirroring how `inbox/` drains: a note is delivered exactly once, so a run that
crashes after reading loses a note the same way it loses any other boot-time delivery. Delivery
never starts a run — the sibling picks its notes up when it next runs, which for two members of
one lane is the same pass or the next one.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .domains import members as domain_members
from .domains import store_dir
from .paths import read_json, read_yaml

log = logging.getLogger("rsched.domainnotes")

NOTES_DIRNAME = "notes"
# What one boot surfaces. A note is a nudge, not a mailbox: past this the run is being handed a
# backlog it will not read; the oldest are the least likely to still matter.
MAX_NOTES_SHOWN = 20
TEXT_CAP = 2000


def notes_dir(store: Path, to_slug: str) -> Path:
    return store / NOTES_DIRNAME / to_slug


def drain(routines_home: Path, slug: str) -> list[dict]:
    """Every note waiting for `slug`, oldest first, REMOVED as it is read.

    Read-and-drop, like `inbox/`: a note is delivered exactly once. Reading is the delivery, so
    a run that dies after boot loses its notes — the same trade `inbox/` already makes and the
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
                # The cap belongs HERE, on the read, because reading is the only half this
                # module owns: a note is written by the sibling routine itself, with an ordinary
                # file write into the shared store (`contract_line` below hands it the literal
                # path). Nothing validates that write, so a note of any length would otherwise
                # go straight into the reader's state digest and its prompt.
                out.append({"from": str(rec.get("from") or "?"), "ts": str(rec.get("ts") or ""),
                            "text": str(rec["text"])[:TEXT_CAP]})
    return out


def _domain_of(routines_home: Path, slug: str) -> str:
    """This routine's domain id, read from its own routine.yaml — "" when it has none.

    Every unreadable shape is "no domain", a leniency that is load-bearing: this runs at
    every run's BOOT, so an exception over a routine.yaml caught mid-save would stop prompt
    composition entirely where the miss costs one note.
    """
    cfg = Path(routines_home) / slug / "routine.yaml"
    if not cfg.is_file():
        return ""
    try:
        raw = read_yaml(cfg, {})
    except (OSError, yaml.YAMLError):    # a broken file has no domain
        return ""
    return str(raw.get("domain") or "") if isinstance(raw, dict) else ""


def _stores_for(routines_home: Path, slug: str) -> list[Path]:
    """Zero or one, because a routine has at most one domain — and the boundary IS the safety
    model here. A note cannot leave the domain because the domain's store is in its members'
    fs roots and nobody else's, which is the whole reason this channel needs no approval.
    """
    did = _domain_of(routines_home, slug)
    return [store_dir(routines_home, did)] if did else []


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
    return ("NOTES FROM YOUR DOMAIN (teammates coordinating — read once, now gone; they are NOT "
            "tracked anywhere and nobody is waiting on a reply):\n" + "\n".join(lines) + tail)


def contract_line(routines_home: Path, slug: str) -> str:
    """How this run tells a SIBLING something — named in the harness contract beside the store
    root, because a channel a run does not know about is a channel that does not exist.
    Lists the actual siblings: "write to a member" is not actionable without their slugs.
    """
    did = _domain_of(routines_home, slug)
    siblings = [m for m in domain_members(routines_home, did) if m != slug] if did else []
    if not siblings:
        return ""
    return (f"\nTo tell one of them something, write a note for them: a JSON file "
            f'{{"from": "{slug}", "text": "…"}} at '
            f"<shared-store>/{NOTES_DIRNAME}/<their-slug>/note-<anything>.json. Their next run "
            f"reads it once and it is gone — no approval, no tracking, nobody waiting on a "
            f"reply. Routines you share a domain with: {', '.join(siblings)}. Use it for "
            f"coordination "
            f"('I staged X for you'); use `report` when someone must ACT on a problem and it "
            f"has to be tracked until they answer.")
