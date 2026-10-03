"""Step 4 of the escalation ladder: the TRIGGER — when a rung fires, what it is shown, what
comes back, and the two guarantees that decide whether the mechanism is safe to ship.

The two that are tests rather than guidelines, both from measured behaviour in this repo:

1. **A healthy run is never told it is being watched.** A `continue` verdict injects NOTHING.
   Budget warnings once rode every observation past 85% and runs converged at the ceiling
   whether or not their job was done — a repeated signal in a prompt becomes the thing the run
   optimizes against. `escalate` asks that question at the ONE place a message is filed.
2. **Oversight may never fail the worker.** A supervisor that crashes, returns prose, times out
   or cannot be started at all leaves the run exactly as it was. The run was doing real work;
   losing its supervision is strictly better than losing the run.

And the arithmetic that makes the ladder affordable: the interval is closed by the rung that
READ it, so no two rungs ever judge the same turns, and the worker's own turn counter does not
move while a rung runs.
"""

import json
from pathlib import Path

import pytest

from rsched.engine import ladder, oversight


class _Transcript:
    def __init__(self, path: Path):
        self.path = path
        self.events: list[tuple[str, dict]] = []

    def event(self, kind, payload, **_kw):
        self.events.append((kind, dict(payload)))

    def kinds(self):
        return [k for k, _ in self.events]


class _Routine:
    def __init__(self, tmp_path, **attrs):
        self.dir = tmp_path
        self.slug = "worker"
        self.name = "Worker"
        self.instruction = "Ship the thing the operator decided."
        self.done_when = ["d1 the thing is shipped"]
        self.finish_line = []
        for key, value in attrs.items():
            setattr(self, key, value)


class _Budgets:
    max_subrun_depth = 2


class _Ctx:
    def __init__(self, tmp_path, *, turn=20, depth=0, **routine_attrs):
        self.transcript = _Transcript(tmp_path / "transcript.jsonl")
        self.transcript.path.write_text("", encoding="utf-8")
        self.routine = _Routine(tmp_path, **routine_attrs)
        self.budgets = _Budgets()
        self.turn = turn
        self.depth = depth
        self.last_rung_turn = 0

    def meter(self):
        return {"turns": self.turn, "max_turns": 100}


class _Subs:
    """The subrun manager's one entry point the ladder uses, scripted."""

    def __init__(self, result):
        self.result = result
        self.calls: list[dict] = []

    def _start_child(self, action, *, mode, prefix, overrides=None):
        self.calls.append({"action": action, "mode": mode, "prefix": prefix,
                           "overrides": overrides})
        return self.result


class _Sub:
    def __init__(self, *, status="ok", summary=""):
        self.status = status
        self.summary = summary
        self.n = 1

        class _Done:
            @staticmethod
            def wait(timeout=None):      # noqa: ARG004 — threading.Event.wait's signature
                return True

        self.done = _Done()


class _Loop:
    def __init__(self, ctx, subs, says=()):
        self.ctx = ctx
        self.subs = subs
        self.turn_records = [{"say": s} for s in says]


def _directive(**over):
    d = {"verdict": "drifting", "disposition": "redirect",
         "instruction": ["Go back to the queue's first item."],
         "next_look": "whether the queue item was built", "next_rung_in": 10}
    d.update(over)
    return d


# -- the knobs ---------------------------------------------------------------------------


def test_the_ladder_is_off_until_a_routine_enables_it(tmp_path):
    """Ship it OFF by default: no live routine changes behaviour at the release, and the
    config field step 5 adds does not exist yet — so the defaults must read as disabled rather
    than raise on a routine object that has never heard of the ladder."""
    settings = ladder.ladder_settings(_Ctx(tmp_path))
    assert settings["enabled"] is False
    assert settings["height"] == ladder.DEFAULT_RUNG_HEIGHT
    assert settings["max_depth"] == ladder.DEFAULT_MAX_DEPTH


