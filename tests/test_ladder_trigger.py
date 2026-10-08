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


class _Server:
    """Enough of ServerConfig for the ladder: where the workflow library lives. A library
    holding the supervisor pattern is the ordinary case; `library=False` is the hard-skip one.
    """

    def __init__(self, tmp_path, *, library=True):
        self.libraries_home = tmp_path / "library"
        if library:
            (self.libraries_home / "workflows").mkdir(parents=True, exist_ok=True)
            (self.libraries_home / "workflows"
             / f"{ladder.SUPERVISOR_WORKFLOW}.py").write_text("META = {}\n", encoding="utf-8")


class _Ctx:
    def __init__(self, tmp_path, *, turn=20, depth=0, library=True, **routine_attrs):
        self.transcript = _Transcript(tmp_path / "transcript.jsonl")
        self.transcript.path.write_text("", encoding="utf-8")
        self.routine = _Routine(tmp_path, **routine_attrs)
        self.server = _Server(tmp_path, library=library)
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
    """The slice of EngineLoop the ladder touches.

    The sub-run manager is `subruns` — the name the REAL loop carries (`engine/loop.py:154`,
    and every working caller in `engine/actionroute.py`). This fake said `subs` and so did
    `ladder.py`, which is the only reason every rung on every run could die of an
    AttributeError for months behind a green suite (R2348/R2352). If a change makes this
    attribute disagree with `engine/loop.py` again, fix the production code, never this line.
    """

    def __init__(self, ctx, subs, says=()):
        self.ctx = ctx
        self.subruns = subs
        self.turn_records = [{"say": s} for s in says]


def _directive(**over):
    d = {"verdict": "drifting", "disposition": "redirect",
         "instruction": ["Go back to the queue's first item."],
         "next_look": "whether the queue item was built", "next_rung_in": 10}
    d.update(over)
    return d


# -- the knobs ---------------------------------------------------------------------------


def test_the_ladder_is_off_until_a_routine_enables_it(tmp_path):
    """Ship it OFF by default: no live routine changes behaviour at the release, and a routine
    object that has never heard of the ladder must read as disabled rather than raise."""
    settings = ladder.ladder_settings(_Ctx(tmp_path))
    assert settings["enabled"] is False
    assert settings["height"] == ladder.DEFAULT_RUNG_HEIGHT
    assert settings["max_depth"] == ladder.DEFAULT_MAX_DEPTH


def test_the_engine_and_the_config_layer_share_one_set_of_defaults():
    """A second copy of a knob's default in the engine is how a knob comes to mean two things:
    the routine page would show one number and the trigger would use another."""
    from rsched.config.base import DEFAULT_LADDER, DEFAULT_RUNG_HEIGHT

    assert DEFAULT_LADDER["max_depth"] == ladder.DEFAULT_MAX_DEPTH
    assert ladder.DEFAULT_RUNG_HEIGHT is DEFAULT_RUNG_HEIGHT
    assert DEFAULT_LADDER["enabled"] is False


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


@pytest.mark.parametrize(("height", "expected"),
                         [(2, 4), (6, 4), (10, 6), (15, 8), (20, 11), (30, 16)])
def test_a_rungs_turn_cap_is_a_sublinear_read_plus_a_constant_judge(height, expected):
    """The cost formula the operator chose over the design's `oversight_turns = n`
    (2026-10-02), and the reason it is a FORMULA: every routine that opts in inherits it.

    A rung's cost has a READ term (the interval's `say` lines, growing with `n`) and a JUDGE
    term (assemble one directive, fixed). `= n` prices both as scaling — the 2×-the-worker
    ceiling the design itself calls the objection that decides the feature. A fixed cap prices
    both as fixed, starving the read at large `n` into `oversight_no_directive`: supervision
    that silently does nothing. `n // 2 + 1` still grows, so a long interval is never starved,
    while holding the per-rung worst case at ~0.5× and the whole m=3 ladder at ~1.5×.
    """
    assert ladder.oversight_turns_for(height) == expected


def test_the_floor_catches_an_interval_too_short_to_judge(tmp_path):
    """`MIN_OVERSIGHT_TURNS` is redundant for any n >= 6 once the cap is derived, and kept
    anyway: it costs nothing and catches someone setting `n = 2`. A rung with one turn spends
    it on the reading and finishes with nothing, which reads as `continue`."""
    assert ladder.oversight_turns_for(2) == ladder.MIN_OVERSIGHT_TURNS
    small = _Ctx(tmp_path, ladder={"enabled": True},
                 tuning={"ladder_rung_height": 30, "oversight_turns": 1})
    assert ladder.ladder_settings(small)["oversight_turns"] == ladder.MIN_OVERSIGHT_TURNS


def test_the_derived_cap_is_used_when_tuning_names_no_override(tmp_path):
    big = _Ctx(tmp_path, ladder={"enabled": True}, tuning={"ladder_rung_height": 30})
    assert ladder.ladder_settings(big)["oversight_turns"] == 16
    tuned = _Ctx(tmp_path, ladder={"enabled": True},
                 tuning={"ladder_rung_height": 30, "oversight_turns": 25})
    assert ladder.ladder_settings(tuned)["oversight_turns"] == 25


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


