"""An approved recipe revision survives a revise leg that dies before it edits anything."""

from __future__ import annotations

import pytest

from conftest import authed_client, finish, make_test_server, mk_run
from helpers import server_for
from rsched import pending
from rsched.endpoints.base import EndpointError
from rsched.engine import recipeproposal, revisearm
from rsched.engine.inbox import file_message
from rsched.engine.revise import NARROW, REVISE_MARKER, write_revise_marker
from rsched.engine.runtime import run_routine
from rsched.paths import read_json

TS = "20261106-090000"


def _queued(srv, slug: str, ts: str, lines: list[str]) -> dict:
    return pending.queue(srv, kind=recipeproposal.KIND, routine=slug, run_id=f"{slug}:{ts}",
                         fields={"lines": lines, "run_id": f"{slug}:{ts}"},
                         summary="proposes a revision to its own recipe")


async def _resumed(cfg, ts=None, *, reason=""):
    return f"{cfg.slug}:{ts}"


async def _refused(cfg, ts=None, *, reason=""):
    return None


@pytest.fixture
def web(tmp_path, make_routine):
    make_routine(slug="rev")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, tmp_path


# ---- the route: a durable record first, and the card is not consumed by arming ---------------

def test_arming_records_the_approved_lines_and_leaves_the_card_standing(web, monkeypatch):
    """The approval used to be consumed the moment the leg was armed, so a leg that died on its
    first model call destroyed it: the record is written BEFORE the resume and the card stays
    until that leg authors a finish, which is the only surface those lines have."""
    c, tmp = web
    run_dir = mk_run(tmp / "routines" / "rev", TS, "finished")
    rec = _queued(c.app.state.server, "rev", TS, ["stages/scan.md — read the cursor first"])
    monkeypatch.setattr(c.app.state.runner, "resume_terminal", _resumed)

    r = c.post(f"/api/pending-creations/{rec['id']}/revise")
    assert r.status_code == 200, r.text
    assert r.json()["arms"] == 1
    assert revisearm.approved_lines(run_dir) == ["stages/scan.md — read the cursor first"]
    assert revisearm.armed(run_dir)["proposal"] == rec["id"]
    assert [row["id"] for row in pending.load_all(tmp / "routines")] == [rec["id"]]


def test_a_refused_resume_gives_the_arming_attempt_back(web, monkeypatch):
    """No leg ran, so none was spent: a cap that charged for refusals would retire a card after
    three clicks the operator never got a leg out of."""
    c, tmp = web
    run_dir = mk_run(tmp / "routines" / "rev", TS, "finished")
    rec = _queued(c.app.state.server, "rev", TS, ["main.md — x"])
    monkeypatch.setattr(c.app.state.runner, "resume_terminal", _refused)

    assert c.post(f"/api/pending-creations/{rec['id']}/revise").status_code == 409
    assert revisearm.arms_spent(run_dir) == 0
    assert not (run_dir / REVISE_MARKER).exists()
    assert len(pending.load_all(tmp / "routines")) == 1


def test_a_card_whose_legs_keep_dying_stops_arming_at_the_cap(web, monkeypatch):
    """The cap is why keeping the approval cannot become a loop; and the card is TOLD it, by the
    one predicate the route refuses with, so the refusal is never discovered by clicking."""
    c, tmp = web
    run_dir = mk_run(tmp / "routines" / "rev", TS, "finished")
    rec = _queued(c.app.state.server, "rev", TS, ["main.md — x"])
    monkeypatch.setattr(c.app.state.runner, "resume_terminal", _resumed)

    for _ in range(revisearm.MAX_ARMS):
        assert c.post(f"/api/pending-creations/{rec['id']}/revise").status_code == 200

    [row] = c.get("/api/pending-creations").json()
    r = c.post(f"/api/pending-creations/{rec['id']}/revise")
    assert r.status_code == 409
    assert row["revise_blocked"] == r.json()["detail"]
    assert str(revisearm.MAX_ARMS) in row["revise_blocked"]
    assert revisearm.arms_spent(run_dir) == revisearm.MAX_ARMS


