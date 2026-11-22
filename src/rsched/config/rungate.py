"""The run GATE's two config models: the whole form a routine declares, and the partial the
PATCH edge accepts. They live beside `config/routine.py` rather than in it because the gate is
its own vocabulary (`rsched/gatekit/`) with its own validation, and a routine's config file was
at the house size ceiling with no room for the fields the conversation layer needs.

`RoutineConfig` re-exports both under the names their callers already reach for, so nothing
imports from here that did not import from `config.routine` before.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RunGateConfig(BaseModel):
    """Explicit, strict opt-in to pre-engine automatic admission: whether a scheduled fire
    first asks its CHECKS if there is work (docs/run-gates.md). `checks` is the vocabulary in
    `rsched/gatekit/`; the `script` kind is the routine's own `scripts/admit.py`.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    timeout_s: int = Field(default=30, ge=1, le=300)
    checks: list[dict] = Field(default_factory=list)
    run_written: bool = True   # a run's state/next-gate.json may replace `checks` (gatecompose)

    @field_validator("checks")
    @classmethod
    def _known_checks(cls, v: list[dict]) -> list[dict]:
        from ..gatekit import validate

        if problems := validate(v):
            raise ValueError("; ".join(problems))
        return v

    @model_validator(mode="after")
    def _enabled_needs_a_check(self) -> RunGateConfig:
        # An enabled gate with nothing to ask would have to invent an answer; either one is a
        # lie — "skip" silently loses every fire, "run" is no gate at all.
        if self.enabled and not self.checks:
            raise ValueError("an enabled gate needs at least one check")
        return self


class RunGatePatch(BaseModel):
    """A PARTIAL run gate for the PATCH edge: each field optional, the merge validated whole
    by `RunGateConfig` (web/patch_merge.apply_run_gate).
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool | None = None
    timeout_s: int | None = Field(default=None, ge=1, le=300)
    checks: list[dict] | None = None
    run_written: bool | None = None
