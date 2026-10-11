"""The suite's RENDER floor: every observation a test produces is rendered, and a renderer that
raises FAILS the test instead of being caught by the engine's own floor.

WHY. 0.410.0 gave memory_write's edit mode three refusals and tested each of them — against the
observation DICT. No test rendered one, the renderer read a key a refusal never carries, and the
first live refusal ended scheduler-builder:20261011-120001 (rc=1, no summary, after four shipped
releases) and then its resume, which re-renders the stored observation. It was the fourth defect
of the kind (engine/obs_floor.py counts them): each time a handler's test proved the result and
nothing proved the WORDS the run would read. With this module in place, those three tests failed
with the production KeyError before any fix was written.

Two halves, one per way a defect could otherwise pass the suite:

- every observation the two dispatch seams return — `executor.dispatch`, which handler tests call
  directly, and `actionroute.dispatch_action`, the loop's — is rendered on the spot, so a test
  that produces a shape proves it renders, with no line in the test;
- the engine's floor (`obs_floor.defect_text`), which in production turns a raising renderer into
  a readable observation so the RUN survives, RAISES here: in the suite a defect is a failure,
  never a fallback. tests/test_obs_floor.py restores the real one (`REAL_DEFECT_TEXT`) to test it.

A test that calls a handler function directly, past both seams, renders its own result.
Session-scoped like the sandbox floor: a test that monkeypatches either seam replaces this wrapper
for its own duration only.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from rsched.engine import actionroute, executor, obs_floor
from rsched.engine.observations import format_observation

#: The production floor, for the tests that exercise it.
REAL_DEFECT_TEXT = obs_floor.defect_text


def _raise_defect(obs: dict, exc: Exception) -> str:
    raise AssertionError(f"the observation renderer raised on a {obs.get('kind')!r} result "
                         f"(keys {sorted(obs)}): {obs_floor.describe(exc)}") from exc


def _rendering(seam: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(seam)
    def rendered(*args: Any, **kwargs: Any) -> Any:
        obs = seam(*args, **kwargs)
        if isinstance(obs, dict):
            format_observation(obs)
        return obs
    return rendered


@pytest.fixture(autouse=True, scope="session")
def _every_observation_renders() -> Iterator[None]:
    patch = pytest.MonkeyPatch()
    patch.setattr(obs_floor, "defect_text", _raise_defect)
    patch.setattr(executor, "dispatch", _rendering(executor.dispatch))
    patch.setattr(actionroute, "dispatch_action", _rendering(actionroute.dispatch_action))
    yield
    patch.undo()
