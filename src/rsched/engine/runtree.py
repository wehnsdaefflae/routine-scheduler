"""RUN-TREE hook addressing — messages between a run and its children (D152).

Adds NO action kind and NO store: a run-tree hook is the `report` action with a run-tree
`target`, recorded in `.control/hooks.jsonl` and delivered through the inbox.

- **CHILD→PARENT, mid-flight:** `target: "parent"`.
- **PARENT→CHILD, after the spawn:** `target: "child:<label>"`.

SIBLING→SIBLING stays closed ON PURPOSE: *the run tree is the only addressing scheme*. A child
names `parent` and the parent re-addresses downward, so no run reaches a run it does not own.

Nothing blocks, polls or wakes anyone: the addressee picks freight up at its next turn boundary
(`control.drain_injections`), so a messaged child stays killable and buys no turns. `resolve`
refuses an exited child, which would never drain again.

A run-tree hook is NOT an `R<n>` ledger row: both parties are gone in minutes, so a row would
sit open forever. It gets a hook row (`hk-…`) and no `R` id.
"""

from __future__ import annotations

from pathlib import Path

from ..hooks import addressee as hook_addressee
from . import inbox

#: The delivery channel. On `inbox.MACHINE_VIAS`, so a hook from another run is never counted
#: as the user talking to this one (`ctx.user_replies`, the `user-spoke` assist predicate).
VIA = "hook"

#: The addressee a CHILD may name: its parent. Bare, like the design's `parent`.
PARENT = "parent"
#: The prefix a PARENT uses for one of its own children: `child:<label>`.
CHILD = "child:"


def is_runtree(target: str) -> bool:
    """Is this `report` target an address in the run tree rather than a routine slug?

    A routine slug can never collide with either spelling: `parent` is a reserved word checked
    against the live tree below, and `child:` carries a colon, which `ids.is_slug` rejects.
    """
    t = str(target or "").strip()
    return t == PARENT or t.startswith(CHILD)


def parent_dir(run_dir: Path) -> Path:
    """The directory whose `inbox/` the PARENT of the run at `run_dir` drains.

    A child's `routine.dir` IS its `sub/<n>/` run dir, so the answer differs by depth:

    - a depth-1 child sits at `<routine>/runs/<ts>/sub/<n>`; its parent is the ROUTINE;
    - a deeper child sits at `<parent sub dir>/sub/<m>`; its parent is that sub dir.

    The fixed `runs` component of the layout tells the two apart.
    """
    base = run_dir.parent.parent          # drop `sub/<n>`
    return base.parent.parent if base.parent.name == "runs" else base


def _child_label(target: str) -> str:
    return str(target or "").strip()[len(CHILD):].strip()


def resolve(loop, target: str) -> tuple[Path | None, str, str]:
    """Where a run-tree hook goes: `(directory, addressee, refusal)`.

    Exactly one of `directory` and `refusal` is set. A refusal names what the run CAN address,
    so it can fix the mistake on its next turn.
    """
    ctx = loop.ctx
    target = str(target or "").strip()
    if target == PARENT:
        if ctx.depth == 0:
            return None, "", ("`parent` addresses the run that spawned this one, and this is a "
                              "top-level run — it has no parent. A problem for another routine "
                              "is `target: <slug>`; one for triage is no target at all.")
        return parent_dir(ctx.run_dir), hook_addressee("parent"), ""
    label = _child_label(target)
    subs = list(getattr(loop.subruns, "subruns", {}).values())
    if not label:
        return None, "", ("`child:` needs a label — the one you passed to `child`. "
                          + _live_labels(subs))
    matches = [s for s in subs if s.label == label]
    if not matches:
        return None, "", (f"no child of this run is labelled {label!r}. " + _live_labels(subs)
                          + " A SIBLING is not addressable: name `parent` and let the run that "
                            "holds the labels re-address it (the run tree is the only "
                            "addressing scheme).")
    sub = matches[-1]
    if sub.done.is_set():
        return None, "", (f"child {label!r} has already exited, so it will take no further turn "
                          f"boundary and would never read this. Its hand-back (its finish "
                          f"summary and artifacts/from-sub-{sub.n}/) is what it had to say.")
    return sub.ctx.routine.dir, hook_addressee("child", label), ""


def _live_labels(subs: list) -> str:
    live = [s.label for s in subs if not s.done.is_set()]
    if not live:
        return "This run has no child still running."
    return "Children still running: " + ", ".join(repr(s) for s in live) + "."


