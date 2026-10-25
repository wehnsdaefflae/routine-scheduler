"""BACKGROUNDED MUTATIONS, ordered by the targets actions declare (D118 phase 3).

Phase 1 refused every mutation the moment `background: true` rode it, for one reason, and the
reason was sound: *a later synchronous action would read state the deferred one has not written
yet*. The operator answered phase 3 with "widen BACKGROUNDABLE_KINDS to mutations behind a
dependency / explicit-barrier model", so the refusal is replaced by a mechanism that makes that
stale read IMPOSSIBLE rather than merely unlikely:

* a backgrounded mutation DECLARES what it will write — in the field the action already
  carries, `path` for the file kinds and `name` for `write_util`/`memory_write`. Nothing new is
  asked of the caller, which is why no new action field exists for this (`ACTION_SCHEMA` is
  unchanged except for the field's prose);
* a later action that TOUCHES that target — reads it, writes it, names it in a script's or a
  util's arguments, hands it to `decide` as evidence — is HELD until the write has landed
  (`await_dependencies`). The barrier closes on the DEPENDENCY, so a run that forgets to
  synchronise still cannot read stale state. That is the half the refusal was protecting;
* the EXPLICIT form stays exactly as it was: `wait` synchronises deliberately, for a run that
  wants everything quiet before it goes on;
* two DEFERRED writes to one target have no defined order — there is no safe way to pick one —
  so the second is refused, naming the handle that holds the target and the call that works
  (`refuse_conflict`).

**Why the registry lives here and not on `background.Pending`.** The natural home for "what
this call writes" is a field on the Pending. `src/rsched/engine/background.py` sits at EXACTLY
its size-ratchet ceiling (474 lines, `tests/size_ratchet.json`), and the house rule is that a
file at its ceiling gains nothing: new code goes into a new module. So the claim is recorded
here, keyed by the handle `background.start` returned, and liveness is still read off the
Pending's own thread — one source of truth for "has it landed", a second for "what it owns".
A cancelled call leaves `loop._background` and therefore stops holding its target; a cancel
that caught a write mid-flight is the caller's own hazard, the same one `kill` already carries.

**The bias is deliberately conservative.** Targets are compared as declared strings (paths
normalised), so a file whose path happens to equal a memory note's name produces a dependency
that is not real — and the cost of that is one wait that was not needed, never a stale read.
The opposite bias would cost correctness, which is the thing phase 1 refused to spend.
"""

from __future__ import annotations

import os
from typing import Any

#: The mutations a run may defer, and the field each declares its target in. Keep this table and
#: `actions.BACKGROUNDABLE_KINDS` in step — a kind in one and not the other is either a mutation
#: that defers with no barrier or a barrier nothing can reach. Deliberately absent:
#:
#: · `delete`, `move`, `mkdir` — none is SLOW, so there is no latency to win, and `move`'s two
#:   ends are not the one target a barrier keys on;
#: · `write_util` and `write_rule` — these look like the best fit of all (a util write runs a
#:   selftest and is genuinely slow) and they are the one case this model CANNOT take yet.
#:   `executor.DISPATCH` has no entry for either: their capability gate lives in
#:   `actionroute._route`, exactly as `script`'s secret gate does, so a backgrounded one would
#:   raise `KeyError` in the thread — and `tests/test_background_actions.py::
#:   test_the_background_runner_matches_the_foreground_routing_table` forbids closing that by
#:   adding a DISPATCH entry, because doing so CEMENTS the gate bypass (F633's lesson). Admitting
#:   them needs a `background._runner` branch and a starting-turn gate of their own, which is the
#:   next step of this phase rather than part of it.
TARGET_FIELD: dict[str, str] = {
    "write_file": "path",
    "edit_file": "path",
    "memory_write": "name",
}

#: Where an action names the things it READS or WRITES. `then_script`'s arguments are included
#: because a script riding a write is dispatched as the script action it is (engine/thenscript),
#: so it can read a file a deferred write owns.
_PATH_FIELDS = ("path", "src", "dst")
_LIST_FIELDS = ("paths", "files", "args", "then_script")


def _norm(target: str) -> str:
    """A path compared the way two spellings of one file should compare."""
    return os.path.normpath(target) if "/" in target or target.startswith(".") else target


