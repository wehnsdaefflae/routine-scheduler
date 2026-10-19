"""`wrong_turn_detected`: the turn that came back WELL-FORMED AND WRONG, filed where a rate
can be read (R2652, first increment — MEASUREMENT ONLY).

`engine/degrade.py` is documented as "when a turn comes back WRONG" and carries four ordered
nets — but all four fire on a turn that failed to ARRIVE (a transport error, an empty reply, a
classifier refusal). A well-formed but WRONG turn is, to that ladder, a success: the engine
rejects it, re-drives it, and the only trace is a transcript `error` event and two telemetry
counters nothing files. So the thing the later effort-ladder increment needs — HOW OFTEN a turn
comes back wrong, at which turn of a run, and at what effort — is measurable on this instance
nowhere.

This increment adds the TRIGGER and nothing else: one health event on the failures the engine
ALREADY SEES, in the machine-checked vocabulary, readable through the reader that already
exists. No effort is changed, no model is swapped, no retry is added, no routing layer appears
— `test_nothing_about_effort_or_model_resolution_changed` is what holds that line, and it is
meant to go red on the day someone wires the cascade here instead of deciding to.
"""

from __future__ import annotations

import ast
from pathlib import Path

from conftest import authed_client, finish, make_test_server
from helpers import health_events, server_for
from rsched.engine import wrongturn
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20261019-090000"

#: A valid, side-effect-free action — the recovered turn after a rejection, and the action the
#: repeat tests send three times.
READ = {"say": "Reading the recipe.", "kind": "read_file", "path": "main.md"}


def _rows(home: Path) -> list[dict]:
    return health_events(home, event=wrongturn.EVENT)


def _run(make_routine, scripted, replies, *, slug="wrongr", ts=TS):
    """One scripted run, returning (routines_home, endpoint, status, transcript events)."""
    d = make_routine(slug=slug)
    ep = scripted(replies)
    status, run_dir = run_routine(d, server_for(d), run_ts=ts)
    events, _ = read_events(run_dir / "transcript.jsonl")
    return d.parent, ep, status, events


def _accepted(events: list[dict]) -> list[str]:
    """The action kinds the run actually LANDED, in order — what a behaviour change would move."""
    return [e["payload"]["kind"] for e in events if e["type"] == "assistant_action"]


# --- each trigger files exactly one row -------------------------------------------------


def test_a_rejected_action_files_one_row(make_routine, scripted):
    """The commonest visible failure: a reply that is not a valid action. The engine rejects
    it, corrects the model and the turn lands on the next attempt — nothing fails, which is
    exactly why nothing recorded it."""
    home, _ep, status, events = _run(make_routine, scripted, [
        "utter prose, no JSON at all",          # rejected: the turn came back WRONG
        READ,                                   # …and the same turn lands on attempt 2
        finish(summary="done after one rejection"),
    ])
    assert status == "ok"
    rows = _rows(home)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["event"] == wrongturn.EVENT
    assert row["reason"] == "schema_rejected"
    assert row["reason"] in wrongturn.REASONS
    assert row["routine"] == "wrongr"
    assert row["run_id"].endswith(TS)
    assert row["turn"] == 1                     # the turn being composed, not the last landed
    assert row["model"]                         # the corpus is per (model, effort), never bare
    assert row["detail"]
    # the run is untouched: the same two actions it would have landed without this event
    assert _accepted(events) == ["read_file", "finish"]


def test_the_same_invalid_reply_twice_is_filed_as_a_repeat(make_routine, scripted):
    """A model that returns the IDENTICAL invalid action after being corrected is the signature
    the later increment is built for (the correction taught it nothing), so it is its own
    reason rather than a second rejection row."""
    home, _ep, status, _events = _run(make_routine, scripted, [
        "the same unparseable reply",
        "the same unparseable reply",
        READ,
        finish(summary="done after a repeat"),
    ])
    assert status == "ok"
    rows = _rows(home)
    assert [r["reason"] for r in rows] == ["schema_rejected", "repeat_invalid"]
    assert all(r["turn"] == 1 for r in rows), rows


def test_a_turn_that_spends_every_attempt_is_filed(make_routine, scripted):
    """The hard end of the same seam: every schema attempt spent and no action accepted, so
    the turn that was due produced nothing at all."""
    home, _ep, status, _events = _run(make_routine, scripted,
                                      ["nope", "still nope", "nope again"])
    assert status == "failed"
    reasons = [r["reason"] for r in _rows(home)]
    assert "attempts_exhausted" in reasons, reasons
    exhausted = [r for r in _rows(home) if r["reason"] == "attempts_exhausted"]
    assert len(exhausted) == 1, exhausted
    assert exhausted[0]["turn"] == 1


