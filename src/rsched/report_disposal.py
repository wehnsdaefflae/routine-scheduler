"""What a report DISPOSES OF — the value object, its normalization, and its one refusal.

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
    - `closes` — this reply is itself born settled. Valid beside EITHER disposal, and REFUSED
      with neither (D196; see `__post_init__`).
    """

    answers: str = ""
    closes: bool = False
    settles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Normalize once, here, so every reader downstream compares like with like: the ledger
        # stores ids upper-cased and a caller passing "r123" must not mint a row nothing matches.
        # Both faces: an `answers: "r123"` settled R123 only because each reader re-upper-cased
        # it, while the case-sensitive ones — the card's reference links, `refs` — missed it.
        object.__setattr__(self, "answers", str(self.answers or "").strip().upper())
        object.__setattr__(self, "settles",
                           tuple(str(i).strip().upper() for i in self.settles if str(i).strip()))
        # `closes` is a property OF a disposal: with neither face there is nothing to complete.
        # REFUSED, not normalized away (D196, the operator 2026-10-18: "Refuse it loudly — a
        # report carrying closes with supersedes but no answers/settles is rejected with a
        # message naming what closes needs, which changes behaviour for existing callers").
        # Dropping it to False accepted the filing and left the sender believing a thread was
        # closed that was not — a silent no-op on a CLOSURE claim is the one that leaves a
        # thread open while its sender has moved on. The action layer already refused the shape
        # for a run; this is the other entrance. He accepted the break for existing callers
        # explicitly, so a caller that starts failing here is intended, not a regression.
        if self.closes and not (self.answers or self.settles):
            raise ValueError(
                "closes needs a disposal to complete: pass `answers` (the ONE exchange this "
                "reply belongs to) or `settles` (every row it terminally disposes of). "
                "`supersedes` is not one of them — folding rows makes this report their "
                "thread, which is the opposite of ending it.")
