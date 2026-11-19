"""A STANDING condition that is over leaves the blocked-work list (2026-11-19).

The fold windows a week, and a standing condition's watcher files one row a day while it holds
— so once it stopped holding, its row stood on the panel for the rest of the week: an overhead
alarm on a recipe already revised, a due-soon marker already deleted, an obligation already met.
`web/operator_conditions` asked "does it still hold?" of the operator's rows only; it now asks
it of every standing condition, of the reader that decides that condition, and a resolved row
leaves `standing_rows` and the table (it stays in `rows`, marked, for the record).
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from helpers import tmp_server
from rsched.readmodels import memo
from rsched.readmodels.blocked_kinds import STANDING_EVENTS
from rsched.readmodels.health_stream import blocked_fleet
from rsched.readmodels.operator_blocks import RESOLVED, annotate
from rsched.web.operator_conditions import checker


def _write(server, rows):
    control = server.routines_home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "health-events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    memo.reset()


def _row(event, routine, hours=2):
    ts = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    return {"event": event, "routine": routine, "run_id": "", "ts": ts, "detail": "x"}


def _obligation(server, slug, since):
    state = server.routines_home / slug / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "obligations.json").write_text(json.dumps({"updated": since, "open": [
        {"id": "b1", "text": "do it", "quote": "do it", "since": since}]}), encoding="utf-8")


def test_every_standing_condition_says_what_resolved_means():
    """A resolved row carries the sentence for its event — one per standing condition."""
    assert set(RESOLVED) >= STANDING_EVENTS


def test_an_obligation_met_since_resolves_its_row(tmp_path):
    server = tmp_server(tmp_path, create=False)
    old = (datetime.now(UTC) - timedelta(days=20)).date().isoformat()
    _obligation(server, "still-owes", old)
    (server.routines_home / "paid-up" / "state").mkdir(parents=True)
    _write(server, [_row("obligation_overdue", "still-owes"),
                    _row("obligation_overdue", "paid-up")])

    payload = annotate(blocked_fleet(server), still_holds=checker(SimpleNamespace(server=server)))

    by = {r["subject"]: r for r in payload["rows"]}
    assert not by["still-owes"].get("resolved")
    assert by["paid-up"]["resolved"] is True
    assert payload["standing_rows"] == 1


def test_overhead_with_no_reading_under_the_new_recipe_keeps_standing(tmp_path):
    """A revised recipe has no runs yet: unknown, never a guess that it is fixed."""
    server = tmp_server(tmp_path, create=False)
    (server.routines_home / "busy").mkdir(parents=True)
    _write(server, [_row("process_overhead_high", "busy")])
    payload = annotate(blocked_fleet(server), still_holds=checker(SimpleNamespace(server=server)))
    assert not payload["rows"][0].get("resolved")
    assert payload["standing_rows"] == 1
