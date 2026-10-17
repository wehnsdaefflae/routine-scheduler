"""A routine's OUTCOME CONTRACT — routine.yaml's `outcome:` (measured by `engine/outcome.py`).

What a routine exists to produce for its principal, as numbers: interviews and contracts for a
freelance hunter, money won for a grant hunter, kilograms for a weight-loss coach. It is the
operator's declaration — config, like everything in routine.yaml — so no run can redefine what
counts as its own success; the routine's `scripts/outcome.py` computes the values from its own
state at every run's end, and the engine records them beside what the run cost.

Distinct from the FINISH LINE (`state/finish-line.json`, engine/finishline.py), whose `outcomes`
say when a routine is DONE FOR GOOD: an outcome contract is what a routine that never finishes
keeps producing, measured, run after run.

The shape is checked HERE so the loader (a bad block is a problem line and the contract is
ignored), the PATCH edge (a 422 naming the field) and the setup surface read one answer.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..ids import is_slug

#: More measures than a person reads at a glance is a dashboard, not an outcome.
MAX_MEASURES = 6


class OutcomeMeasure(BaseModel):
    """One number the routine exists to move."""

    model_config = ConfigDict(extra="forbid", strict=True)

    # The key `scripts/outcome.py` prints the value under, and what a Done-when line names.
    id: str = Field(min_length=1, max_length=40)
    # What a person reads beside the number ("interviews or calls booked with a client").
    label: str = Field(min_length=1, max_length=120)
    # `count`: a running total that only grows (applications sent, euros won) — a period's
    # value is the difference of two readings. `level`: a current reading (weight, open
    # programmes) — the reading itself is the value.
    kind: Literal["count", "level"] = "count"
    # Which way is progress. A weight is `lower`; nearly everything else is `higher`.
    better: Literal["higher", "lower"] = "higher"

    @field_validator("id")
    @classmethod
    def _key(cls, v: str) -> str:
        if not is_slug(v.replace("_", "-")):
            raise ValueError(f"{v!r} is not a key (lower case, digits, - or _)")
        return v


class OutcomeConfig(BaseModel):
    """`{principal, measures}` — who the results are for, and the numbers that say they came."""

    model_config = ConfigDict(extra="forbid", strict=True)

    principal: str = Field(min_length=1, max_length=80)
    measures: list[OutcomeMeasure] = Field(min_length=1, max_length=MAX_MEASURES)

    @field_validator("measures")
    @classmethod
    def _unique(cls, v: list[OutcomeMeasure]) -> list[OutcomeMeasure]:
        ids = [m.id for m in v]
        if dupes := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"measure ids repeat: {', '.join(dupes)}")
        return v
