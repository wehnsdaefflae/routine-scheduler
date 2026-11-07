"""D152 step 7: the ledger read back AS hooks, and the first DECISION taken from the stream.

Step 4's claim was "the hook is the source" — written first, the ledger derived. This step's
claim is stronger and is what these tests pin: the derivation is INVERTIBLE (so the history
that predates the stream can be carried into it exactly, not approximately), and one decision
in the engine now answers from the stream instead of from the projection.

The round-trip is tested over GENERATED rows here and over the LIVE ledger in
`test_report_hooks_roundtrip_live` — the live one is the measurement that mattered: 5,016 rows,
zero mismatches, which is why the backfill carries history rather than reconstructing it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rsched.hooks.reportinv import hook_of_row, open_threads_now, still_owed_now
from rsched.hooks.reportproj import rows_for
from rsched.report_disposal import Disposal
from rsched.reports import file_report, read_reports, reports_path

LEDGER = Path("/home/mark/routines/.control/reports.jsonl")

ROWS = [
    {"id": "R1", "ts": "2026-07-19T21:20:06+02:00", "routine": "a", "run_id": "a:1",
     "title": "t", "detail": "d"},
    {"id": "R2", "ts": "2026-07-20T10:00:00+02:00", "routine": "a", "run_id": "a:2",
     "title": "t", "detail": "d", "target": "b"},
    {"id": "R3", "ts": "2026-07-21T10:00:00+02:00", "routine": "a", "run_id": "a:3",
     "title": "t", "detail": "d", "target": "b", "answers": "R2", "closes": True},
    {"id": "R4", "ts": "2026-07-22T10:00:00+02:00", "routine": "a", "run_id": "a:4",
     "title": "t", "detail": "d", "supersedes": ["R1"], "settles": ["R1"]},
    {"id": "R2", "event": "delivered", "ts": "2026-07-23T10:00:00+02:00", "run_id": "b:1"},
    {"id": "R1", "event": "superseded", "ts": "2026-07-24T10:00:00+02:00", "by": "R4",
     "to": "b"},
    {"id": "R5", "event": "retracted", "ts": "2026-07-25T10:00:00+02:00"},
    # The one live shape today's Disposal REFUSES (D196): closes with nothing to complete.
    # History must read back as what it says, so the inverse may not route through Disposal.
    {"id": "R1062", "ts": "2026-08-29T13:39:33+02:00", "routine": "a", "run_id": "a:9",
     "title": "t", "detail": "d", "closes": True},
]


@pytest.mark.parametrize("row", ROWS, ids=[f"{r['id']}-{r.get('event') or 'filing'}"
                                           for r in ROWS])
def test_every_ledger_row_shape_survives_the_round_trip(row):
    """`rows_for(hook_of_row(row)) == row` — the projection is invertible, which is what lets
    the pre-stream history be carried rather than reconstructed.
    """
    assert rows_for(hook_of_row(row)) == row


def test_the_refused_closes_shape_reads_back_without_raising():
    """A pre-D196 row (`closes` with neither answers nor settles) is history and must read, so
    the inverse assembles the payload directly instead of through `Disposal`, whose refusal
    belongs on the write path.
    """
    with pytest.raises(ValueError):
        Disposal(closes=True)                       # the write path still refuses it
    hook = hook_of_row({"id": "R1062", "ts": "t", "title": "x", "detail": "y",
                        "closes": True})
    assert hook.payload["closes"] is True


def test_a_row_without_an_id_yields_a_hook_the_backfill_skips():
    assert hook_of_row({"ts": "t", "title": "x"}).id == ""
    assert hook_of_row("not a dict").id == ""


@pytest.mark.skipif(not LEDGER.is_file(), reason="no live ledger on this host")
def test_report_hooks_roundtrip_live():
    """Every row of the LIVE ledger survives the round trip. This is the measurement the
    backfill rests on — it ran at 5,016 rows with zero mismatches.
    """
    rows = [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
    bad = [r for r in rows if rows_for(hook_of_row(r)) != r]
    assert bad == [], f"{len(bad)} of {len(rows)} live ledger rows do not round-trip"


def _file(home: Path, routine: str, target: str, n: int) -> str:
    """One filed report through the LIVE write path — which writes the hook first and the
    ledger row from it, so both stores are populated the way production populates them.
    """
    out = file_report(home, routine=routine, run_id=f"{routine}:{n}",
                      title=f"t{n}", detail="d", target=target)
    assert out is not None, "the filing was refused"
    return out[1]


def test_the_thread_cap_now_decides_from_the_hook_stream(tmp_path, monkeypatch):
    """The cap's count comes from `open_threads_now`, and with both stores written by the live
    path the stream and the ledger agree — so no divergence event is filed.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    for n in range(3):
        _file(home, "a", "b", n)
    rows = read_reports(reports_path(home))
    assert open_threads_now(home, rows, routine="a", target="b") == ["R1", "R2", "R3"]
    assert seen == [], "the stores agree, so nothing should have been filed"


