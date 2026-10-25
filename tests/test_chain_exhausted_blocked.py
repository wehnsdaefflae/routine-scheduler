"""F716. A whole-model-chain failure is work that was due and did not happen.

`model_chain_exhausted` fires when EVERY member of a role's fallback chain failed hard or was
cooling (`engine/degrade.py`), so the turn could not be taken at all. Until now it reached no
blocked-work surface: the only signal was one routine's failed run page, and when the cause is
the host's shared proxy the failure is never one routine's. On 2026-10-19 five
`model_chain_exhausted` plus three `run_failed` events were all claude-proxy HTTP 503
`auth_unavailable`, killing legs of `scheduler-builder` (twice) and `bahnbonus-seat-position` —
eight events in one day, and nothing a person clicks said the chain was down.

That is the same shape as every other member of `BLOCKED_EVENTS`: a run page can show a run that
failed, but it cannot show that the FLEET's models stopped serving, and `readmodels/health_stream`
is the only in-product reader of the stream — an event left off this fold is read by the nightly
ssh recipe or by nobody.

These tests also pin the two prose counts that had ALREADY drifted when this was written
(`docs/architecture.md` said "the eight events" and `docs/run-analytics.md` "Ten health events"
while the set held 17), because a written-out count is a claim about a `len()` and nothing
checked this one — the same defect class `tests/test_doc_counts.py` exists for.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from helpers import tmp_server
from rsched import health_events as health_events_mod
from rsched.readmodels import memo
from rsched.readmodels.health_stream import BLOCKED_EVENTS, blocked_fleet

ROOT = Path(__file__).resolve().parents[1]


def _ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _write(server, rows: list[dict]) -> None:
    control = server.routines_home / ".control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "health-events.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    memo.reset()


def _exhausted(routine: str, run_id: str, hours: float, model: str = "claude-opus-5") -> dict:
    return {"event": "model_chain_exhausted", "routine": routine, "run_id": run_id,
            "ts": _ago(hours), "model": model, "last_model": "gpt-6-astra",
            "cooldown_s": 900,
            "detail": f"every model in {model}'s fallback chain failed or is cooling; "
                      f"last was gpt-6-astra: 503 auth_unavailable"}


def test_an_exhausted_chain_reaches_the_blocked_fold(tmp_path):
    """The defect itself: the event exists, and `/api/health/blocked` could not see it."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [_exhausted("scheduler-builder", "scheduler-builder:20261019-030000", 2)])
    out = blocked_fleet(server)
    assert out["total"] == 1
    row = out["rows"][0]
    assert row["event"] == "model_chain_exhausted"
    assert row["subject"] == "scheduler-builder"
    assert "503 auth_unavailable" in row["detail"]
    assert row["means"].strip(), "a label a reader can act on"


def test_the_2026_10_19_outage_folds_to_one_row_per_routine(tmp_path):
    """The live specimen. Three routines, five events: a reader must see that the chain is the
    subject, not that three unrelated routines each had a bad day."""
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        _exhausted("scheduler-builder", "scheduler-builder:20261019-030000", 9),
        _exhausted("scheduler-builder", "scheduler-builder:20261019-150000", 4),
        _exhausted("bahnbonus-seat-position", "bahnbonus-seat-position:20261019-060000", 7),
        _exhausted("self-audit", "self-audit:20261019-003000", 10),
        _exhausted("self-audit", "self-audit:20261019-003000", 6),
    ])
    out = blocked_fleet(server)
    rows = [r for r in out["rows"] if r["event"] == "model_chain_exhausted"]
    assert len(rows) == 3, "one row per (event, subject)"
    assert {r["subject"] for r in rows} == {"scheduler-builder", "bahnbonus-seat-position",
                                            "self-audit"}
    assert sum(r["count"] for r in rows) == 5
    assert out["total"] == 5


def test_a_resumed_run_does_not_erase_a_chain_outage(tmp_path):
    """A correction excuses an ENDING the run later disproved by finishing ok. A chain that was
    unusable is not that: the turn it blocked was still paid for and the provider was still
    down, so the row stands (`model_chain_exhausted` is deliberately not in
    `runcorrection.CORRECTED_ENDINGS`).
    """
    server = tmp_server(tmp_path, create=False)
    _write(server, [
        _exhausted("scheduler-builder", "scheduler-builder:20261019-030000", 5),
        {"event": "run_corrected", "routine": "scheduler-builder",
         "run_id": "scheduler-builder:20261019-030000", "ts": _ago(1),
         "detail": "resumed and finished ok"},
    ])
    out = blocked_fleet(server)
    assert [r["event"] for r in out["rows"]] == ["model_chain_exhausted"]
    assert out["rows"][0]["count"] == 1


def test_the_event_is_in_the_writers_vocabulary():
    """The set is a subset of `health_events.py`'s enum — the header docstring is what every
    other reader of the stream is compiled against."""
    assert '"model_chain_exhausted"' in (health_events_mod.__doc__ or "")


def test_it_is_not_repeat_suppressed(tmp_path):
    """Nothing on this fold may be in `REPEAT_COOLDOWN_HOURS`: the fold COUNTS rows, so a
    suppressed one makes the fleet look less stuck than it is. Eight events in a day is the
    signal — one is a hiccup."""
    for event in BLOCKED_EVENTS:
        assert event not in health_events_mod.REPEAT_COOLDOWN_HOURS, event


def test_the_prose_counts_follow_the_set():
    """A written-out count is a claim about `len(BLOCKED_EVENTS)` and both documents had
    drifted (8 and 10, against a set of 17) before anything checked them. The names matter more
    than the number: a reader who counts more names than the figure cannot tell which half is
    stale.
    """
    n = len(BLOCKED_EVENTS)
    arch = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    analytics = (ROOT / "docs" / "run-analytics.md").read_text(encoding="utf-8")
    # Both documents WRAP the sentence, so the count and the words it qualifies are on
    # different lines: the pattern has to span whitespace, not a single line.
    counted = re.compile(r"(\d+)\s+(?:events that mean WORK THAT WAS DUE"
                         r"|health events record the opposite)", re.DOTALL)
    for doc, text in (("architecture.md", arch), ("run-analytics.md", analytics)):
        claims = [int(m) for m in counted.findall(text)]
        assert claims, f"{doc} no longer states a count for the blocked-work set"
        for claimed in claims:
            assert claimed == n, (
                f"{doc} claims {claimed} blocked-work events, BLOCKED_EVENTS holds {n} — "
                f"a count in contract prose is a claim about a len()")
        assert "`model_chain_exhausted`" in text, (
            f"{doc} lists the blocked-work events and omits model_chain_exhausted")