def test_an_identical_action_repeated_is_filed(make_routine, scripted):
    """The other half of "wrong in a way the engine can already SEE": an action that is
    perfectly schema-valid and is the same one again. The engine already warns at
    `REPEAT_WARN` and already fails the run at `REPEAT_FAIL`; what it never did was say so
    where a rate can be read."""
    home, _ep, status, events = _run(make_routine, scripted, [
        READ, READ, READ,                       # streak 1, 2, 3 — the engine's own warn line
        finish(summary="done after a repeat streak"),
    ])
    assert status == "ok"
    rows = [r for r in _rows(home) if r["reason"] == "action_repeat"]
    assert len(rows) == 1, rows
    assert rows[0]["streak"] == 3               # the engine's REPEAT_WARN, not a new threshold
    assert rows[0]["turn"] == 3
    # four actions landed, exactly as they would have without the event
    assert _accepted(events) == ["read_file", "read_file", "read_file", "finish"]


def test_one_row_per_reason_and_turn(make_routine, scripted):
    """Two rejections inside ONE turn are one row, not two. `health_cooldown`'s docstring holds
    what per-occurrence filing did to this stream once (26 rows of 1,289 from a single run);
    the corpus this event feeds is keyed on (turn, effort), so the turn is the unit."""
    home, _ep, status, _events = _run(make_routine, scripted, [
        "prose, attempt one",
        "different prose, attempt two",         # rejected again, NOT a repeat of the raw text
        READ,
        finish(summary="done after two rejections in one turn"),
    ])
    assert status == "ok"
    rows = [r for r in _rows(home) if r["reason"] == "schema_rejected"]
    assert len(rows) == 1, rows
    assert rows[0]["turn"] == 1


def test_a_clean_run_files_nothing(make_routine, scripted):
    """The event has to be worth reading: a run whose turns were all right must not appear."""
    home, _ep, status, _events = _run(make_routine, scripted, [
        READ,
        {"say": "Reading it again, differently.", "kind": "read_file", "path": "LEDGER.md"},
        finish(summary="nothing came back wrong"),
    ])
    assert status == "ok"
    assert _rows(home) == []


# --- the event is REGISTERED in every surface a reader uses ------------------------------


def test_the_event_is_in_the_documented_vocabulary():
    """`health_events.py`'s header enum IS the vocabulary and is machine-checked both ways
    (tests/test_health_events.py). An event missing from it is read by nobody — the
    `prompt_oversize_shrunk` failure, written for weeks past every documented filter."""
    from rsched import health_events as he

    doc = he.__doc__ or ""
    head = doc.split('"routine": <slug>')[0]
    assert f'"{wrongturn.EVENT}"' in head, "the event is not in the header enum's event list"
    assert wrongturn.EVENT in doc, "the enum entry carries no paragraph saying what it means"


def test_the_event_reaches_the_blocked_work_surface():
    """`GET /api/health/blocked` folds exactly `BLOCKED_EVENTS`, and that fold is the only
    in-product reader of this stream. An unlisted event is written and read by nobody."""
    from rsched.readmodels.health_stream import BLOCKED_EVENTS

    assert wrongturn.EVENT in BLOCKED_EVENTS, "invisible to the console"
    assert BLOCKED_EVENTS[wrongturn.EVENT].strip(), "a label a reader can act on"


def test_the_event_is_not_repeat_suppressed_by_the_cooldown_table():
    """Nothing in `BLOCKED_EVENTS` may be named in `REPEAT_COOLDOWN_HOURS`: the blocked-fleet
    fold COUNTS rows, so a suppressed one makes the fleet look less stuck than it is — and a
    rate is the whole point of this event. The (reason, turn) latch is the bound instead."""
    from rsched.health_cooldown import REPEAT_COOLDOWN_HOURS

    assert wrongturn.EVENT not in REPEAT_COOLDOWN_HOURS


def test_a_real_runs_row_is_served_by_the_blocked_endpoint(make_routine, scripted, tmp_path):
    """End to end, through the reader that exists: a run trips a trigger, and the row the
    engine wrote is served by `GET /api/health/blocked` with its vocabulary label. Proving the
    registration by inspection is what left six events unreadable for a year."""
    home, _ep, status, _events = _run(make_routine, scripted, [
        "prose instead of an action",
        READ,
        finish(summary="served"),
    ])
    assert status == "ok" and len(_rows(home)) == 1

    with authed_client(make_test_server(tmp_path)) as client:
        payload = client.get("/api/health/blocked?days=7").json()
    served = [r for r in payload["rows"] if r["event"] == wrongturn.EVENT]
    assert len(served) == 1, payload
    assert served[0]["subject"] == "wrongr"
    assert served[0]["count"] == 1
    assert served[0]["means"] == payload["vocabulary"][wrongturn.EVENT]
    assert served[0]["means"].strip()


# --- NO BEHAVIOUR CHANGE: this increment measures, and that is all ------------------------