def test_folding_the_stream_is_what_makes_the_two_agree(tmp_path, monkeypatch):
    """A thread DISPOSED OF by a lifecycle fact must read the same on both sides.

    The regression this pins, measured on the live instance 2026-11-06: five
    `hook_stream_diverged` events in one day across three sender/owner pairs, every one
    spurious. `stream_rows` is the RAW projection — a filing row and each lifecycle fact as
    separate rows — while `rows` is already folded, and `_is_open` reads `retracted` /
    `superseded` keys that only the fold writes onto a filing row. So an unfolded comparison
    counts every disposed-of row as still open: scheduler-builder -> self-audit read 19 open
    threads on the stream against the ledger's 3.

    Three filings where the third folds the first two is the live shape: the ledger holds one
    open thread, and the stream must say so too.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    _file(home, "a", "b", 1)
    _file(home, "a", "b", 2)
    out = file_report(home, routine="a", run_id="a:3", title="t3", detail="d", target="b",
                      supersedes=("R1", "R2"))
    assert out is not None and sorted(out[2]) == ["R1", "R2"], out
    rows = read_reports(reports_path(home))
    assert open_threads_now(home, rows, routine="a", target="b") == ["R3"]
    assert seen == [], f"the fold was not applied to the stream: {seen}"


def test_a_divergence_keeps_the_ledgers_answer_and_files_the_event(tmp_path,
                                                                   monkeypatch):
    """When the stream cannot account for a thread the ledger shows (an unmirrored write, or an
    incomplete backfill), the LEDGER's answer is used and `hook_stream_diverged` is filed.
    """
    home = tmp_path / "routines"
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    monkeypatch.setattr("rsched.hooks.reportinv.stream_rows", lambda _h: [])
    rows = [{"id": "R9", "ts": "t", "routine": "a", "run_id": "a:1", "title": "t",
             "detail": "d", "target": "b"}]
    assert open_threads_now(home, rows, routine="a", target="b") == ["R9"]
    assert len(seen) == 1 and seen[0]["stream"] == 0 and seen[0]["ledger"] == 1


def test_the_owed_reply_hold_agrees_with_the_ledger_over_the_stream(tmp_path, monkeypatch):
    """D152 step 8, the second decision on the stream: which handed-in reports a run still owes
    a reply to. The two stores must agree over a real filing-and-closure shape, with no event.

    This is the first moved decision that reads rows OTHER routines wrote (step 7's cap reads
    one sender's own filings), so it is the first use that depends on the backfill being
    complete across the fleet rather than within a thread.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    _file(home, "a", "b", 1)                        # R1: a hands work to b
    _file(home, "a", "b", 2)                        # R2: and another
    # b answers R1 and closes it — so only R2 is still owed
    assert file_report(home, routine="b", run_id="b:1", title="done", detail="d", target="a",
                       disposal=Disposal(answers="R1", closes=True)) is not None
    rows = read_reports(reports_path(home))
    assert still_owed_now(home, rows, ["R1", "R2"], routine="b") == ["R2"]
    assert seen == [], f"the stream and the ledger disagreed: {seen}"
    # …and an empty owed list asks nothing of either store
    assert still_owed_now(home, rows, [], routine="b") == []


def test_an_owed_reply_divergence_keeps_the_ledgers_answer_and_files_the_event(tmp_path,
                                                                              monkeypatch):
    """A hold is placed on the LEDGER's answer, never on a stream-only reading: answering from
    a stream missing history would ask a run again for a reply it already sent, which is the
    exact failure R2086 fixed by re-reading the ledger at the moment of asking.
    """
    home = tmp_path / "routines"
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    monkeypatch.setattr("rsched.hooks.reportinv.stream_rows", lambda _h: [])
    rows = [{"id": "R9", "ts": "t", "routine": "a", "run_id": "a:1", "title": "t",
             "detail": "d", "target": "b"}]
    assert still_owed_now(home, rows, ["R9"], routine="b") == ["R9"]
    assert len(seen) == 1 and seen[0]["stream"] == 0 and seen[0]["ledger"] == 1
    assert "still owes a reply" in seen[0]["detail"]


def test_the_finish_hold_predicate_reads_the_stream(tmp_path, monkeypatch):
    """THE pin on step 8's decision actually moving — the counterpart of the filing-path pin
    below. `assist_predicates._unclosed_delivered_report` is what HOLDS a run's finish, and it
    must consult the stream rather than the ledger alone.

    Its answer is deliberately unchanged, so the observable difference is the divergence event:
    against code that only reads the projection, nothing is filed here and this fails.
    """
    from types import SimpleNamespace

    from rsched.engine import assist_predicates

    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    _file(home, "a", "b", 1)
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    monkeypatch.setattr("rsched.hooks.reportinv.stream_rows", lambda _h: [])
    situation = SimpleNamespace(ctx=SimpleNamespace(
        reports_open=["R1"], routine=SimpleNamespace(slug="b"),
        server=SimpleNamespace(routines_home=home)), loop=SimpleNamespace())

    assert assist_predicates._unclosed_delivered_report(situation) is True
    assert len(seen) == 1, "the finish-hold predicate did not consult the hook stream"
    assert seen[0]["routine"] == "b" and seen[0]["ledger"] == 1


def test_the_live_filing_path_reads_the_stream(tmp_path, monkeypatch):
    """THE pin on the decision actually moving: `file_report` — the engine's write path — must
    consult the stream, not just the ledger.

    The cap's ANSWER is deliberately unchanged (the ledger's count is kept while both stores
    are written), so the observable difference is the divergence event. With the decision still
    read from the ledger alone, nothing is filed here; this test fails against that code.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    _file(home, "a", "b", 1)
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    monkeypatch.setattr("rsched.hooks.reportinv.stream_rows", lambda _h: [])
    _file(home, "a", "b", 2)
    assert len(seen) == 1, "the filing path did not consult the hook stream"
    assert seen[0]["ledger"] == 1 and seen[0]["stream"] == 0


def _run_backfill(home: Path) -> dict:
    from rsched.migrate_report_hooks import run_migration

    class Stub:
        routines_home = str(home)

    return run_migration(Stub())


def test_the_backfill_carries_pre_stream_history_in_timestamp_order(tmp_path):
    """The point of the whole step: a thread opened before the stream existed is countable
    from the stream afterwards. The rows merge by ts, because a later row about an id is the
    later fact about it.
    """
    home = tmp_path / "routines"
    control = home / ".control"
    control.mkdir(parents=True)
    # R1's filing, R2's filing to `b`, and R2's DELIVERY — a delivery does not settle a
    # thread, so R2 is a thread opened in July and still open, which is exactly the history a
    # stream that began in October cannot see.
    history = [ROWS[0], ROWS[1], ROWS[4]]
    (control / "reports.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in history), encoding="utf-8")
    (control / "hooks.jsonl").write_text(
        json.dumps({"id": "rem-x", "ts": "2026-10-19T05:11:16+02:00", "source": "reminder",
                    "trigger": {}, "addressee": "", "payload": {}, "delivery": "",
                    "disposition": "live", "origin": {}}) + "\n", encoding="utf-8")
    record = _run_backfill(home)
    assert record["carried"] == 3, record
    stream = [json.loads(line) for line in
              (control / "hooks.jsonl").read_text().splitlines() if line.strip()]
    assert [r["ts"] for r in stream] == sorted(r["ts"] for r in stream), "not in ts order"
    assert [r["id"] for r in stream if r["source"] == "report"] == ["R1", "R2", "R2"]
    # and the carried history is now DECIDABLE: R2's July thread is countable from the stream,
    # which is what the cap needs. Without the backfill the stream would say `none` here and
    # the divergence event would fire on every filing.
    assert open_threads_now(home, read_reports(reports_path(home)),
                            routine="a", target="b") == ["R2"]


def test_the_backfill_is_idempotent_and_records_itself(tmp_path):
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    (home / ".control" / "reports.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in ROWS), encoding="utf-8")
    first = _run_backfill(home)
    assert first["carried"] == len(ROWS)
    assert (home / ".control" / "migrations" / "report-hooks.json").is_file()
    assert _run_backfill(home) == {}, "a second boot must do nothing"
    rows = [line for line in
            (home / ".control" / "hooks.jsonl").read_text().splitlines() if line.strip()]
    assert len(rows) == len(ROWS), "the second run appended duplicates"


def test_the_backfill_skips_rows_already_in_the_stream(tmp_path):
    """Keyed by (id, event) — a report that was filed, delivered and superseded is three facts
    and three hooks, so the id alone cannot be the key.
    """
    home = tmp_path / "routines"
    control = home / ".control"
    control.mkdir(parents=True)
    rows = [ROWS[1], ROWS[4]]                       # R2's filing and R2's delivery
    (control / "reports.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (control / "hooks.jsonl").write_text(
        json.dumps(hook_of_row(ROWS[1]).as_record()) + "\n", encoding="utf-8")
    record = _run_backfill(home)
    assert record["carried"] == 1 and record["already_present"] == 1
    stream = [json.loads(line) for line in
              (control / "hooks.jsonl").read_text().splitlines() if line.strip()]
    assert [(r["id"], r["payload"].get("event", "")) for r in stream] == [
        ("R2", ""), ("R2", "delivered")]


def test_an_unreadable_stream_never_breaks_the_cap(tmp_path, monkeypatch):
    """A decision moved onto the stream must not become a new way for a filing to fail."""
    home = tmp_path / "routines"

    def boom(_home):
        raise OSError("stream unreadable")

    monkeypatch.setattr("rsched.hooks.reportinv.stream_rows", boom)
    rows = [{"id": "R9", "ts": "t", "routine": "a", "run_id": "a:1", "title": "t",
             "detail": "d", "target": "b"}]
    assert open_threads_now(home, rows, routine="a", target="b") == ["R9"]
