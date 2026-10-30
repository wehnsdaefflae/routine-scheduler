"""D223: a run whose incompressible prompt floor exceeds its model's window is RECORDED, and
still starts.

The second half of F539. The first half shipped in 0.474.0: a run served by a smaller model is
told its window, durably (`engine/substitution.py`). What stayed open is the precondition — the
floor is computable before the first model call, and nothing compared it to anything, so a run
that was unrunnable from turn 0 still started and died mid-run. The specimen is
`llmsectest-weekday:20260922-060001`: dead at turn 106 having spent $1.03, on
*"prompt is too long and cannot be shrunk further: 57,250 estimated tokens … provider maximum
32,768"*.

THE OPERATOR DECIDED IT (D223, 2026-10-30), option (a) verbatim: *"A HEALTH EVENT only: record
the precondition failure and start the run anyway. Nothing changes for any run, and you get a
surface that says how often it happens (recommended first step)."* So these tests pin BOTH
halves of that answer, and the second is the one worth having:

- the event is filed when the floor exceeds the window;
- **the run is not refused, not aborted, and nothing is appended to its prompt.** A run that
  previously produced something must still produce it. That is why (b) — refusing the fire —
  was not chosen: the window figure comes from the catalog, and an F278 run-local correction
  can already make the real clamp smaller than the catalog says, so a refusal would be a veto
  resting on an approximation this repo has already documented as approximate.

The floor is measured on the prompt as composed, because that is the thing that cannot shrink:
`engine/window.py` and `engine/compaction.py` both state that the retained head+tail is an
incompressible floor, and `engine/overflow.py` raises exactly when shrinking cannot get under
the window.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rsched.engine.promptfloor import check as floor_check


def _events(home) -> list[dict]:
    path = home / ".control" / "health-events.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _loop(tmp_path, *, window: int, messages: list[dict]):
    """A loop-shaped stand-in: the registry answers with a model of `window` tokens."""
    ref = SimpleNamespace(endpoint="ep", model="m", name="m", context_tokens=window,
                          multimodal=False)
    ctx = SimpleNamespace(
        routine=SimpleNamespace(dir=tmp_path / "r", slug="victim", models={}, efforts={}),
        server=SimpleNamespace(routines_home=tmp_path),
        registry=SimpleNamespace(for_model=lambda *_a, **_k: (SimpleNamespace(), ref)),
        run_id="victim:20261030-140004", depth=0)
    return SimpleNamespace(ctx=ctx, messages=messages, resume=False)


def _fat(tokens: int) -> list[dict]:
    """A prompt whose head+tail alone estimate above `tokens` (~4 chars per token)."""
    return [{"role": "system", "content": "x" * (tokens * 4)},
            {"role": "user", "content": "y" * 400}]


def test_a_floor_over_the_window_files_the_precondition_event(tmp_path):
    (tmp_path / ".control").mkdir(parents=True)
    loop = _loop(tmp_path, window=32_768, messages=_fat(50_000))

    floor_check(loop)

    rows = [e for e in _events(tmp_path) if e["event"] == "prompt_floor_over_window"]
    assert len(rows) == 1, _events(tmp_path)
    row = rows[0]
    assert row["routine"] == "victim"
    assert row["run_id"] == "victim:20261030-140004"
    # the two numbers are FIELDS, not only prose: "how often does this happen, and by how much"
    # is the question the surface exists to answer, and a sweep must be able to filter on them
    assert row["window_tokens"] == 32_768
    assert row["floor_tokens"] > 32_768
    assert "32,768" in row["detail"] and "m" in row["detail"]


def test_the_run_is_not_refused_and_its_prompt_is_untouched(tmp_path):
    """His answer is (a) and (a) alone: *record the precondition failure and start the run
    anyway*. A run that previously produced something must still produce it — and nothing is
    appended either, because a note here would be the engine telling a run something it cannot
    act on (the prompt is immutable once composed, and the floor is the prompt).
    """
    (tmp_path / ".control").mkdir(parents=True)
    messages = _fat(50_000)
    before = [dict(m) for m in messages]
    loop = _loop(tmp_path, window=32_768, messages=messages)

    assert floor_check(loop) is None     # no verdict, no refusal, nothing to raise
    assert loop.messages == before


def test_a_prompt_that_fits_files_nothing(tmp_path):
    """Every ordinary run. The layer must cost nothing in the common case — and an event filed
    on a fitting prompt would make the surface's own count meaningless.
    """
    (tmp_path / ".control").mkdir(parents=True)
    loop = _loop(tmp_path, window=200_000, messages=_fat(5_000))

    floor_check(loop)

    assert _events(tmp_path) == []


def test_a_window_the_registry_cannot_answer_files_nothing(tmp_path):
    """No window, no comparison. The check is a diagnostic: it must never be the reason a boot
    fails, so an unresolvable model is simply not measured.
    """
    (tmp_path / ".control").mkdir(parents=True)
    loop = _loop(tmp_path, window=0, messages=_fat(50_000))

    def boom(*_a, **_k):
        raise RuntimeError("no such role")

    loop.ctx.registry = SimpleNamespace(for_model=boom)
    assert floor_check(loop) is None
    assert _events(tmp_path) == []


def test_the_check_never_raises_whatever_it_is_handed(tmp_path):
    """It runs inside boot, before turn one: an exception here would turn a diagnostic into the
    reason a run never starts, which is the opposite of the answer chosen.
    """
    assert floor_check(SimpleNamespace(ctx=None, messages=None)) is None
    assert floor_check(SimpleNamespace()) is None


def test_the_event_is_in_the_documented_vocabulary():
    """An event at a call site but not in the header enum is invisible by construction — the
    `prompt_oversize_shrunk` failure, which tests/test_health_events.py exists to catch. Pinned
    here too, next to the thing that emits it, so the registration is not a separate chore.
    """
    from rsched import health_events

    head = (health_events.__doc__ or "").split('"routine": <slug>')[0]
    assert "prompt_floor_over_window" in head
    assert "prompt_floor_over_window" in (health_events.__doc__ or "")
