"""The materialization pipeline's OUTPUT BUDGET (`workflows/pipeline.py`): what each call asks
for, against what the model can actually return.

The per-artifact constants (OUTLINE/MAIN/STAGE_MAX_TOKENS) are what ONE recipe document needs.
What the model can deliver is its resolved cap, `ModelRef.max_tokens` — per-model config, then
the provider-discovered `max_output_tokens` of `endpoints/limits.py`, then the endpoint default,
then the engine floor. A request above that cap is one the provider rejects or truncates
(`input + requested_output <= window`), so the pipeline asks for the SMALLER of the two: the
constant is a ceiling, never a target. A model whose cap is unresolved falls back to the
constant — never to zero, never to a crash.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rsched.config import DEFAULT_MODEL_MAX_TOKENS
from rsched.workflows import pipeline

OUTLINE = {"stages": [{"name": "work", "scope": "do it", "inputs": "-", "outputs": "-"}]}
MAIN = {"main": "## Run flow\n\n1. **work** — `stages/work.md`\n\n## Done when\n\n- d1 — it ran\n"}
STAGE = {"body": "Read the inbox.\nWrite the record."}
CONSTANTS = [pipeline.OUTLINE_MAX_TOKENS, pipeline.MAIN_MAX_TOKENS, pipeline.STAGE_MAX_TOKENS]


def _asked(**ref_fields) -> list[int]:
    """Drive the whole pipeline (outline → main → one stage) with a scripted model, and return
    the `max_tokens` each of the three calls ASKED for, in order.
    """
    answers = iter([OUTLINE, MAIN, STAGE])
    asked: list[int] = []

    def complete(_messages, **kw):
        asked.append(kw["max_tokens"])
        return SimpleNamespace(parsed=next(answers), text="")

    ref = SimpleNamespace(model="m", effort="", temperature=None, **ref_fields)
    out = pipeline._pipeline(lambda: (SimpleNamespace(complete=complete), ref), "raw", "task",
                             pins=[], done_when=[], never=[], slug="general-task")
    assert out["stages"]["work"]      # the pipeline really ran, it did not degrade
    return asked


def test_a_model_that_cannot_return_the_constant_is_asked_for_what_it_can(tmp_path):
    """The reported case: a discovered output cap of 8,192 was asked for 16,000 anyway."""
    assert _asked(max_tokens=8_192) == [8_000, 8_192, 8_192]


def test_a_cap_above_the_constants_does_not_raise_the_request(tmp_path):
    """The constant is a CEILING, not a target — a 200k-cap model still gets one document's
    worth. Maxing it out would starve the prompt (see endpoints/limits.py)."""
    assert _asked(max_tokens=200_000) == CONSTANTS


@pytest.mark.parametrize("cap", [0, None, DEFAULT_MODEL_MAX_TOKENS],
                         ids=["zero", "none", "floor"])
def test_an_unresolved_cap_falls_back_to_the_constants(cap):
    """A model the provider does not list gets no discovered cap: resolution hands the pipeline
    the engine floor (`DEFAULT_MODEL_MAX_TOKENS`), and a fake or unset ref hands it a falsy one.
    Either way the request is the constant — clamping to a falsy cap would ask for 0 tokens and
    ship nothing, and the floor is not a licence to ask above the constant."""
    assert _asked(max_tokens=cap) == CONSTANTS