#: Every call by which a module could RESOLVE a model or re-point one mid-turn. The trigger
#: module may READ what the turn ran at; it may not reach the machinery that decides what the
#: next turn runs at. R2652's first increment is deliberately the trigger alone — the cascade is
#: the increment this one exists to justify, and wiring it here instead of DECIDING it is what
#: this guard prevents.
FORBIDDEN_CALLS = frozenset({
    "for_model", "for_model_chain", "for_name", "for_uncensored", "for_system",
    "resolve", "resolve_chain", "pick", "complete", "note_prompt_size",
    "apply_model_switch", "_adopt_model", "_override_window",
})

#: Attributes whose ASSIGNMENT would move what a turn runs at. Reading any of them is the
#: point of the event (the corpus is per model and effort); writing one is the next increment.
FORBIDDEN_ASSIGNMENTS = frozenset({
    "effort", "efforts", "main_model", "configured_model", "models", "model",
    "action_schema", "_schema_off", "_shed_schema_turns",
})

#: Modules that CHANGE a turn rather than record one. A measurement module importing any of
#: them is the shape this increment must not have.
FORBIDDEN_IMPORTS = frozenset({"degrade", "switches", "endpoints", "registry", "completion",
                               "turntakeover", "refusal", "window"})


def _module_ast() -> ast.Module:
    return ast.parse(Path(wrongturn.__file__).read_text(encoding="utf-8"))


def test_the_trigger_module_resolves_nothing():
    """A static guard on the one module this increment adds, over its AST rather than its text:
    what it CALLS, what it ASSIGNS and what it IMPORTS. Its prose names `engine/degrade.py` —
    that ladder is the whole subject of the finding — so a textual scan would read the
    explanation as the offence.
    """
    tree = _module_ast()
    called = {node.func.attr if isinstance(node.func, ast.Attribute)
              else getattr(node.func, "id", "")
              for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not (called & FORBIDDEN_CALLS), (
        f"wrongturn.py calls {sorted(called & FORBIDDEN_CALLS)} — this increment emits a "
        f"MEASUREMENT; resolving a model or re-pointing a turn here is the next one")

    assigned: set[str] = set()
    for node in ast.walk(tree):
        targets = (list(node.targets) if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AugAssign | ast.AnnAssign)
                   else [])
        assigned |= {t.attr for t in targets if isinstance(t, ast.Attribute)}
    assert not (assigned & FORBIDDEN_ASSIGNMENTS), (
        f"wrongturn.py assigns {sorted(assigned & FORBIDDEN_ASSIGNMENTS)} — a measurement reads "
        f"what the turn ran at and changes nothing about what the next one will")

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {(node.module or "").rsplit(".", 1)[-1]}
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.Import):
            imported |= {a.name.rsplit(".", 1)[-1] for a in node.names}
    assert not (imported & FORBIDDEN_IMPORTS), (
        f"wrongturn.py imports {sorted(imported & FORBIDDEN_IMPORTS)} — a module that changes "
        f"a turn, not one that records it")


def test_the_repeat_threshold_is_the_engines_own():
    """The streak this module files on is the streak the LOOP already warns the run about — one
    reading of "the run is stuck", not two that drift apart."""
    from rsched.engine.loop import REPEAT_FAIL, REPEAT_WARN

    assert wrongturn.WARN_STREAK == REPEAT_WARN
    assert wrongturn.WARN_STREAK < REPEAT_FAIL   # filed at the onset, before the run is ended


def test_nothing_about_effort_or_model_resolution_changed(make_routine, scripted):
    """The assertion the item asks for in the negative: the effort and the model the engine
    resolves are IDENTICAL across a run that trips a trigger and one that does not, and
    identical on every attempt within the tripping turn — a rejected turn is retried on the
    same model at the same effort, exactly as before.
    """
    _clean_home, clean_ep, clean_status, _ev = _run(
        make_routine, scripted, [READ, finish(summary="clean")], slug="cleanr")
    home, trip_ep, trip_status, events = _run(
        make_routine, scripted,
        ["prose, not an action", READ, finish(summary="tripped")], slug="tripr")
    assert clean_status == "ok" and trip_status == "ok"
    assert len(_rows(home)) == 1                     # the trigger really did fire

    clean_efforts = {c["effort"] for c in clean_ep.calls}
    trip_efforts = {c["effort"] for c in trip_ep.calls}
    assert len(trip_efforts) == 1, trip_efforts       # no mid-run escalation
    assert trip_efforts == clean_efforts              # …and not a different level either
    clean_models = {c["model"] for c in clean_ep.calls}
    trip_models = {c["model"] for c in trip_ep.calls}
    assert len(trip_models) == 1 and trip_models == clean_models   # no model swap

    # and no new call was added to the turn: one rejection costs one extra attempt, the
    # attempt the schema-retry cycle already spent before this event existed.
    assert len(trip_ep.calls) == len(clean_ep.calls) + 1
    assert _accepted(events) == ["read_file", "finish"]