def target_of(action: dict) -> str | None:
    """What this action will WRITE, if it is a mutation that declares one target."""
    field = TARGET_FIELD.get(str(action.get("kind")))
    if not field:
        return None
    value = action.get(field)
    return str(value) if isinstance(value, str) and value.strip() else None


def _mentions(action: dict) -> set[str]:
    """Every target string this action names, however it names it."""
    out: set[str] = set()
    for field in _PATH_FIELDS:
        value = action.get(field)
        if isinstance(value, str) and value.strip():
            out.add(_norm(value))
    for field in _LIST_FIELDS:
        value = action.get(field)
        if isinstance(value, (list, tuple)):
            out |= {_norm(item) for item in value if isinstance(item, str) and item.strip()}
    # `name` is a NAMESPACE, not a path: a util's name, a memory note's topic, a rule's slug.
    # It is included so `memory_read quirks` depends on a deferred `memory_write quirks`.
    name = action.get("name")
    if isinstance(name, str) and name.strip():
        out.add(_norm(name))
    return out


def touches(action: dict, target: str) -> bool:
    """Would `action` read or write `target`? The question the barrier asks of every action."""
    return _norm(target) in _mentions(action)


def _claims(loop) -> dict[str, str]:
    """Handle → the target that handle's deferred mutation owns.

    A defaulting accessor that CREATES the attribute rather than standing in for it — an empty
    dict is falsy, and `background._pendings` records what the `or []` version of this mistake
    cost that module (every claim appended to a throwaway).
    """
    if getattr(loop, "_background_targets", None) is None:
        loop._background_targets = {}
    return loop._background_targets


def claim(loop, action: dict, obs: dict) -> None:
    """Record what the call just started in the background will write.

    Called by `actionroute` right after `background.start` returned the handle — not from
    inside `start`, which is in a file with no ratchet headroom.
    """
    handle, target = obs.get("handle"), target_of(action)
    if handle and target:
        _claims(loop)[str(handle)] = _norm(target)


def _live(loop) -> list[Any]:
    """The pending calls still running. A cancelled one has left the list and holds nothing."""
    return [p for p in (getattr(loop, "_background", None) or []) if not p.done]


def holders(loop, action: dict) -> list[str]:
    """The handles of deferred mutations whose target this action touches."""
    claims = _claims(loop)
    return [p.handle for p in _live(loop)
            if p.handle in claims and touches(action, claims[p.handle])]


def await_dependencies(loop, action: dict) -> list[str]:
    """Hold `action` until every deferred write it depends on has landed; return their handles.

    This is the IMPLICIT barrier, and it is why widening the allowlist is safe: the ordering
    contract no longer rests on the run remembering to `wait`. Returns the handles waited on,
    in the order they were started, so the caller can say what it held the turn on; an empty
    list means nothing was delayed, which is the common case and must stay cheap.

    Joining the thread is the whole wait: the result is delivered by `background.collect` at
    the turn boundary exactly as before, so a dependency changes WHEN the later action runs and
    nothing about how the observation arrives.
    """
    waited: list[str] = []
    for handle in holders(loop, action):
        pending = next((p for p in (getattr(loop, "_background", None) or [])
                        if p.handle == handle), None)
        if pending is None:
            continue
        pending.thread.join()
        waited.append(handle)
    return waited


def refuse_conflict(loop, action: dict) -> dict | None:
    """Refuse a SECOND deferred write to a target a live deferred write already owns.

    Only a deferred-against-deferred pair is refused. A SYNCHRONOUS write to the same target is
    fine — `await_dependencies` orders it behind the deferred one, which is a defined order. Two
    deferred writes have none, and picking one silently is how a run loses a write it believes
    it made.
    """
    if not action.get("background") or not (target := target_of(action)):
        return None
    claims = _claims(loop)
    for pending in _live(loop):
        if claims.get(pending.handle) == _norm(target):
            return {
                "kind": action["kind"], "refused": True,
                "error": f"`{pending.handle}` is already writing `{target}` in the background, "
                         f"and two deferred writes to one target have no defined order — the "
                         f"second could land first and lose yours. Either wait for it "
                         f"(`wait`) and then make this change, or run this one "
                         f"synchronously by dropping 'background'",
            }
    return None