def test_a_malformed_knob_leaves_the_ladder_off_rather_than_ending_the_run(tmp_path):
    """This runs at every turn boundary of every run, so a knob someone typed wrong must
    degrade to the default — never raise into the loop."""
    ctx = _Ctx(tmp_path, ladder="yes please", tuning=["not", "a", "mapping"])
    settings = ladder.ladder_settings(ctx)
    assert settings["enabled"] is False and settings["height"] == ladder.DEFAULT_RUNG_HEIGHT


@pytest.mark.parametrize("bad", [0, -5, True, "15", 2.5, None])
def test_a_nonsense_rung_height_falls_back(tmp_path, bad):
    ctx = _Ctx(tmp_path, ladder={"enabled": True}, tuning={"ladder_rung_height": bad})
    assert ladder.ladder_settings(ctx)["height"] == ladder.DEFAULT_RUNG_HEIGHT


def test_the_rung_budget_scales_with_the_interval_but_has_a_floor(tmp_path):
    """`oversight_turns = n` keeps the judgement proportional to what it reads; the floor
    exists because a rung with one turn spends it on the reading and finishes with nothing,
    which reads as `continue` — the one failure mode that is invisible."""
    big = _Ctx(tmp_path, ladder={"enabled": True}, tuning={"ladder_rung_height": 30})
    assert ladder.ladder_settings(big)["oversight_turns"] == 30
    small = _Ctx(tmp_path, ladder={"enabled": True},
                 tuning={"ladder_rung_height": 30, "oversight_turns": 1})
    assert ladder.ladder_settings(small)["oversight_turns"] == ladder.MIN_OVERSIGHT_TURNS


# -- when a rung is due ------------------------------------------------------------------


def test_no_rung_fires_while_the_ladder_is_disabled(tmp_path):
    loop = _Loop(_Ctx(tmp_path, turn=999), _Subs("unused"))
    assert ladder.rung_is_due(loop, ladder.ladder_settings(loop.ctx)) is None


def test_a_rung_fires_on_the_interval_and_not_before(tmp_path):
    enabled = {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15}
    early = _Loop(_Ctx(tmp_path, turn=14), _Subs("unused"))
    assert ladder.rung_is_due(early, enabled) is None
    due = _Loop(_Ctx(tmp_path, turn=15), _Subs("unused"))
    assert ladder.rung_is_due(due, enabled) == "interval"


def test_the_interval_is_counted_from_the_last_rung_not_from_the_runs_start(tmp_path):
    """A rung reports on the interval it OWNS. Counted from the run's start instead, every
    boundary past the first rung would be due and the ladder would fire continuously."""
    enabled = {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15}
    loop = _Loop(_Ctx(tmp_path, turn=20), _Subs("unused"))
    loop.ctx.last_rung_turn = 15
    assert ladder.rung_is_due(loop, enabled) is None
    loop.ctx.turn = 30
    assert ladder.rung_is_due(loop, enabled) == "interval"


@pytest.mark.parametrize(("flag", "expected"), [
    ("escalation_requested", "worker_requested"),
    ("outcome_claimed_met", "outcome_claimed_met"),
])
def test_three_events_pull_an_escalation_forward(tmp_path, flag, expected):
    """`n` is a ceiling on the interval, not a metronome."""
    enabled = {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15}
    loop = _Loop(_Ctx(tmp_path, turn=2), _Subs("unused"))
    setattr(loop.ctx, flag, True)
    assert ladder.rung_is_due(loop, enabled) == expected


def test_a_repeated_failure_pulls_an_escalation_forward(tmp_path):
    enabled = {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15}
    loop = _Loop(_Ctx(tmp_path, turn=3), _Subs("unused"))
    loop.repeat_failure_signal = ["util:thing failed twice unchanged"]
    assert ladder.rung_is_due(loop, enabled) == "repeat_failure"