def hook_id(run_id: str, turn: int) -> str:
    """The hook's id and inbox filename stem: deterministic, so a retry of the same turn
    REPLACES its own delivery instead of queuing a second copy.
    """
    return f"hk-{run_id}-t{turn}"


def message_text(*, sender: str, title: str, detail: str) -> str:
    """What the addressee reads: WHO is speaking and WHAT is owed, so a run never mistakes a
    hook for its operator.
    """
    head = f"HOOK FROM {sender} — {title}" if title else f"HOOK FROM {sender}"
    body = f"{head}\n\n{detail}" if detail else head
    return (body + "\n\nThis is another run of your own run tree speaking, not the operator. "
                   'Reply by addressing it back (`report` with `target: "parent"` or '
                   '`target: "child:<label>"`); it is not an item and owes the ledger nothing.')


def drain_child(loop) -> None:
    """A CHILD's turn boundary: take the run-tree hooks addressed to it, and nothing else.

    Narrowed to `VIA` on purpose: the operator's channels and the deferred-answer bridge belong
    to the ROOT run (a child holds no `ask_user`), and a wider set would let a child consume
    freight addressed to the routine. Appends, never rewrites, and blocks on nothing.
    """
    ctx = loop.ctx
    for m in inbox.drain_messages(ctx.routine.dir, loop.consumed_dir, vias=(VIA,)):
        inject_message(loop, m)


def inject_message(loop, m: dict) -> None:
    """Put one drained hook into the child's conversation, stamped `VIA`: a hook is NOT the user
    talking, so it must not advance `ctx.user_replies` or count as an intervention.
    """
    from .control import inject_user_message

    inject_user_message(loop, {**m, "via": VIA})


#: The `report` fields that dispose of LEDGER rows. Inert on a run-tree message (it has no row);
#: named back rather than refused, since a refusal costs the turn and disposes of nothing.
DISPOSAL_FIELDS = ("answers", "settles", "supersedes")


def ignored_disposal(disposal: dict | None) -> str:
    """What to tell a sender that named ledger rows on a hook — "" when it named none.

    `closes` is left out: it names no row, and scolding every terminal reply would teach runs
    to stop replying. The other three are named with their ids so the sender knows those rows
    are still open.
    """
    named: list[str] = []
    for field in DISPOSAL_FIELDS:
        value = (disposal or {}).get(field)
        if not value:
            continue
        ids = [value] if isinstance(value, str) else list(value)
        named.append(f"{field} {', '.join(str(i) for i in ids)}")
    if not named:
        return ""
    return (f"IGNORED: {'; '.join(named)} — a run-tree message is a hook, not a ledger row, so "
            "it disposes of nothing. Those rows are still open: dispose of them in a separate "
            "`report` with `target: <slug>` (or no target for triage)")


def send(loop, *, target: str, title: str, detail: str, disposal: dict | None = None) -> dict:
    """File a run-tree hook, or refuse it. The observation for the `report` action, either way.

    Ledger-disposal fields in `disposal` are NAMED back (`ignored_disposal`). Best-effort on the
    WRITE: a failed delivery reports `delivered: False` and never aborts the sending run.
    """
    from ..hooks import store

    ctx = loop.ctx
    directory, address, refusal = resolve(loop, target)
    if directory is None:
        return {"kind": "report", "target": target, "runtree": True, "delivered": False,
                "refused": refusal}
    sender = f"{ctx.routine.slug} run {ctx.run_id}"
    hid = hook_id(ctx.run_id, ctx.turn)
    delivered = True
    try:
        inbox.file_message(directory, message_text(sender=sender, title=title, detail=detail),
                           via=VIA, name=hid,
                           extra={"hook": hid, "from": ctx.run_id, "addressee": address})
    except OSError:
        delivered = False
    store.record_runtree(ctx.server.routines_home, hook_id=hid, routine=ctx.routine.slug,
                         run_id=ctx.run_id, turn=ctx.turn, addressee=address,
                         title=title, delivered=delivered)
    note = ("delivered at that run's next turn boundary; it is a hook, not an item, so "
            "it has no R id and nothing is owed to the ledger")
    # The warning rides the EXISTING note, which `obs_admin` renders verbatim — no new key.
    if warning := ignored_disposal(disposal):
        note = f"{note}. {warning}"
    return {"kind": "report", "target": target, "runtree": True, "delivered": delivered,
            "hook": hid, "addressee": address, "note": note}
