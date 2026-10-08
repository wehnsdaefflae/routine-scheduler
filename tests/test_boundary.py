"""Stage-boundary compaction: archive the middle when doing it now costs less than carrying it.

The size gate alone almost never fires on a 1M-token window (14 passes in 160 fleet runs), while
every finished stage is re-read on every turn the run has left. `engine/boundary.py` prices the
two against each other at each boundary — SoL-Pi's Online Context Compact test.

The gate fixture (`_gate_loop`) is 40 messages of ~1,751 estimated tokens against a 100k window:
70k, under the cached 0.8 gate. A pass keeps head 6 + tail 24, so it leaves ~52.6k and archives a
~17.5k middle; with cache reads and writes priced that is a ~48-turn breakeven, and the pass is
taken when the run has twice that ahead (`boundary.MARGIN`).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rsched.config import ModelRef
from rsched.engine import window
from rsched.engine.boundary import decide
from rsched.engine.window import compact_if_needed
from test_composer import _gate_loop

REF = ModelRef("e", "m", context_tokens=100_000, max_tokens=0)
CACHED = {"cached_in": 5_000, "cache_write": 1_000}
STAGES = ("orient", "draft", "check", "ship")


def _events(loop) -> list[tuple]:
    events: list[tuple] = []
    loop.ctx.transcript = SimpleNamespace(event=lambda t, p, **_k: events.append((t, p)))
    return events


# ---- the breakeven test itself -------------------------------------------------------------

def test_a_long_horizon_repays_the_pass_and_a_short_one_does_not():
    figures = {"before": 70_000, "after": 52_600, "middle": 17_500, "system": 1_750,
               "usage": CACHED}
    out = decide(horizon=160, **figures)
    assert out["compact"] is True
    assert 40 < out["breakeven_turns"] < 60
    assert decide(horizon=10, **figures)["compact"] is False


def test_an_uncached_provider_repays_a_pass_within_a_couple_of_turns():
    """No cache read means every turn re-reads the whole prompt at full price, and a rewrite
    costs nothing extra — so only the archival call is left to repay."""
    out = decide(before=70_000, after=52_600, middle=17_500, system=1_750, horizon=3, usage={})
    assert out["compact"] is True
    assert out["breakeven_turns"] < 2


def test_a_priced_cache_write_makes_the_same_pass_dearer():
    """Writes are priced only where the provider has REPORTED one — an OpenAI-style automatic
    cache charges none, so the kept prefix costs nothing to re-establish there."""
    figures = {"before": 70_000, "after": 52_600, "middle": 17_500, "system": 1_750,
               "horizon": 100}
    written = decide(usage={"cached_in": 5_000, "cache_write": 1_000}, **figures)
    unwritten = decide(usage={"cached_in": 5_000}, **figures)
    assert written["cost"] > unwritten["cost"]


def test_nothing_saved_is_never_a_pass_and_the_record_stays_json():
    out = decide(before=50_000, after=50_000, middle=0, system=1_000, horizon=500,
                 usage=CACHED)
    assert out["compact"] is False
    assert out["breakeven_turns"] is None
    json.dumps(out, allow_nan=False)   # it rides the compaction event into the transcript


# ---- the gate at a boundary ----------------------------------------------------------------

def test_entering_an_early_stage_compacts_a_prompt_under_the_size_gate(monkeypatch):
    """Same prompt the cached-gate test leaves alone (70k under 0.8 × 100k). One stage of four
    reached in 40 turns leaves ~160 turns ahead — past twice the ~48-turn breakeven."""
    loop, calls = _gate_loop(monkeypatch, usage=CACHED, phase="orient", stages=STAGES,
                             entered=("orient",), stage_mark=("", ()))
    compact_if_needed(loop, endpoint=None, ref=REF)
    assert calls, "a pass that repays itself is taken at the boundary"


def test_entering_the_last_stage_leaves_too_few_turns_to_repay_it(monkeypatch):
    loop, calls = _gate_loop(monkeypatch, usage=CACHED, phase="ship", stages=STAGES,
                             entered=STAGES, stage_mark=("check", STAGES[:3]))
    compact_if_needed(loop, endpoint=None, ref=REF)
    assert not calls, "ten turns ahead cannot repay a ~48-turn breakeven"


def test_the_turn_budget_caps_the_horizon(monkeypatch):
    loop, calls = _gate_loop(monkeypatch, usage=CACHED, phase="orient", stages=STAGES,
                             entered=("orient",), stage_mark=("", ()), turns_left=20)
    compact_if_needed(loop, endpoint=None, ref=REF)
    assert not calls, "a run with 20 turns of budget left will not see 160 more"


def test_an_uncached_run_compacts_at_a_boundary_well_under_its_gate(monkeypatch):
    """~46k against the uncached 0.6 gate (60k): only the boundary can take this pass."""
    loop, calls = _gate_loop(monkeypatch, usage={}, phase="ship", stages=STAGES,
                             entered=STAGES, stage_mark=("check", STAGES[:3]), body=4_000)
    compact_if_needed(loop, endpoint=None, ref=REF)
    assert calls


def test_a_stage_the_run_only_recorded_is_a_boundary_too(monkeypatch):
    """A run that routes by writing `state/phase.json` and never re-reads a module never moves
    `ctx.phase` — `llmsectest-weekday`, the fleet's largest spender, is one (F563). The stage
    joining the visited set is the boundary, and the pass is stamped with its name."""
    loop, calls = _gate_loop(monkeypatch, usage=CACHED, stages=STAGES, entered=("orient",),
                             stage_mark=("", ()), real_compact=True)
    events = _events(loop)
    compact_if_needed(loop, endpoint=None, ref=REF)
    assert calls == ["llm"]
    passes = [p for t, p in events if t == "compaction"]
    assert passes and passes[-1]["anticipated"] == "orient"
    assert passes[-1]["economics"]["compact"] is True


def test_inside_a_stage_the_ordinary_gate_applies(monkeypatch):
    loop, calls = _gate_loop(monkeypatch, usage=CACHED, phase="draft", stages=STAGES,
                             entered=("orient", "draft"), stage_mark=("draft",
                                                                     ("orient", "draft")))
    compact_if_needed(loop, endpoint=None, ref=REF)
    assert not calls, "already inside the stage — the ordinary 0.8 gate applies"


def test_a_first_look_is_never_a_boundary(monkeypatch):
    """A resumed leg starts mid-stage with its phase rehydrated: that is where the run already
    stood, not a stage it just reached."""
    loop, calls = _gate_loop(monkeypatch, usage=CACHED, phase="orient", stages=STAGES,
                             entered=("orient",))
    compact_if_needed(loop, endpoint=None, ref=REF)
    assert not calls
    assert loop._stage_mark == ("orient", ("orient",))


def test_a_boundary_waits_for_the_archive_already_in_flight(monkeypatch):
    """Only one archive runs at a time and a second is dropped (`archival.start`), so a pass
    taken while one is in flight would elide a middle that never reaches history/."""
    loop, calls = _gate_loop(monkeypatch, usage=CACHED, phase="orient", stages=STAGES,
                             entered=("orient",), stage_mark=("", ()))
    loop._archival = object()                     # the previous boundary's archive, still running
    compact_if_needed(loop, endpoint=None, ref=REF)
    assert not calls


def test_a_boundary_cannot_force_a_pass_the_anti_thrash_guards_refuse(monkeypatch):
    """30 messages are all head and tail: there is no middle to archive, so nothing to weigh."""
    loop, calls = _gate_loop(monkeypatch, usage=CACHED, phase="orient", stages=STAGES,
                             entered=("orient",), stage_mark=("", ()))
    loop.messages = [{"role": "user", "content": "x" * 4_000} for _ in range(30)]
    compact_if_needed(loop, endpoint=None, ref=REF)
    assert not calls


def test_the_pass_the_eviction_warning_defers_is_still_the_boundarys(monkeypatch):
    """The warning promises "the archive happens on your next turn either way". The boundary's
    decision applies to the boundary turn alone, so the owed pass must carry it — cap, stage
    and the economics that decided it — or the next turn re-tests the size gate, finds the
    prompt under it and the once-per-run warning was spent on nothing."""
    loop, calls = _gate_loop(monkeypatch, usage=CACHED, phase="orient", stages=STAGES,
                             entered=("orient",), stage_mark=("", ()))
    events = _events(loop)
    loop._evict_warned = False
    compact_if_needed(loop, endpoint=None, ref=REF)
    assert not calls, "the warning defers the pass by exactly one turn"
    assert "about to be ARCHIVED" in loop.messages[-1]["content"]
    loop.messages.append({"role": "user", "content": "OBSERVATION (util x, exit 0):\nok"})
    compact_if_needed(loop, endpoint=None, ref=REF)      # same stage: no boundary this turn
    assert calls, "the archive the warning announced must happen on the next turn"
    passes = [p for t, p in events if t == "compaction"]
    assert passes and passes[-1]["anticipated"] == "orient"
    assert passes[-1]["economics"]["compact"] is True


# ---- a real run context ----------------------------------------------------------------------

def test_a_real_run_reads_its_stages_and_turn_budget(make_routine, monkeypatch):
    """`stage_coverage` and `turns_remaining` on a real RunContext — the two inputs the stub
    fakes — feed the same decision: two declared stages, the first recorded via the run's own
    cursor at turn 60, 400 turns of budget."""
    from helpers import run_context
    from rsched.engine.loop import EngineLoop
    from test_loop import TS, _server

    d = make_routine(slug="stagey", budgets={"max_turns": 400},
                     workflow_md="# Flow\n\nRun `stages/gather.md`, then `stages/report.md`.\n")
    (d / "stages").mkdir()
    (d / "stages" / "gather.md").write_text("# Gather\n", encoding="utf-8")
    (d / "stages" / "report.md").write_text("# Report\n", encoding="utf-8")
    loop = EngineLoop(run_context(d, TS, server=_server(d), registry=None), "## Run flow", "i")
    loop.messages = [{"role": "user", "content": "x" * 6_070} for _ in range(40)]
    loop.ctx.usage.update(CACHED)
    loop.ctx.budgets.max_total_tokens = -1
    calls: list[str] = []
    monkeypatch.setattr(window.archival, "start", lambda *_a, **_k: calls.append("llm"))
    loop._evict_warned = True
    compact_if_needed(loop, None, REF)                  # first look: where the run stands
    assert not calls
    loop.ctx.turn = 60
    loop.ctx.phases_recorded.append("gather")           # the run wrote state/phase.json
    compact_if_needed(loop, None, REF)
    assert calls, "60 turns per stage × 2 stages ahead repays the ~48-turn breakeven twice"
