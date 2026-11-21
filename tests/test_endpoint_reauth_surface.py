"""A dead subscription login reaches the BLOCKED-WORK surface, and leaves it when signed back in
(C5, operator 2026-11-21: "surface needs_reauth on the blocked-work surface")."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from helpers import tmp_server
from rsched.daemon import endpoint_watch
from rsched.endpoints import endpoint_health
from rsched.readmodels import memo
from rsched.readmodels.health_stream import blocked_fleet
from rsched.readmodels.operator_blocks import annotate
from rsched.web.operator_conditions import checker


def test_the_event_is_in_the_writers_vocabulary_and_on_the_blocked_work_surface():
    """Both halves of the registration (C5): written into the stream's vocabulary, and READ by
    the blocked-work fold as a standing condition only the operator clears — a sign-in is a
    consent page in his browser."""
    from rsched import health_vocab
    from rsched.readmodels import blocked_kinds, operator_blocks
    from rsched.readmodels.health_stream import BLOCKED_EVENTS

    assert endpoint_watch.EVENT == "endpoint_needs_reauth"
    assert endpoint_watch.EVENT in health_vocab.EVENTS
    assert endpoint_watch.EVENT in BLOCKED_EVENTS
    assert blocked_kinds.kind(endpoint_watch.EVENT) == blocked_kinds.STANDING
    assert operator_blocks.is_operator_block(endpoint_watch.EVENT)


def _filed(server) -> None:
    control = server.routines_home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    (control / "health-events.jsonl").write_text(json.dumps(
        {"ts": ts, "event": endpoint_watch.EVENT, "routine": "", "run_id": "",
         "detail": "otari/codex: all 1 proxy account(s) of this login are unavailable",
         "login": "otari/codex"}) + "\n", encoding="utf-8")
    memo.reset()


def _row(server) -> dict:
    payload = annotate(blocked_fleet(server), still_holds=checker(SimpleNamespace(server=server)))
    [row] = [r for r in payload["rows"] if r["event"] == endpoint_watch.EVENT]
    return row | {"operator_total": payload["operator_total"]}


def test_a_dead_login_is_an_instance_row_waiting_on_the_operator(tmp_path):
    server = tmp_server(tmp_path, create=False)
    server.routines_home.mkdir(parents=True)
    endpoint_health.remember(server.routines_home, "otari/codex", state="needs_reauth",
                             detail="dead", probe=endpoint_watch.PROBE)
    _filed(server)

    row = _row(server)
    assert row["subject"] == "" and row["kind"] == "standing"
    assert row["operator"] is True and "sign it back in" in row["waiting_on_you"]
    assert not row.get("resolved") and row["operator_total"] == 1


def test_a_login_signed_back_in_resolves_the_row(tmp_path):
    server = tmp_server(tmp_path, create=False)
    server.routines_home.mkdir(parents=True)
    endpoint_health.remember(server.routines_home, "otari/codex", state="ok",
                             detail="1 of 1", probe=endpoint_watch.PROBE)
    _filed(server)

    row = _row(server)
    assert row["resolved"] is True and row["operator"] is False
    assert row["operator_total"] == 0


def test_no_record_at_all_cannot_be_told_so_the_row_keeps_counting(tmp_path):
    """None is "cannot be told": only a KNOWN False retires a row (operator_conditions)."""
    server = tmp_server(tmp_path, create=False)
    server.routines_home.mkdir(parents=True)
    _filed(server)

    assert endpoint_health.any_needs_reauth(server.routines_home) is None
    assert not _row(server).get("resolved")
