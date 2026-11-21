"""BACKGROUNDED MUTATIONS, ordered by the targets actions declare (D118 phase 3).

A deferred mutation must never let a later action read state it has not written yet:

* a backgrounded mutation DECLARES its target in the field it already carries (`path` for the
  file kinds, `name` for `memory_write`) — no new action field;
* a later action that TOUCHES that target (reads, writes, names it in script/util args or
  `decide` evidence) is HELD until the write lands (`await_dependencies`), so a run that never
  calls `wait` is still correctly ordered; `wait` remains the explicit barrier;
* two DEFERRED writes to one target have no defined order, so the second is refused
  (`refuse_conflict`).

The claim registry lives here, keyed by the handle `background.start` returned, rather than on
`background.Pending` (that file is at its size ceiling); liveness is still read off the
Pending's thread. A cancelled call leaves `loop._background` and stops holding its target.

A path target is resolved against `run_context.work_dir` (passed as `base`) so a relative and
an absolute spelling of one file compare equal. A `memory_write` target is a TOPIC and is not
resolved. Where an argument cannot be classified as path or word, BOTH readings are compared:
the wrong guess costs a needless wait, never a stale read.

A `then_script` may not ride a deferred change: the thread dispatches only the kind, so the
script and its secret gate would be dropped silently (`engine/thenscript.problems` refuses it).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .run_context import work_dir

#: The mutations a run may defer, and the field each declares its target in. Keep in step with
#: `actions.BACKGROUNDABLE_KINDS`. Deliberately absent: `delete`/`move`/`mkdir` (not slow, and
#: `move` has two ends); `write_util`/`write_rule` (gated in `actionroute._route`, not reachable
#: from `background._runner` — a DISPATCH entry would bypass that gate).
TARGET_FIELD: dict[str, str] = {
    "write_file": "path",
    "edit_file": "path",
    "memory_write": "name",
}

#: Where an action names the things it READS or WRITES. `then_script` is included: a synchronous
#: write+script pair can read a file another deferred write owns.
_PATH_FIELDS = ("path", "src", "dst")
_LIST_FIELDS = ("paths", "files", "args", "then_script")


def _resolve(target: str, base: str | None) -> str:
    """A path compared the way two spellings of ONE file have to compare (relative → `base`)."""
    if base and not Path(target).is_absolute():
        target = str(Path(base) / target)
    # `normpath`, not `resolve()`: a lexical comparison; the target often does not exist yet.
    return os.path.normpath(target)


def base_for(ctx) -> str | None:
    """Where this run's relative paths resolve, for `_resolve` — or None if the context cannot say.

    Tolerant on purpose: every action passes through here, including from lighter context
    stand-ins; without a routine dir targets are compared as written rather than crashing.
    """
    try:
        return str(work_dir(ctx))
    except AttributeError:
        return None


def target_of(action: dict) -> str | None:
    """What this action will WRITE, as DECLARED, if it is a mutation naming one target."""
    field = TARGET_FIELD.get(str(action.get("kind")))
    if not field:
        return None
    value = action.get(field)
    return str(value) if isinstance(value, str) and value.strip() else None


def _canonical(action: dict, base: str | None) -> str | None:
    """The form a claim is STORED and compared in: a `path` resolved, a `name` (a memory topic,
    a different namespace) left alone.
    """
    field = TARGET_FIELD.get(str(action.get("kind")))
    target = target_of(action)
    if target is None:
        return None
    return _resolve(target, base) if field == "path" else target


def _mentions(action: dict, base: str | None) -> set[str]:
    """Every target string this action names, however it names it."""
    out: set[str] = set()
    for field in _PATH_FIELDS:
        value = action.get(field)
        if isinstance(value, str) and value.strip():
            out.add(_resolve(value, base))
    for field in _LIST_FIELDS:
        value = action.get(field)
        if isinstance(value, (list, tuple)):
            for item in value:
                # Path or plain word cannot be known here, so BOTH readings are compared.
                if isinstance(item, str) and item.strip():
                    out |= {_resolve(item, base), _resolve(item, None)}
    # `name` is a namespace, not a path: `memory_read x` depends on a deferred `memory_write x`.
    name = action.get("name")
    if isinstance(name, str) and name.strip():
        out.add(name)
    return out


def touches(action: dict, target: str, *, base: str | None = None) -> bool:
    """Would `action` read or write `target`? `target` is already canonical (`_canonical`)."""
    return target in _mentions(action, base)


def _read_claims(loop) -> dict[str, str]:
    """Handle → the target that handle's deferred mutation owns, for a READER.

    Reading never writes: callers may pass no loop at all (a direct dispatch, a slash command),
    and such a loop has nothing in flight.
    """
    claims = getattr(loop, "_background_targets", None)
    return claims if isinstance(claims, dict) else {}


def _writable_claims(loop) -> dict[str, str] | None:
    """The registry to RECORD into, created on first claim — or None if `loop` cannot hold one.

    It creates the attribute rather than returning a stand-in, or claims land in a throwaway.
    """
    if _read_claims(loop):
        return loop._background_targets
    try:
        loop._background_targets = {}
    except AttributeError:
        return None          # a loop that cannot carry claims also cannot have started one
    return loop._background_targets


def claim(loop, action: dict, obs: dict, *, base: str | None = None) -> None:
    """Record what the call just started in the background will write.

    Called by `actionroute` right after `background.start` returned the handle; the claim is
    stored canonical (resolved against `base`).
    """
    handle, target = obs.get("handle"), _canonical(action, base)
    if handle and target and (claims := _writable_claims(loop)) is not None:
        claims[str(handle)] = target


def _live(loop) -> list[Any]:
    """The pending calls still running. A cancelled one has left the list and holds nothing."""
    return [p for p in (getattr(loop, "_background", None) or []) if not p.done]


def holders(loop, action: dict, *, base: str | None = None) -> list[str]:
    """The handles of deferred mutations whose target this action touches."""
    claims = _read_claims(loop)
    if not claims:
        return []                                   # the common case, and it stays cheap
    return [p.handle for p in _live(loop)
            if p.handle in claims and touches(action, claims[p.handle], base=base)]


def await_dependencies(loop, action: dict, *, base: str | None = None) -> list[str]:
    """Hold `action` until every deferred write it depends on has landed; return their handles.

    The IMPLICIT barrier. Handles come back in start order; empty (the common, cheap case)
    means nothing was delayed. Joining the thread is the whole wait — the result is still
    delivered by `background.collect` at the turn boundary.
    """
    waited: list[str] = []
    for handle in holders(loop, action, base=base):
        pending = next((p for p in (getattr(loop, "_background", None) or [])
                        if p.handle == handle), None)
        if pending is None:
            continue
        pending.thread.join()
        waited.append(handle)
    return waited


def refuse_conflict(loop, action: dict, *, base: str | None = None) -> dict | None:
    """Refuse a SECOND deferred write to a target a live deferred write already owns.

    A synchronous write is fine (`await_dependencies` orders it); two deferred ones have no
    defined order, so one could silently lose.
    """
    if not action.get("background") or not (target := target_of(action)):
        return None
    claims = _read_claims(loop)
    if not claims:
        return None
    canonical = _canonical(action, base)
    for pending in _live(loop):
        if claims.get(pending.handle) == canonical:
            return {
                "kind": action["kind"], "refused": True,
                "error": f"`{pending.handle}` is already writing `{target}` in the background, "
                         f"and two deferred writes to one target have no defined order — the "
                         f"second could land first and lose yours. Either wait for it "
                         f"(`wait`) and then make this change, or run this one "
                         f"synchronously by dropping 'background'",
            }
    return None
