"""BACKGROUNDED MUTATIONS, ordered by the targets actions declare (D118 phase 3).

Phase 1 refused every mutation the moment `background: true` rode it, for one reason, and the
reason was sound: *a later synchronous action would read state the deferred one has not written
yet*. The operator answered phase 3 with "widen BACKGROUNDABLE_KINDS to mutations behind a
dependency / explicit-barrier model", so the refusal is replaced by a mechanism that makes that
stale read IMPOSSIBLE rather than merely unlikely:

* a backgrounded mutation DECLARES what it will write — in the field the action already
  carries, `path` for the file kinds and `name` for `memory_write`. Nothing new is asked of the
  caller, so no new action field exists for this: `ACTION_SCHEMA` gains nothing but the
  `background` field's own prose, which had to say what may now be deferred and what the
  ordering promise is (`engine/schema_background.py` — the field moved to its own module
  because `actionschema.py` may only shrink);
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

**One file, two spellings, is ONE target.** A path target is resolved against the directory the
run's relative paths resolve in (`run_context.work_dir`, passed in as `base`) before anything is
compared, because a run that defers `write_file state/x.json` and reads the absolute path back
two turns later is reading the same file — and comparing the strings as declared would miss it,
which is precisely the stale read this module exists to prevent. A `memory_write` target is a
TOPIC in the memory namespace and is deliberately NOT resolved: anchoring it on a directory
would make a file of the same name depend on it.

**The bias is otherwise deliberately conservative.** Where a target cannot be classified — an
argument in a script's or a util's argv, which may be a path or any other word — BOTH readings
are compared, so an unrelated word that happens to match costs one wait that was not needed,
never a stale read. The opposite bias would cost correctness, which is the thing phase 1
refused to spend.

**What this model cannot order, it refuses.** A `then_script` may not ride a deferred change:
the thread dispatches the KIND (`background._runner` → `executor.dispatch`), while the fusion
lives in `actionroute._route`, so a deferred pair would land the write and drop the script in
silence with D39's call-time secret gate never asked — F633's exact shape. It is refused inside
the schema-retry cycle instead (`engine/thenscript.problems`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .run_context import work_dir

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
#: because a riding script is dispatched as the script action it is (engine/thenscript), so a
#: SYNCHRONOUS pair can read a file some OTHER deferred write owns and has to be held for it.
#: (The pair may not itself be deferred — `thenscript.problems` refuses that.)
_PATH_FIELDS = ("path", "src", "dst")
_LIST_FIELDS = ("paths", "files", "args", "then_script")


def _resolve(target: str, base: str | None) -> str:
    """A path compared the way two spellings of ONE file have to compare.

    A run writes `state/x.json` and reads it back two turns later as an absolute path, or the
    other way round — one file, two spellings. Comparing the strings as declared, they miss
    each other, and that miss is exactly the STALE READ this module exists to make impossible,
    so a relative path is anchored on `base` (`run_context.work_dir`, where the run's relative
    paths resolve) before it is compared.
    """
    if base and not Path(target).is_absolute():
        target = str(Path(base) / target)
    # `normpath` and NOT `Path.resolve()`: this is a LEXICAL comparison of two spellings, and
    # resolve() touches the filesystem (following symlinks, and for a file that does not exist
    # yet — which a deferred write's target routinely is).
    return os.path.normpath(target)


def base_for(ctx) -> str | None:
    """Where this run's relative paths resolve, for `_resolve` — or None if the context cannot say.

    Tolerant on purpose: the barrier is asked about EVERY action from the single point all
    routing passes through, and the file kinds are driven by lighter context stand-ins too. One
    that carries no routine dir yields None, and targets are then compared as written — never a
    crash in the one place that cannot afford one.
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
    """The form a claim is STORED and compared in: a `path` resolved, a `name` left alone.

    The two targets live in different namespaces and must not be made to collide: `path` is a
    file, so it is anchored on the working directory, while `memory_write`'s `name` is a TOPIC
    in the memory namespace — resolving it would make a file of the same name depend on it.
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
                # An argument may be a path or any other word, and which it is cannot be known
                # here — so BOTH readings are compared. The cost of the wrong one is a wait that
                # was not needed; the cost of guessing is a stale read.
                if isinstance(item, str) and item.strip():
                    out |= {_resolve(item, base), _resolve(item, None)}
    # `name` is a NAMESPACE, not a path: a util's name, a memory note's topic, a rule's slug.
    # It is included so `memory_read quirks` depends on a deferred `memory_write quirks`.
    name = action.get("name")
    if isinstance(name, str) and name.strip():
        out.add(name)
    return out


def touches(action: dict, target: str, *, base: str | None = None) -> bool:
    """Would `action` read or write `target`? The question the barrier asks of every action.

    `target` is a claim in the form `claim` recorded it (`_canonical`), so this compares two
    canonical spellings and never re-anchors the claim it was handed.
    """
    return target in _mentions(action, base)


def _read_claims(loop) -> dict[str, str]:
    """Handle → the target that handle's deferred mutation owns, for a READER.

    Reading never writes. The barrier is asked about EVERY action from `actionroute`, which
    several callers reach with no loop at all — a direct dispatch in a test, a slash command —
    and the first cut of this module set the registry as an attribute from this path, so merely
    ASKING raised `AttributeError: 'NoneType' object has no attribute '_background_targets'`
    and turned the whole dispatch into an engine_error. An empty mapping is the right answer to
    "what does this loop have in flight" for a loop that cannot have anything in flight.
    """
    claims = getattr(loop, "_background_targets", None)
    return claims if isinstance(claims, dict) else {}


def _writable_claims(loop) -> dict[str, str] | None:
    """The registry to RECORD into, created on first claim — or None if `loop` cannot hold one.

    It creates the attribute rather than standing in for it: an empty dict is falsy, and
    `background._pendings` records what the `or []` version of this mistake cost that module
    (every claim appended to a throwaway).
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

    Called by `actionroute` right after `background.start` returned the handle — not from
    inside `start`, which is in a file with no ratchet headroom. `base` is where the run's
    relative paths resolve, so the claim is stored in the one spelling every later action is
    compared against.
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

    This is the IMPLICIT barrier, and it is why widening the allowlist is safe: the ordering
    contract no longer rests on the run remembering to `wait`. Returns the handles waited on,
    in the order they were started, so the caller can say what it held the turn on; an empty
    list means nothing was delayed, which is the common case and must stay cheap.

    Joining the thread is the whole wait: the result is delivered by `background.collect` at
    the turn boundary exactly as before, so a dependency changes WHEN the later action runs and
    nothing about how the observation arrives.
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

    Only a deferred-against-deferred pair is refused. A SYNCHRONOUS write to the same target is
    fine — `await_dependencies` orders it behind the deferred one, which is a defined order. Two
    deferred writes have none, and picking one silently is how a run loses a write it believes
    it made.
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
