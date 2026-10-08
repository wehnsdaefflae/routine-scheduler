"""A MODEL TRIAL's shape — routine.yaml's `trial:` (the mechanism is `rsched/trials.py`).

"Run this routine on catalog model X for its next N fires" answers whether the routine's own
model is more than its work needs, or not enough. The routine's `models:` stay exactly as they
are; a fire while the trial is ACTIVE runs on the trial's models instead, and every run it
applied to says so in its durable record (`fingerprint.trial`, engine/runrecord.py).

The shape is checked HERE, because three readers need the same answer: the loader (a bad block
is a problem line and the trial is ignored), the PATCH edge (a 422 naming the field) and the
filing check a run's `config_patch` meets (engine/config_bridge.py). Whether each name is in the
model CATALOG needs the server's config, so that is `rsched.trials.catalog_problem`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..ids import is_slug
from .base import MODEL_KINDS

#: The most runs one trial may span — enough to see past one odd run, few enough that a trial
#: stays an experiment rather than a quiet re-binding of the routine's model.
TRIAL_MAX_RUNS = 20


class TrialConfig(BaseModel):
    """`{id, models, runs, reason}` — strict, and no other key: a misspelled `run:` read as
    absent would make a five-run trial a no-op that nothing reports.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    # Names the trial in every run it applies to. A NEW trial needs a new id: the runs already
    # recorded under one count toward it, so re-proposing an old id finds it finished.
    id: str = Field(min_length=1, max_length=80)
    # Role → catalog model NAME, the chat roles only (main / tool_call / uncensored) — usually
    # just `main`. Replaces those roles for the trial's runs; a role left out keeps its own.
    models: dict[str, str] = Field(min_length=1)
    runs: int = Field(ge=1, le=TRIAL_MAX_RUNS)
    # Why it is being tried — what the operator reads beside the chip, and what the results are
    # read against.
    reason: str = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _kebab(cls, v: str) -> str:
        if not is_slug(v):
            raise ValueError(f"{v!r} is not kebab-case (e.g. t-20261008-sonnet-high)")
        return v

    @field_validator("models")
    @classmethod
    def _chat_roles(cls, v: dict[str, str]) -> dict[str, str]:
        if unknown := sorted(set(v) - set(MODEL_KINDS)):
            raise ValueError(f"unknown role(s) {unknown} — a trial names chat roles "
                             f"{', '.join(MODEL_KINDS)}")
        if blank := sorted(role for role, name in v.items() if not name.strip()):
            raise ValueError(f"no catalog model named for {blank}")
        return {role: name.strip() for role, name in v.items()}

    @field_validator("reason")
    @classmethod
    def _said(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("say what the trial should show")
        return v.strip()


def trial_load_problems(raw_trial: object, loaded: TrialConfig | None) -> list[str]:
    """The loader's closing line for a `trial:` block its validation DROPPED — the lines before
    it named what is wrong, this one says what it costs. [] for no block, or one that loaded.
    """
    if raw_trial is None or loaded is not None:
        return []
    return [("trial: ignored — every run stays on the routine's own models until the trial "
             "block is fixed or cleared")]