def test_the_top_rung_answers_to_a_person_not_to_a_fourth_rung(tmp_path):
    """m is a SEMANTIC ceiling: at the top, no rung fires. The design's escalation from there
    is to a PERSON, which a child cannot block on, so it surfaces as a decision record."""
    enabled = {"enabled": True, "height": 1, "max_depth": 1, "oversight_turns": 4}
    loop = _Loop(_Ctx(tmp_path, turn=50, depth=1), _Subs("unused"))
    assert ladder.rung_is_due(loop, enabled) is None


# -- what a rung is shown ----------------------------------------------------------------


def test_the_dispatch_carries_the_goal_verbatim_and_the_workers_own_words(tmp_path):
    """A supervisor judging drift against a SUMMARY of the goal cannot see drift into that
    summary, and the worker's words sit beside the engine's counts, never merged into them."""
    ctx = _Ctx(tmp_path, ladder={"enabled": True})
    subs = _Subs(_Sub(summary=json.dumps(_directive())))
    loop = _Loop(ctx, subs, says=["Reading the queue.", "Building item one."])
    ladder.escalate(loop, "interval", ladder.ladder_settings(ctx) | {"enabled": True})
    prompt = subs.calls[0]["action"]["prompt"]
    assert "Ship the thing the operator decided." in prompt
    assert "Building item one." in prompt
    assert "worker_says" in prompt


def test_a_rung_runs_as_an_oversight_child_on_the_main_model_with_a_pinned_budget(tmp_path):
    """Child budgets are a share of the parent's remainder by default, which would cause the
    stalling the ladder was hired to prevent: turns are PINNED. And the verdict runs on `main`
    — judging drift is the hardest inference in the design, and the cheap tier buys a saving
    and loses the capability."""
    ctx = _Ctx(tmp_path)
    subs = _Subs(_Sub(summary=json.dumps(_directive())))
    settings = {"enabled": True, "height": 12, "max_depth": 3, "oversight_turns": 12}
    ladder.escalate(_Loop(ctx, subs), "interval", settings)
    call = subs.calls[0]
    assert call["mode"] == "oversight"
    assert call["overrides"] == {"turns": 12}
    assert call["action"]["model"] == "main"
    assert call["action"]["workflow"] == ladder.SUPERVISOR_WORKFLOW


