"""An obligation says WHO its next move is on, and the owed-work sweep routes it (2026-11-19).

The blocked-work list carried seven routines' `obligation_overdue` rows, and most of them could
never be cleared by the routine named: a video only the operator can approve, a payment only he
can make, a host change no sandbox reaches, a film not released yet, a feature another routine
owes. Each run re-answered "unmet" for them forever. So an unmet goal may say
`b<n> waits on <who>: <act>` (engine/accounting.py): `operator` → ONE Decisions card he settles
(done / drop / still wanted), `external` → parked for `EXTERNAL_DAYS`, a routine that runs →
that routine's move. Only what the routine itself owes stays overdue work.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from conftest import authed_client, finish, seeded_server
from helpers import server_for
from rsched import pending
from rsched.daemon import owed_watch
from rsched.daemon.owed_watch import OwedWatch
from rsched.engine import accounting, obligations
from rsched.engine.runtime import run_routine
from rsched.paths import atomic_write_json

OLD = (datetime.now(UTC) - timedelta(days=12)).date().isoformat()
TODAY = datetime.now(UTC).date()


def _owe(d, *rows):
    atomic_write_json(d / obligations.FILE, {"open": [
        {"id": i, "text": f"ask {i}", "quote": f"ask {i}", "since": OLD, "from_run": "r:1",
         "remains": f"left {i}", **({"waits": w, "waits_since": OLD} if w else {})}
        for i, w in rows]})


def test_waits_on_parses_as_unmet_and_names_its_addressee():
    entries = ["b1 waits on operator: approve the video cut", "b2 unmet: still drafting",
               "b3 waits on scheduler-builder: R3386 ships the submit button"]
    parsed = accounting.parse(entries)
    assert parsed["b1"] == ("unmet", "approve the video cut")
    assert parsed["b3"][0] == "unmet"
    assert accounting.waits(entries) == {"b1": "operator", "b3": "scheduler-builder"}


LOOK = {"say": "Reading the recipe.", "kind": "read_file", "path": "main.md"}


def test_a_run_records_who_an_obligation_waits_on(make_routine, scripted):
    d = make_routine(slug="maker")
    _owe(d, ("b1", ""))
    scripted([LOOK, {**finish(), "accounting": ["b1 waits on operator: he approves the cut"]}])
    assert run_routine(d, server_for(d), run_ts="20261119-100000")[0] == "ok"
    [row] = obligations.load(d)
    assert row["waits"] == "operator" and row["remains"] == "he approves the cut"
    first = row["waits_since"]
    # a second run saying the same keeps the day it FIRST waited
    scripted([LOOK, {**finish(), "accounting": ["b1 waits on operator: he approves the cut"]}])
    assert run_routine(d, server_for(d), run_ts="20261119-110000")[0] == "ok"
    assert obligations.load(d)[0]["waits_since"] == first
    # and a plain `unmet` takes it back as the routine's own
    scripted([LOOK, {**finish(), "accounting": ["b1 unmet: I will cut it myself after all"]}])
    assert run_routine(d, server_for(d), run_ts="20261119-120000")[0] == "ok"
    assert "waits" not in obligations.load(d)[0]


def test_only_the_routines_own_moves_are_overdue(tmp_path, make_routine):
    seeded_server(tmp_path)
    d = make_routine(slug="owes")
    make_routine(slug="builder")
    _owe(d, ("b1", ""), ("b2", "operator"), ("b3", "external"), ("b4", "builder"),
         ("b5", "ghost-routine"))
    owed = {o["id"] for o in owed_watch.overdue(d.parent, TODAY)["owes"]}
    # the routine's own, and a move owed by a routine that starts no run (nobody can make it)
    assert owed == {"b1", "b5"}
    # the world gets EXTERNAL_DAYS, then it is the routine's to chase again
    later = TODAY + timedelta(days=owed_watch.EXTERNAL_DAYS + 1)
    assert "b3" in {o["id"] for o in owed_watch.overdue(d.parent, later)["owes"]}


def test_what_waits_on_the_operator_becomes_his_card_and_his_word_settles_it(tmp_path,
                                                                            make_routine):
    server = seeded_server(tmp_path)
    home = server.routines_home
    d = make_routine(slug="owes")
    _owe(d, ("b1", "operator"), ("b2", "operator"), ("b3", "operator"))
    OwedWatch(server).check(TODAY)
    cards = [r for r in pending.load_all(home) if r["kind"] == owed_watch.OWED_KIND]
    assert {c["fields"]["id"] for c in cards} == {"b1", "b2", "b3"}
    OwedWatch(server).check(TODAY + timedelta(days=1))          # one card each, not two
    assert len([r for r in pending.load_all(home) if r["kind"] == owed_watch.OWED_KIND]) == 3
    by = {c["fields"]["id"]: c for c in cards}

    with authed_client(server) as c:
        for gid, choice in (("b1", "met"), ("b2", "drop"), ("b3", "keep")):
            r = c.post(f"/api/pending-creations/{by[gid]['id']}/materialize",
                       json={"choice": choice, "note": "seen it"})
            assert r.status_code == 200, r.text
    assert [o["id"] for o in obligations.load(d)] == ["b3"]          # kept stays open
    settled = json.loads((d / obligations.FILE).read_text())["settled"]
    assert {(s["id"], s["how"]) for s in settled} == {("b1", "met"), ("b2", "dropped")}
    told = [json.loads(p.read_text()) for p in (d / "inbox").glob("msg-*.json")]
    assert any("marked it DONE" in m.get("text", "") and "seen it" in m["text"] for m in told)
    # "still wanted" holds the next card off for KEPT_DAYS
    OwedWatch(server).check(TODAY + timedelta(days=2))
    assert not [r for r in pending.load_all(home) if r["kind"] == owed_watch.OWED_KIND]


def test_a_run_in_flight_cannot_resurrect_what_he_settled(make_routine, scripted):
    d = make_routine(slug="owes")
    _owe(d, ("b1", "operator"))
    # the run carries b1 at boot; the operator settles it before the run finishes
    scripted([LOOK, {**finish(), "accounting": ["b1 waits on operator: still his"]}])
    original = obligations.save

    def settle_first(ctx):
        obligations.settle(d, "b1", "r:1", how="met")
        original(ctx)

    obligations.save = settle_first
    try:
        assert run_routine(d, server_for(d), run_ts="20261119-130000")[0] == "ok"
    finally:
        obligations.save = original
    assert obligations.load(d) == []