def test_an_absent_supervisor_pattern_skips_the_rung_rather_than_degrading_it(tmp_path):
    """The HARD SKIP (operator, 2026-10-02 — option 1 + 3 together).

    `childrun.materialize_to_disk` catches every failure and degrades an unknown slug to the
    builtin fallback recipe. For an ordinary child that is the right trade; for a rung it is the
    worst outcome in the design — a generic child, instructed to "orient, do the work, record",
    holding authority over a live run. So the rung is skipped, countably, and the worker is left
    exactly as it was.
    """
    ctx = _Ctx(tmp_path, library=False)
    subs = _Subs(_Sub(summary=json.dumps(_directive())))
    ladder.escalate(_Loop(ctx, subs), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    assert subs.calls == []                                   # no child was ever started
    assert ctx.transcript.kinds() == ["oversight_skipped"]
    reason = ctx.transcript.events[0][1]["reason"]
    assert ladder.SUPERVISOR_WORKFLOW in reason
    assert list((tmp_path / "inbox").glob("msg-*.json")) == []


def test_a_skipped_rung_does_not_close_the_interval_it_never_read(tmp_path):
    """Two rungs judging one interval is how a ladder becomes m copies of one audit — and the
    converse matters just as much: a rung that could not run must not consume the turns it was
    never shown, or the next one judges a window with a hole in it.
    """
    ctx = _Ctx(tmp_path, turn=40, library=False)
    ctx.last_rung_turn = 10
    ladder.escalate(_Loop(ctx, _Subs("unused")), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    assert ctx.last_rung_turn == 10


def test_an_unreadable_library_skips_the_rung_instead_of_ending_the_run(tmp_path):
    """Oversight may never fail the worker — a library that cannot be read is the ladder's
    problem, not the run's."""
    ctx = _Ctx(tmp_path, library=False)
    ctx.server.libraries_home = None
    assert ladder.supervisor_pattern_missing(ctx.server)
    ladder.escalate(_Loop(ctx, _Subs("unused")), "interval",
                    {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15})
    assert ctx.transcript.kinds() == ["oversight_skipped"]


def test_the_supervisor_pattern_is_in_the_library_seed():
    """The other half of the operator's answer: the pattern itself is AUTHORED, not merely
    guarded against. Without it the hard skip above makes the feature permanently dark."""
    seed = Path(__file__).resolve().parents[1] / "library-seed" / "workflows"
    assert (seed / f"{ladder.SUPERVISOR_WORKFLOW}.py").is_file()


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
    assert "oversight_failed" in ctx.transcript.kinds()


def test_a_mechanism_that_raised_is_not_recorded_as_a_rung_that_declined(tmp_path):
    """A RAISE and a DECLINE are different facts and must never share an event.

    This is the property R2348/R2352 was filed on: `ladder.py` dispatched through `loop.subs`,
    an attribute the real loop has never had, and filed the AttributeError as
    `oversight_skipped` — the same record the legitimate "deep or budget-spent tree" case
    writes. So every rung on every run died and read, on every surface, exactly like a healthy
    ladder declining to fire. The two events below must stay distinct whatever else changes.
    """
    class _Exploding(_Subs):
        def _start_child(self, action, *, mode, prefix, overrides=None):
            raise AttributeError("'EngineLoop' object has no attribute 'subs'")

    settings = {"enabled": True, "height": 15, "max_depth": 3, "oversight_turns": 15}

    (tmp_path / "broke").mkdir()
    (tmp_path / "declined").mkdir()
    broke = _Ctx(tmp_path / "broke", turn=99, ladder={"enabled": True})
    ladder.at_boundary(_Loop(broke, _Exploding(None)))
    assert "oversight_failed" in broke.transcript.kinds()
    assert "oversight_skipped" not in broke.transcript.kinds()

    # The decline: `_start_child` hands back a REASON STRING, which is not an error at all.
    declined = _Ctx(tmp_path / "declined")
    ladder.escalate(_Loop(declined, _Subs("child-task budget exhausted")), "interval", settings)
    assert "oversight_skipped" in declined.transcript.kinds()
    assert "oversight_failed" not in declined.transcript.kinds()


def test_the_ladder_dispatches_through_an_attribute_the_real_loop_has(tmp_path):
    """The fake `_Loop` above is the ladder's only test surface, so a name only IT carries
    proves nothing. Pin the attribute against the real class's own annotations: had this
    existed, R2348's "every rung has always died" would have failed on the first run.
    """
    import re

    from rsched.engine.loop import EngineLoop

    # CODE only: the comments in ladder.py name the historical `loop.subs` on purpose, so a
    # guard that read them would fail on its own explanation.
    source = "\n".join(line.split("#", 1)[0] for line
                       in Path(ladder.__file__).read_text(encoding="utf-8").splitlines())
    reached = set(re.findall(r"\bloop\.([a-z_]+)\b", source))
    known = set(EngineLoop.__annotations__) | set(dir(EngineLoop))
    assert reached, "the ladder reaches nothing on the loop — did the dispatch move?"
    assert not (reached - known), (
        f"engine/ladder.py reaches loop attributes EngineLoop does not have: "
        f"{sorted(reached - known)}")


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
