"""RUN-TREE hook addressing — F695 as a case of the one mechanism (D152, step 3).

The operator, 2026-10-18: *"communication between subruns and between subruns and their parent
run is a type of hook as well. maybe even a type of message. so implement hooks first as a single
mechanism unifying a), b) and c) and make sure f695 is a special case of that as well"*. This
module is that special case, and it adds NO action kind and NO store: a run-tree hook is the
`report` action with a run-tree `target`, recorded in `.control/hooks.jsonl` like every other
hook and delivered through the inbox like every other message.

F695 measured three closed seams. Two of them are opened here:

- **CHILD→PARENT, mid-flight.** Before this, a child could only speak at its exit (its finish
  summary and `artifacts/from-sub-<n>/`). A child that learns at turn 5 that its brief is wrong
  had to spend its whole budget before saying so. `target: "parent"`.
- **PARENT→CHILD, after the spawn.** Before this, the `prompt` at spawn time was the parent's
  one and only word to a child. `target: "child:<label>"`.

The third — SIBLING→SIBLING — stays closed ON PURPOSE, and refusing it here is rule 1 of the
design: *the run tree is the only addressing scheme*. A child names `parent`; the parent, which
holds the labels, re-addresses downward. Nothing has to resolve a name outside its own subtree,
so no run can reach a run it does not own.

## Why delivery rides an EXISTING turn boundary and nothing waits

The finding's own constraint: a child that can be messaged mid-flight must stay KILLABLE and
must not extend its own turn budget by waiting. So nothing here blocks, polls or wakes anyone.
The sender files freight and returns in the same turn; the addressee picks it up at the next
turn boundary it was going to take anyway (`control.drain_injections`). A child that never takes
another boundary never reads it — which is why `resolve` refuses a child that has already
exited rather than filing into a directory nobody will drain again.

## Why a run-tree hook is NOT an `R<n>` ledger row

An item in `.control/reports.jsonl` is owed an answer by a ROUTINE, and lives until something
settles it. The parent and the child are both gone in minutes, so a row for one would sit open
forever with no one able to close it — exactly the backlog pathology the item ledger exists to
make visible. A run-tree hook is therefore addressing, not an item: it gets a hook row (`hk-…`)
and no `R` id. The ledger keeps meaning what it meant.
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

    A run drains `routine.dir/inbox/`, and a child's `routine.dir` IS its `sub/<n>/` run dir
    (`childrun._sub_routine`), so the answer differs by depth and that is why it is computed
    here once instead of at the call site:

    - a depth-1 child sits at `<routine>/runs/<ts>/sub/<n>`, and its parent is the ROUTINE
      (`<routine>`), four levels up;
    - a deeper child sits at `<parent sub dir>/sub/<m>`, and its parent is that sub dir itself.

    The `runs` component is what tells the two apart, and it is a fixed part of the layout
    (`registry.run_index`, every run home) rather than a guess.
    """
    base = run_dir.parent.parent          # drop `sub/<n>`
    return base.parent.parent if base.parent.name == "runs" else base


def _child_label(target: str) -> str:
    return str(target or "").strip()[len(CHILD):].strip()


def resolve(loop, target: str) -> tuple[Path | None, str, str]:
    """Where a run-tree hook goes: `(directory, addressee, refusal)`.

    Exactly one of `directory` and `refusal` is set. A refusal is prose for the sending run's
    observation and names what it CAN address, because an addressing mistake is the one error a
    run can fix on its next turn — and cannot fix from "unknown target".
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
        return None, "", ("`child:` needs a label — the one you passed to spawn/subtask. "
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
    """The hook's id, and the inbox filename's stem: deterministic, so a retry of the same
    turn REPLACES its own delivery instead of queuing a second copy (F697's bug class — a
    record that cannot be recognised as already written is delivered twice).
    """
    return f"hk-{run_id}-t{turn}"


def message_text(*, sender: str, title: str, detail: str) -> str:
    """What the addressee reads. It says WHO is speaking and WHAT is owed, because a run that
    cannot tell a hook from its operator answers the wrong party (the mistake
    `inbox.VIAS` was closed to fix).
    """
    head = f"HOOK FROM {sender} — {title}" if title else f"HOOK FROM {sender}"
    body = f"{head}\n\n{detail}" if detail else head
    return (body + "\n\nThis is another run of your own run tree speaking, not the operator. "
                   'Reply by addressing it back (`report` with `target: "parent"` or '
                   '`target: "child:<label>"`); it is not an item and owes the ledger nothing.')


def drain_child(loop) -> None:
    """A CHILD's turn boundary: take the run-tree hooks addressed to it, and nothing else.

    This is the lifted half of F695's first seam. `control.drain_injections` used to return
    immediately for any `ctx.depth > 0`, so a child drained nothing at all — the gate that made
    PARENT→CHILD a one-shot (the `prompt` at spawn time) and left the operator unable to speak
    to a child at all.

    What is drained is narrowed to `VIA` on purpose, rather than opening the LIVE set to
    children:

    - the operator's channels belong to the ROOT run. A child's inbox is its `sub/<n>/` dir,
      which no console writes to; widening the set here would only let a child consume freight
      addressed to the routine, and D92/D93 is exactly that bug (a follow-up leg eating the
      night run's decision answers).
    - the deferred-answer bridge stays the root's too: a child holds no `ask_user`.

    Appends, never rewrites, like every other boundary step — and it blocks on nothing, so a
    messaged child stays killable and buys no extra turn by being reachable (the finding's own
    constraint).
    """
    ctx = loop.ctx
    for m in inbox.drain_messages(ctx.routine.dir, loop.consumed_dir, vias=(VIA,)):
        inject_message(loop, m)


def inject_message(loop, m: dict) -> None:
    """Put one drained hook into the child's conversation. Separate from
    `control.inject_user_message` because a hook is NOT the user talking: it must not advance
    `ctx.user_replies`, must not count as an intervention, and says in its own text who spoke.
    """
    from .control import inject_user_message

    inject_user_message(loop, {**m, "via": VIA})


def send(loop, *, target: str, title: str, detail: str) -> dict:
    """File a run-tree hook, or refuse it. The observation for the `report` action, either way.

    Best-effort on the WRITE, like every other hook: a failed delivery reports `delivered:
    False` and never aborts the sending run, whose real work is elsewhere.
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
    return {"kind": "report", "target": target, "runtree": True, "delivered": delivered,
            "hook": hid, "addressee": address,
            "note": ("delivered at that run's next turn boundary; it is a hook, not an item, so "
                     "it has no R id and nothing is owed to the ledger")}
