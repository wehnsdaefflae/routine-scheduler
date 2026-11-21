"""The suite's RENDER floor: every observation a test produces is rendered, and a renderer that
raises FAILS the test instead of being caught by the engine's own floor.
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