def test_a_re_arm_keeps_the_operators_own_wording(web, monkeypatch):
    """He edited the lines on the card and the leg he gave them to died. The card's own `lines`
    stay the run's, so a plain second `apply it` would quietly resume on the phrasing he
    replaced — the recorded text is what the next leg is told, and still as HIS."""
    c, tmp = web
    run_dir = mk_run(tmp / "routines" / "rev", TS, "finished")
    rec = _queued(c.app.state.server, "rev", TS, ["main.md — tighten step 2"])
    monkeypatch.setattr(c.app.state.runner, "resume_terminal", _resumed)

    first = c.post(f"/api/pending-creations/{rec['id']}/revise",
                   json={"lines": ["main.md — delete step 2 entirely"]})
    assert first.status_code == 200 and first.json()["edited"] is True

    again = c.post(f"/api/pending-creations/{rec['id']}/revise")
    assert again.status_code == 200, again.text
    assert again.json()["lines"] == ["main.md — delete step 2 entirely"]
    assert again.json()["edited"] is True
    instruction = read_json(run_dir / REVISE_MARKER)["instruction"]
    assert "delete step 2 entirely" in instruction and "tighten step 2" not in instruction
    assert "THE LINES BELOW ARE HIS" in instruction


# ---- the leg's ending: only an AUTHORED finish consumes the approval -------------------------

def _probe() -> dict:
    return {"say": "Doing the task.", "kind": "write_file", "path": "state/probe.txt",
            "content": "probe"}


def _arm_leg(routine_dir, run_dir, card: dict, lines: list[str]) -> None:
    """What the approval route files: the durable record, the one-shot marker, the live message."""
    text = "The operator approved: " + "; ".join(lines)
    revisearm.arm(run_dir, proposal=card["id"], lines=lines)
    write_revise_marker(run_dir, text, leg=NARROW)
    file_message(routine_dir, text, via="web-converse")


def test_a_leg_whose_model_chain_dies_keeps_the_approval_and_says_so(make_routine, scripted):
    """The reported failure: the leg's FIRST call failed through the whole fallback chain and it
    closed `failed` having taken no action. The engine ended it, so the approval is not spent —
    the card still stands with its lines and the run's result says the edit was not made."""
    d = make_routine(slug="dies")
    server = server_for(d)
    scripted([_probe(), finish(summary="task done")])
    _status, run_dir = run_routine(d, server, run_ts=TS)
    card = _queued(server, "dies", TS, ["main.md — drop step 4"])
    _arm_leg(d, run_dir, card, ["main.md — drop step 4"])

    scripted([EndpointError("otari 502 Bad Gateway; chain exhausted")])
    run_routine(d, server, run_ts=TS, resume_from=TS)

    assert [row["id"] for row in pending.load_all(server.routines_home)] == [card["id"]]
    assert revisearm.approved_lines(run_dir) == ["main.md — drop step 4"]
    result = (run_dir / "result.md").read_text(encoding="utf-8")
    assert "was NOT applied" in result and card["id"] in result
    assert "can arm another leg" in result


def test_an_authored_finish_consumes_the_approval(make_routine, scripted):
    """The other half of the same rule: the leg read the lines and said what it did with them,
    which is the whole of what was approved — so the card goes and the record with it."""
    d = make_routine(slug="edits")
    server = server_for(d)
    scripted([_probe(), finish(summary="task done")])
    _status, run_dir = run_routine(d, server, run_ts=TS)
    card = _queued(server, "edits", TS, ["main.md — drop step 4"])
    _arm_leg(d, run_dir, card, ["main.md — drop step 4"])

    scripted([{"say": "Making the approved edit.", "kind": "edit_file", "path": "main.md",
               "anchor": "as few actions as possible", "replacement": "as few actions as needed"},
              finish(summary="main.md: step 1 reworded")])
    run_routine(d, server, run_ts=TS, resume_from=TS)

    assert pending.load_all(server.routines_home) == []
    assert not revisearm.record_path(run_dir).exists()
    assert "was NOT applied" not in (run_dir / "result.md").read_text(encoding="utf-8")


def test_a_leg_that_spends_its_window_without_finishing_keeps_the_approval(make_routine,
                                                                          scripted):
    """Not only a dead chain: a leg the engine ends at its turn backstop never authored a
    finish either, so the approval it was given is still the operator's to spend."""
    from rsched.engine import reviseleg

    d = make_routine(slug="runsout")
    server = server_for(d)
    scripted([_probe(), finish(summary="task done")])
    _status, run_dir = run_routine(d, server, run_ts=TS)
    card = _queued(server, "runsout", TS, ["main.md — drop step 4"])
    _arm_leg(d, run_dir, card, ["main.md — drop step 4"])

    scripted([{"say": "Reading it back.", "kind": "read_file", "path": "main.md",
               "start_line": i + 1} for i in range(reviseleg.LEG_TURNS + 4)])
    run_routine(d, server, run_ts=TS, resume_from=TS)

    assert [row["id"] for row in pending.load_all(server.routines_home)] == [card["id"]]
    assert "was NOT applied" in (run_dir / "result.md").read_text(encoding="utf-8")
