"""What a report DISPOSES OF — the value object, its normalization, and its one correction.

A pure value with rules and no I/O, which is why it is its own module beside `report_detail`
(the delivered bound) rather than part of `reports` (identity, locking, lifecycle): the three
have different reasons to change, and the ledger module is at the house size ceiling, where a
shared narrative no longer fits beside the lifecycle it is not about.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Disposal:
    """The one exchange a report answers, the rows it settles, and whether it is itself terminal.

    The three travel together because they are one decision with three faces, and keeping them
    apart is what let them drift: `closes` was a property of `answers` alone, so a reply that
    settled several rows could only close one, and a report that asked nothing back could close
    nothing at all (F497 — measured on this ledger as rows aging at 11 and 12 days a day after
    being genuinely answered, and seven fold-carriers born open, four of them literally saying
    "no reply needed").

    - `answers` — the ONE report id this reply belongs to, the exchange record. Unchanged.
    - `settles` — every id this reply terminally disposes of (D134, operator-selected option C).
    - `closes` — this reply is itself born settled. Valid beside EITHER disposal, and DROPPED
      with neither — explicitly, never silently (see `__post_init__`).
    - `closes_dropped` — a `closes` this value refused to keep, so every surface downstream can
      SAY it was dropped. Derived, never passed by a caller: it is the correction's record, and
      a correction nobody is told about is the silent no-op D196 rejected.
    """

    answers: str = ""
    closes: bool = False
    settles: tuple[str, ...] = ()
    closes_dropped: bool = False

    def __post_init__(self) -> None:
        # Normalize once, here, so every reader downstream compares like with like: the ledger
        # stores ids upper-cased and a caller passing "r123" must not mint a row nothing matches.
        # Both faces: an `answers: "r123"` settled R123 only because each reader re-upper-cased
        # it, while the case-sensitive ones — the card's reference links, `refs` — missed it.
        object.__setattr__(self, "answers", str(self.answers or "").strip().upper())
        object.__setattr__(self, "settles",
                           tuple(str(i).strip().upper() for i in self.settles if str(i).strip()))
        # `closes` is a property OF a disposal: with neither face there is nothing to complete.
        # It is DROPPED here and the drop RECORDED, so every surface downstream can say what
        # happened to it. D196 (the operator 2026-10-18) refused this shape loudly instead:
        # "Refuse it loudly — a report carrying closes with supersedes but no answers/settles
        # is rejected with a message naming what closes needs". What he rejected there was
        # dropping it to False SILENTLY, which accepted the filing and left the sender
        # believing a thread was closed that was not. He later chose the self-applying form:
        # "accept the call, drop `closes`, and tell the run in the observation that the notice
        # was filed as owing nothing back but settled no row". Measured, every one of the eight
        # refusal events wanted exactly that — to declare that its notice owes no answer — and
        # none was trying to settle a row, so the refusal was costing a turn to teach a field
        # nobody wanted. `closes_dropped` is what keeps this from being the silent no-op: the
        # correction is carried, not swallowed, and the run is told.
        if self.closes and not (self.answers or self.settles):
            object.__setattr__(self, "closes", False)
            object.__setattr__(self, "closes_dropped", True)
        elif self.closes_dropped:
            # Derived, never a caller's to assert: a closure that stands was not corrected.
            object.__setattr__(self, "closes_dropped", False)
