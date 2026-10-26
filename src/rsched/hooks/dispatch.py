"""DISPATCH — the one evaluator that answers "what fires at this point", for every mechanism.

`hooks/points.py` is the vocabulary; this is the seam. It exists so that a mechanism firing at
a hook point goes through ONE place that knows the point's rules, instead of each layer
reaching into the turn loop at a moment of its own — which is what `assist.py`, `remind.py`,
`degrade.py`, `finishgate.py` and `autoask.py` each do today, and the reason the operator's
question ("are hooks fully implemented?") could only be answered by reading the loop.

## What it does, and the two things it refuses to do

`at(point, candidates, fires)` takes the point, the hooks offering to fire there, and the
per-candidate decision, and returns the ones that fire. Three invariants hold whatever the
mechanism:

1. **A candidate whose payload the point does not allow never fires.** That is the coupling
   `points.allows` carries: at `pre-action` the action is already emitted, so only a HOLD can
   reach the model; at a free point there is no action to stop. A layer that passed the wrong
   payload used to be a silent no-op — here it is dropped by the table, and tested.
2. **A predicate that raises never fails the turn.** The caller's `fires` is wrapped; an
   exception means "did not fire". This is `assist._fire`'s own discipline, promoted to the
   seam: a hook layer live in every run must not be able to break one.
3. **A point this build does not know fires nothing.** Lenient, like `hooks.from_record` and
   for the same reason: the hook store is shared by mechanisms being migrated one at a time,
   so a row or a caller from a newer build must be inert rather than fatal.

What dispatch does NOT do, deliberately: it does not RENDER (every mechanism's wording is its
own and is prompt surface), it does not DELIVER (the carrier belongs to the call site — an
observation tail, an engine note, a held turn), and it does not keep the fire guards (a
once-per-run budget is the layer's own state, and a shared one would let one layer spend
another's). It decides which candidates fire, and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from . import points


def at[T](point: str, candidates: Iterable[T], fires: Callable[[T], bool], *,
          payload: Callable[[T], str] | None = None) -> list[T]:
    """The candidates that FIRE at `point`, in order.

    `fires` is the caller's own decision for one candidate (its predicate, its pattern match)
    and may raise — a raising predicate means "did not fire", never a failed turn. `payload`
    says what a candidate would do, so the point's own table can drop one it cannot carry;
    omitted, the payload check is skipped (the caller has already narrowed by moment).
    """
    p = points.point(point)
    if p is None:
        return []
    out: list[T] = []
    for candidate in candidates:
        if payload is not None and not points.allows(point, payload(candidate)):
            continue
        try:
            if not fires(candidate):
                continue
        except Exception:  # noqa: S112 — nothing to log TO: a hook layer live in every run must
            continue       # never fail one, and the caller's own predicate owns its diagnosis
                           # (assist._fire does the same, and its tally is where a dead one shows)
        out.append(candidate)
    return out


def holds(point: str) -> bool:
    """Can a hook firing at this point change what runs? The registry's rule 2, asked once."""
    p = points.point(point)
    return bool(p and p.holds)


def live(point: str) -> bool:
    """Does any mechanism fire at this point in this build?

    The predicate behind the honest half of "are hooks fully implemented": a point in the table
    with nothing firing at it is a named gap, not a feature.
    """
    p = points.point(point)
    return bool(p and p.live)
