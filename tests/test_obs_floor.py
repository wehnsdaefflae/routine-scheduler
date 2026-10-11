"""The observation renderer's two floors (engine/obs_floor.py) and the suite's render guard.

The specimen is scheduler-builder:20261011-120001 (2026-10-11): a memory_write edit that would
have left its note at 112 lines was correctly REFUSED, the refusal reached a renderer that read
the `lines` of a success, and the KeyError ended the engine after four shipped releases — then
ended the resume at turn 0, because a resumed leg re-renders the stored observation. That is the
fourth defect of the kind, so these tests hold the CLASS: every action kind words an error
result, a renderer that raises costs a run one line and never the run, and a test that produces
an observation proves its words (tests/render_guard.py).
"""

import pytest

import render_guard
from helpers import health_events, run_context, server_config, server_for
from rsched.engine import executor, obs_files, obs_floor, observations
from rsched.engine.actions import MEMORY_NOTE_MAX_LINES
from rsched.engine.actionschema import KINDS
from rsched.engine.history import replay_messages
from rsched.engine.observations import format_observation
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20261011-120001"
SENTINEL = "E-7f3a: the action could not do what it was asked"
#: The identity keys real error observations carry (fsops, fileops, memops, executor, subruns).
IDENTITY = {"path": "state/x.md", "name": "x", "handle": "bg-1", "slug": "x", "n": 1}

# ---- floor 1: an error result, for every action kind --------------------------------------


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("extra", [{}, IDENTITY], ids=["bare", "identified"])
def test_every_action_kind_words_an_error_result_and_shows_it(kind, extra):
    """No kind may raise on an error result or render it as something else: before obs_floor,
    19 of the 33 kinds did one or the other (memory_write among them)."""
    text = format_observation({"kind": kind, "error": SENTINEL, **extra})
    assert SENTINEL in text
    assert text.startswith("OBSERVATION (")


@pytest.mark.parametrize("kind", sorted(set(KINDS) - obs_floor.OWN_ERRORS))
def test_a_kind_without_error_wording_of_its_own_gets_the_generic_failure(kind):
    assert format_observation({"kind": kind, "path": "state/x.md", "error": SENTINEL}) == (
        f"OBSERVATION ({kind} state/x.md FAILED): {SENTINEL}")


@pytest.mark.parametrize("kind", sorted(obs_floor.OWN_ERRORS))
def test_a_kind_listed_as_wording_its_own_errors_really_does(kind, monkeypatch):
    """The other direction: membership is a claim about the kind's renderer, so it is checked
    with the generic line switched OFF — a kind listed here whose renderer ignores `error` would
    otherwise be shown a success (or raise) and pass because the floor covered for it."""
    monkeypatch.setattr(obs_floor, "failed", lambda obs, kind: None)
    for extra in ({}, IDENTITY):
        assert SENTINEL in format_observation({"kind": kind, "error": SENTINEL, **extra})


def test_the_own_errors_list_names_only_real_kinds():
    assert set(KINDS) >= obs_floor.OWN_ERRORS


def test_the_generic_failure_names_what_the_action_was_about():
    assert format_observation({"kind": "move", "src": "a.md", "dst": "b/a.md",
                               "error": "no such file"}) == (
        "OBSERVATION (move a.md → b/a.md FAILED): no such file")
    long = "x" * 900   # the generic JSON line cut a long error at 500 characters
    assert long in format_observation({"kind": "delete", "path": "p", "error": long})


# ---- the incident: memory_write's refusals --------------------------------------------------


def _ctx(make_routine, tmp_path):
    return run_context(make_routine(slug="memfloor"), TS,
                       server=server_config(libraries_home=tmp_path / "libraries"))


def test_each_memory_write_refusal_reads_as_one(make_routine, tmp_path):
    ctx = _ctx(make_routine, tmp_path)
    body = "\n".join(f"- fact {i}" for i in range(MEMORY_NOTE_MAX_LINES))
    executor.dispatch({"kind": "memory_write", "name": "big", "content": body,
                       "about": "a mature note"}, ctx)
    edit = {"kind": "memory_write", "name": "big", "anchor": "- fact 0"}

    over = format_observation(executor.dispatch({**edit, "replacement": "a\nb\nc"}, ctx))
    assert over.startswith("OBSERVATION (memory_write big.md NOT written): the edit would leave "
                           f"the note at {MEMORY_NOTE_MAX_LINES + 2} lines")
    stale = format_observation(executor.dispatch({**edit, "anchor": "- gone"}, ctx))
    assert "NOT written" in stale and "anchor not found" in stale
    missing = format_observation(executor.dispatch({**edit, "name": "nothing"}, ctx))
    assert "NOT written" in missing and missing.endswith("Existing topics: big.")