def test_the_supervisor_runs_on_its_own_pattern_never_the_generic_one(tmp_path):
    """A run whose output BINDS another run must be instructed in what a dispatch is and what
    it may answer. `general-task` would be a generic child handed authority over a run."""
    assert ladder.SUPERVISOR_WORKFLOW != "general-task"
    ctx = _Ctx(tmp_path)
    subs = _Subs(_Sub(summary=json.dumps(_directive())))
    ladder.escalate(_Loop(ctx, subs), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    prompt = subs.calls[0]["action"]["prompt"]
    for verdict in oversight.VERDICTS:
        assert verdict in prompt
    for disposition in oversight.DISPOSITIONS:
        assert disposition in prompt


# -- what comes back ---------------------------------------------------------------------


def test_a_redirect_reaches_the_worker_as_freight_on_the_oversight_channel(tmp_path):
    ctx = _Ctx(tmp_path)
    subs = _Subs(_Sub(summary=json.dumps(_directive())))
    ladder.escalate(_Loop(ctx, subs), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    filed = sorted((tmp_path / "inbox").glob("msg-*.json"))
    assert len(filed) == 1
    record = json.loads(filed[0].read_text(encoding="utf-8"))
    assert record["via"] == "oversight"
    assert "Go back to the queue's first item." in record["text"]
    assert "A user message outranks it." in record["text"]


def test_a_continue_verdict_injects_nothing(tmp_path):
    """Constraint 1, and the whole reason the ladder is invisible to a healthy run."""
    ctx = _Ctx(tmp_path)
    subs = _Subs(_Sub(summary=json.dumps(
        _directive(verdict="on_track", disposition="continue", instruction=[]))))
    ladder.escalate(_Loop(ctx, subs), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    assert list((tmp_path / "inbox").glob("msg-*.json")) == []
    assert "oversight_directive" in ctx.transcript.kinds()   # recorded, just not injected


def test_a_supervisors_next_rung_in_shortens_its_own_interval_but_cannot_lengthen_it(tmp_path):
    """A supervisor may shorten its OWN next interval and may never speak inside one — the one
    way a rung could otherwise quietly switch itself off is a huge next_rung_in."""
    ctx = _Ctx(tmp_path)
    subs = _Subs(_Sub(summary=json.dumps(_directive(next_rung_in=999))))
    ladder.escalate(_Loop(ctx, subs), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    assert ctx.next_rung_in == 15


# -- oversight may never fail the worker -------------------------------------------------


@pytest.mark.parametrize(("sub", "why"), [
    (_Sub(summary="Looks fine to me, carry on!"), "prose that is not a directive"),
    (_Sub(summary='{"verdict": "nonsense", "disposition": "redirect"}'), "malformed"),
    (_Sub(status="failed", summary='{"verdict": "on_track"}'), "the rung failed"),
    (_Sub(summary=""), "the rung said nothing"),
])
def test_a_rung_that_hands_back_no_usable_directive_changes_nothing(tmp_path, sub, why):
    ctx = _Ctx(tmp_path)
    ladder.escalate(_Loop(ctx, _Subs(sub)), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    assert list((tmp_path / "inbox").glob("msg-*.json")) == [], why
    assert "oversight_no_directive" in ctx.transcript.kinds()


def test_a_rung_that_cannot_be_started_is_a_non_event(tmp_path):
    """The ordinary case on a deep or budget-spent tree, not an error."""
    ctx = _Ctx(tmp_path)
    ladder.escalate(_Loop(ctx, _Subs("child-task budget exhausted")), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    assert "oversight_skipped" in ctx.transcript.kinds()
    assert list((tmp_path / "inbox").glob("msg-*.json")) == []


def test_nothing_in_the_ladder_can_end_the_run(tmp_path):
    """`at_boundary` is the loop's only entry point into the ladder, and it is wrapped: an
    oversight mechanism that takes the run down with it is worse than no oversight."""
    class _Exploding(_Subs):
        def _start_child(self, action, *, mode, prefix, overrides=None):
            raise RuntimeError("the supervisor's thread could not start")

    ctx = _Ctx(tmp_path, turn=99, ladder={"enabled": True})
    loop = _Loop(ctx, _Exploding(None))
    ladder.at_boundary(loop)                       # must not raise
    assert "oversight_skipped" in ctx.transcript.kinds()


def test_the_interval_is_closed_even_when_the_rung_fails(tmp_path):
    """Two rungs judging one interval is how a ladder becomes m copies of one audit, so the
    interval is closed by the rung that READ it — whatever that rung then managed to say."""
    ctx = _Ctx(tmp_path, turn=40)
    ladder.escalate(_Loop(ctx, _Subs(_Sub(summary="not a directive"))), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    assert ctx.last_rung_turn == 40


def test_a_pull_forward_flag_is_spent_by_the_rung_it_fired(tmp_path):
    """Otherwise one repeated failure escalates at every boundary for the rest of the run."""
    ctx = _Ctx(tmp_path, turn=7)
    ctx.escalation_requested = True
    ladder.escalate(_Loop(ctx, _Subs(_Sub(summary=json.dumps(_directive())))), "worker_requested",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    assert ctx.escalation_requested is False
    assert ctx.outcome_claimed_met is False


def test_the_workers_turn_counter_does_not_move_while_a_rung_runs(tmp_path):
    """The rung's cost is the rung's own — pinned by `oversight_turns`, never silently taken
    out of the worker's budget."""
    ctx = _Ctx(tmp_path, turn=25)
    ladder.escalate(_Loop(ctx, _Subs(_Sub(summary=json.dumps(_directive())))), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    assert ctx.turn == 25