def test_the_incident_run_reads_its_refusal_and_resumes(make_routine, scripted):
    """scheduler-builder:20261011-120001, end to end: the refused edit is read, the run finishes
    on its own summary, and a resume replays the stored refusal without raising."""
    d = make_routine(slug="memincident")
    body = "\n".join(f"- fact {i}" for i in range(MEMORY_NOTE_MAX_LINES - 4))
    over = {"say": "Recording the look.", "kind": "memory_write", "name": "ui",
            "anchor": "- fact 0", "replacement": "\n".join(f"- new {i}" for i in range(12))}
    ep = scripted([{"say": "Seeding.", "kind": "memory_write", "name": "ui", "content": body,
                    "about": "how to look at the UI"},
                   over,
                   {"say": "Done.", "kind": "finish", "status": "ok", "summary": "four shipped"}])
    assert run_routine(d, server_for(d), run_ts=TS)[0] == "ok"
    seen = ep.calls[2]["messages"][-1]["content"]
    assert "memory_write ui.md NOT written" in seen and "notes are capped at 100" in seen

    scripted([{"say": "Again.", "kind": "finish", "status": "ok", "summary": "resumed"}])
    assert run_routine(d, server_for(d), run_ts=TS, resume_from=TS)[0] == "ok"


# ---- floor 2: a renderer that raises --------------------------------------------------------


@pytest.fixture
def broken_memory_read(monkeypatch):
    """The production floor back in place, and memory_read's renderer broken the way 0.410.0
    broke memory_write's — a success field read off a result that does not carry it."""
    monkeypatch.setattr(obs_floor, "defect_text", render_guard.REAL_DEFECT_TEXT)
    real = obs_files.format_files

    def breaking(obs, kind):
        if kind == "memory_read":
            return f"{obs['lines']}"
        return real(obs, kind)

    monkeypatch.setattr(obs_files, "format_files", breaking)


OBS = {"kind": "memory_read", "name": "ui", "content": "the note", "media": [{"data": "QUJD"}]}


@pytest.mark.usefixtures("broken_memory_read")
def test_a_raising_renderer_shows_the_raw_result_and_names_the_defect():
    text, defect = observations.render_observation(OBS)
    assert defect is not None
    assert defect.startswith("KeyError: 'lines' at ")
    assert "the engine could not word this result: KeyError: 'lines'" in text
    assert '"content": "the note"' in text          # the result itself, so the run can act
    assert "QUJD" not in text                       # never the media bytes
    assert format_observation(OBS) == text          # a replay rebuilds the prompt byte for byte


@pytest.mark.usefixtures("broken_memory_read")
def test_a_live_defect_is_recorded_once_and_a_replay_records_nothing(make_routine, scripted):
    d = make_routine(slug="renderboom")
    ep = scripted([{"say": "Reading.", "kind": "memory_read", "name": "ui"},
                   {"say": "Done.", "kind": "finish", "status": "ok", "summary": "it went on"}])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)

    assert status == "ok"                           # the defect cost a line, not the run
    assert "the engine could not word this result" in ep.calls[1]["messages"][-1]["content"]
    events, _ = read_events(run_dir / "transcript.jsonl")
    errors = [e for e in events if e["type"] == "error" and e["payload"]["where"] == "render"]
    assert len(errors) == 1 and errors[0]["payload"]["kind"] == "memory_read"
    rows = health_events(d.parent, event=obs_floor.EVENT, routine="renderboom")
    assert len(rows) == 1 and "KeyError: 'lines'" in rows[0]["detail"]

    replay_messages(events)                         # a resume: same words, nothing filed
    assert len(health_events(d.parent, event=obs_floor.EVENT)) == 1


def test_the_event_is_an_incident_not_a_standing_condition():
    from rsched.health_events import REPEAT_COOLDOWN_HOURS
    assert obs_floor.EVENT not in REPEAT_COOLDOWN_HOURS


# ---- the guard itself -----------------------------------------------------------------------


def test_in_the_suite_a_renderer_defect_fails_instead_of_falling_back(monkeypatch):
    monkeypatch.setattr(obs_files, "format_files", lambda obs, kind: obs["lines"])
    with pytest.raises(AssertionError, match="renderer raised on a 'memory_read' result"):
        format_observation({"kind": "memory_read", "name": "ui"})


def test_every_observation_a_dispatch_seam_returns_is_rendered(make_routine, tmp_path,
                                                               monkeypatch):
    rendered = []
    monkeypatch.setattr(render_guard, "format_observation", rendered.append)
    obs = executor.dispatch({"kind": "memory_read", "name": "nothing"},
                            _ctx(make_routine, tmp_path))
    assert rendered == [obs]
