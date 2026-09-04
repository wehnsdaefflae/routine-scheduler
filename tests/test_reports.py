"""The `report` action — the ONE channel a run uses for work that is not its own task.

Ungated and in ALWAYS_KINDS, so every routine holds it. What varies is whether the reporting
run can name an owner: UNADDRESSED goes to the triage stream, ADDRESSED (`target`) is also
delivered into that routine's inbox for its NEXT SCHEDULED RUN.

The load-bearing property, asserted directly below: an addressed report NEVER starts a run.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from rsched.engine.actions import ALWAYS_KINDS, KIND_EXAMPLES, validate_action
from rsched.engine.actionschema import KINDS
from rsched.engine.inbox import drain_messages
from rsched.engine.interact import handle_report
from rsched.engine.observations import format_observation
from rsched.grantpolicy import GrantPolicy
from rsched.grants import GATED_KINDS
from rsched.readmodels import items
from rsched.reports import (
    next_id,
    read_reports,
    reports_path,
    retract_report,
    stamp_delivered,
)


def _routine(home: Path, slug: str) -> Path:
    d = home / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(f"slug: {slug}\n", encoding="utf-8")
    return d


def _loop(tmp_path, *, slug="some-routine", run_id="some-routine:20260726-020000"):
    home = tmp_path / "routines"
    home.mkdir(parents=True, exist_ok=True)
    _routine(home, slug)
    ctx = SimpleNamespace(server=SimpleNamespace(routines_home=home),
                          routine=SimpleNamespace(slug=slug), run_id=run_id)
    return SimpleNamespace(ctx=ctx), home


def _rows(home):
    return read_reports(reports_path(home))


# -- schema gate ---------------------------------------------------------------------------


def test_report_is_an_always_kind_with_an_example():
    assert "report" in KINDS
    assert "report" in ALWAYS_KINDS
    assert "report" not in GATED_KINDS          # ungated: routing needs no capability
    assert KIND_EXAMPLES["report"]["kind"] == "report"
    # the two channels it replaced are gone — one action, one id namespace, one stream
    assert "report_bug" not in KINDS
    assert "hand_off" not in KINDS


def test_validate_action_report():
    assert validate_action({"say": "s", "kind": "report", "title": "a defect",
                            "detail": "what I did, what happened"}) == []
    assert validate_action({"say": "s", "kind": "report", "title": "t"}) == []   # detail optional
    assert validate_action({"say": "s", "kind": "report", "title": "t",
                            "target": "config-optimizer", "answers": "R3"}) == []
    # title is required
    assert validate_action({"say": "s", "kind": "report", "detail": "no title"})
    assert validate_action({"say": "s", "kind": "report", "title": "   "})


def test_validate_action_closes_needs_answers():
    """`closes` marks an ANSWER as terminal — without `answers` there is no exchange to
    complete, so a bare closes is rejected rather than silently ignored."""
    assert validate_action({"say": "s", "kind": "report", "title": "t",
                            "answers": "R3", "closes": True}) == []
    problems = validate_action({"say": "s", "kind": "report", "title": "t", "closes": True})
    assert any("closes" in p and "answers" in p for p in problems)


def test_report_bypasses_allowlist_and_capability_gate():
    obj = {"say": "s", "kind": "report", "title": "a defect"}
    # a restrictive workflow tools: allowlist that omits report still permits it
    assert validate_action(obj, allowed_kinds={"read_file"}) == []
    # a routine with NO gated capabilities still permits it, addressed or not
    assert validate_action(obj, grants=GrantPolicy(actions=frozenset())) == []
    assert validate_action({**obj, "target": "self-audit"},
                           grants=GrantPolicy(actions=frozenset())) == []


# -- unaddressed: the triage stream ---------------------------------------------------------


def test_unaddressed_report_lands_in_the_stream_and_delivers_nothing(tmp_path):
    loop, home = _loop(tmp_path, slug="bahnbonus-seat-position",
                       run_id="bahnbonus-seat-position:20260726-134640")
    other = _routine(home, "self-audit")
    obs = handle_report(loop, {"title": "write_util selftest fails silently",
                               "detail": "exit 2, no message, under the sandbox"})
    assert obs == {"kind": "report", "title": "write_util selftest fails silently",
                   "filed": True, "id": "R1"}
    rows = _rows(home)
    assert len(rows) == 1
    assert rows[0]["routine"] == "bahnbonus-seat-position"
    assert "target" not in rows[0]           # triage: nobody was addressed
    assert not (other / "inbox").exists()    # and nothing was delivered anywhere


def test_ids_are_monotonic_across_reporters(tmp_path):
    loop, home = _loop(tmp_path)
    assert next_id(reports_path(home)) == "R1"
    handle_report(loop, {"title": "one"})
    handle_report(loop, {"title": "two"})
    assert [r["id"] for r in _rows(home)] == ["R1", "R2"]
    assert next_id(reports_path(home)) == "R3"


# -- addressed: delivery to the owner --------------------------------------------------------


def test_addressed_report_is_delivered_to_the_target_inbox(tmp_path):
    loop, home = _loop(tmp_path, slug="self-audit")
    _routine(home, "routine-improver")
    obs = handle_report(loop, {"target": "routine-improver",
                               "title": "newsletter-digest stages contradict main.md",
                               "detail": "stages/finalize.md:12 vs main.md:8"})
    assert obs["filed"] is True and obs["id"] == "R1" and obs["target"] == "routine-improver"

    assert _rows(home)[0]["target"] == "routine-improver"
    delivered = list((home / "routine-improver" / "inbox").glob("msg-rep-*.json"))
    assert len(delivered) == 1
    msg = json.loads(delivered[0].read_text())
    assert msg["via"] == "report" and msg["report"] == "R1"
    assert "REPORT R1 from routine `self-audit`" in msg["text"]


def test_an_addressed_report_never_starts_a_run(tmp_path):
    """The invariant. Delivery writes ONE file into the target's inbox and touches nothing
    else — no one-shot spool, no state, no status. The target's next scheduled run drains it.
    """
    loop, home = _loop(tmp_path, slug="self-audit")
    target = _routine(home, "global-utils-review")
    handle_report(loop, {"target": "global-utils-review", "title": "t", "detail": "d"})

    written = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    assert written == ["inbox/msg-rep-R1.json", "routine.yaml"]
    assert not (home / ".control" / "schedule-once").exists()
    assert not (target / "runs").exists()
    assert not (target / "status.json").exists()


def test_report_refuses_self_target_and_unknown_target(tmp_path):
    loop, home = _loop(tmp_path, slug="self-audit")
    _routine(home, "config-optimizer")
    assert handle_report(loop, {"target": "self-audit", "title": "t"})["self_target"] is True

    obs = handle_report(loop, {"target": "config-optimiser", "title": "t"})
    assert obs["unknown_target"] is True
    assert "config-optimizer" in obs["suggestions"]      # a near-miss slug is offered back
    assert "config-optimizer" in obs["valid_targets"]
    assert not (home / "config-optimizer" / "inbox").exists()   # a miss delivers nothing
    assert _rows(home) == []                                    # and files nothing


# -- delivery stamp: the target's own drain reports back --------------------------------------


def test_the_targets_drain_stamps_delivery(tmp_path):
    loop, home = _loop(tmp_path, slug="self-audit")
    target = _routine(home, "routine-improver")
    handle_report(loop, {"target": "routine-improver", "title": "t", "detail": "d"})
    assert _rows(home)[0].get("delivered") is None

    msgs = drain_messages(target, tmp_path / "consumed")
    assert len(msgs) == 1
    assert msgs[0]["report"] == "R1"
    assert msgs[0]["from"] == "self-audit"       # keeps it out of the user-message channel

    stamp_delivered(home, msgs, run_id="routine-improver:20260726-010000")
    assert _rows(home)[0]["delivered"]["run_id"] == "routine-improver:20260726-010000"


def test_a_plain_user_message_carries_no_report_keys(tmp_path):
    _, home = _loop(tmp_path)
    target = _routine(home, "routine-improver")
    (target / "inbox").mkdir(exist_ok=True)
    (target / "inbox" / "msg-1.json").write_text(json.dumps({"text": "hi"}), encoding="utf-8")
    msgs = drain_messages(target, tmp_path / "consumed")
    assert msgs == [{"text": "hi", "attachments": []}]
    stamp_delivered(home, msgs, run_id="r:1")    # a no-op that must not create the ledger
    assert not reports_path(home).exists()


# -- retraction: the outbox's one write (D74) ---------------------------------------------------


def test_retract_report_withdraws_the_pending_delivery(tmp_path):
    """Retracting an undelivered addressed report unlinks the delivery file (the recipient
    never sees it), appends a `retracted` event the fold carries, and the item reads
    `dropped`. The row itself is never rewritten — the ledger stays append-only."""
    loop, home = _loop(tmp_path, slug="self-audit")
    target = _routine(home, "routine-improver")
    handle_report(loop, {"target": "routine-improver", "title": "t", "detail": "d"})
    assert (target / "inbox" / "msg-rep-R1.json").exists()

    row = retract_report(home, "R1")
    assert row["title"] == "t"
    assert not (target / "inbox" / "msg-rep-R1.json").exists()
    assert _rows(home)[0]["retracted"]["ts"]
    assert drain_messages(target, tmp_path / "consumed") == []   # nothing ever arrives
    assert _items(home, home / "self-audit")["R1"]["status"] == "dropped"
    with pytest.raises(ValueError, match="already retracted"):
        retract_report(home, "R1")


def test_retract_refusals(tmp_path):
    """No phantom retractions: unknown id, unaddressed row, an already-consumed delivery
    (stamped or not) all refuse — only a target whose inbox is GONE (it can never consume)
    lets the retraction stand on a missing file."""
    loop, home = _loop(tmp_path, slug="self-audit")
    target = _routine(home, "routine-improver")
    with pytest.raises(LookupError):
        retract_report(home, "R99")
    handle_report(loop, {"title": "unaddressed — triage"})
    with pytest.raises(ValueError, match="unaddressed"):
        retract_report(home, "R1")
    handle_report(loop, {"target": "routine-improver", "title": "landed"})
    stamp_delivered(home, drain_messages(target, tmp_path / "consumed"),
                    run_id="routine-improver:20260726-010000")
    with pytest.raises(ValueError, match="picked up"):
        retract_report(home, "R2")
    # consumed but not yet stamped (the drain's instant between rename and append)
    handle_report(loop, {"target": "routine-improver", "title": "in flight"})
    (target / "inbox" / "msg-rep-R3.json").unlink()
    with pytest.raises(ValueError, match="picked up"):
        retract_report(home, "R3")
    # a target whose inbox no longer exists can never consume — the retraction stands
    handle_report(loop, {"target": "routine-improver", "title": "orphaned"})
    shutil.rmtree(target)
    retract_report(home, "R4")
    assert {r["id"]: bool(r.get("retracted")) for r in _rows(home)} == {
        "R1": False, "R2": False, "R3": False, "R4": True}


def test_a_retracted_answer_settles_nothing(tmp_path):
    """A reply that was retracted before the asker consumed it must not settle its target —
    the answer never arrived, so the exchange is still open."""
    loop, home = _loop(tmp_path, slug="self-audit")
    target = _routine(home, "routine-improver")
    handle_report(loop, {"target": "routine-improver", "title": "fix the pattern"})
    stamp_delivered(home, drain_messages(target, tmp_path / "consumed"),
                    run_id="routine-improver:20260726-010000")
    back = SimpleNamespace(ctx=SimpleNamespace(
        server=SimpleNamespace(routines_home=home),
        routine=SimpleNamespace(slug="routine-improver"),
        run_id="routine-improver:20260726-010000"))
    handle_report(back, {"target": "self-audit", "title": "fixed it", "answers": "R1"})
    assert _items(home, home / "self-audit")["R1"]["status"] == "settled"

    retract_report(home, "R2")
    after = _items(home, home / "self-audit")
    assert after["R1"]["status"] == "in_progress"    # delivered, but the answer never came
    assert after["R2"]["status"] == "dropped"


# -- the Items read model ----------------------------------------------------------------------


def _items(home: Path, audit_dir: Path) -> dict:
    return {i["id"]: i for i in items._build(*items.source_paths(audit_dir, home))["items"]}


def test_items_shows_a_report_and_its_lifecycle(tmp_path):
    loop, home = _loop(tmp_path, slug="self-audit")
    audit = home / "self-audit"
    target = _routine(home, "routine-improver")
    handle_report(loop, {"target": "routine-improver", "title": "fix the pattern",
                         "detail": "see the stage module"})

    item = _items(home, audit)["R1"]
    assert item["type"] == "report"
    assert item["status"] == "open"           # filed; the target has not run yet
    assert item["origin"]["routine"] == "self-audit"
    assert item["to"] == "routine-improver"

    stamp_delivered(home, drain_messages(target, tmp_path / "consumed"),
                    run_id="routine-improver:20260726-010000")
    assert _items(home, audit)["R1"]["status"] == "in_progress"   # it is in their prompt

    back = SimpleNamespace(ctx=SimpleNamespace(
        server=SimpleNamespace(routines_home=home),
        routine=SimpleNamespace(slug="routine-improver"),
        run_id="routine-improver:20260726-010000"))
    handle_report(back, {"target": "self-audit", "title": "fixed it", "answers": "R1"})

    closed = _items(home, audit)
    assert closed["R1"]["status"] == "settled"
    assert closed["R1"]["answered_by"] == "R2"
    assert closed["R2"]["answers"] == "R1"
    # WITHOUT closes, the answer itself is a new open report — the ratchet the terminal
    # acknowledgment exists to end
    assert closed["R2"]["status"] == "open"


def test_a_closure_is_born_settled_and_ends_the_exchange(tmp_path):
    """`closes: true` beside `answers`: the reply settles its target AND itself — the thread
    has a terminal state instead of every answer needing one more answer."""
    loop, home = _loop(tmp_path, slug="self-audit")
    audit = home / "self-audit"
    _routine(home, "routine-improver")
    handle_report(loop, {"target": "routine-improver", "title": "fix the pattern"})

    back = SimpleNamespace(ctx=SimpleNamespace(
        server=SimpleNamespace(routines_home=home),
        routine=SimpleNamespace(slug="routine-improver"),
        run_id="routine-improver:20260726-010000"))
    handle_report(back, {"target": "self-audit", "title": "fixed it — nothing more needed",
                         "answers": "R1", "closes": True})

    by_id = _items(home, audit)
    assert by_id["R1"]["status"] == "settled"
    assert by_id["R2"]["status"] == "settled"        # born settled: no open tail
    assert by_id["R2"]["closes"] is True
    # the closure is still DELIVERED (the filer learns the outcome), marked terminal
    msg = json.loads((home / "self-audit" / "inbox" / "msg-rep-R2.json").read_text())
    assert "closes the exchange, no reply needed" in msg["text"]

    # answering a closure anyway still works and changes nothing — it is already settled
    handle_report(loop, {"target": "routine-improver", "title": "ack", "answers": "R2"})
    assert _items(home, audit)["R2"]["status"] == "settled"


def test_file_report_refuses_closes_without_answers(tmp_path):
    """Out-of-band callers (batch scripts) get the same pairing rule the action layer
    enforces: a bare closes is dropped, never recorded."""
    from rsched.reports import file_report
    _, home = _loop(tmp_path)
    file_report(home, routine="x", run_id="x:1", title="t", closes=True)
    assert "closes" not in _rows(home)[0]


def test_an_unaddressed_report_has_no_routing(tmp_path):
    loop, home = _loop(tmp_path, slug="self-audit")
    handle_report(loop, {"title": "something is wrong and I can't say whose"})
    item = _items(home, home / "self-audit")["R1"]
    assert item["to"] == "" and item["delivered"] == {}
    assert item["status"] == "open"


def test_report_ids_stay_out_of_historical_changelog_prose():
    """`R` is its own namespace beside `F`/`D`, and the historical changelog prose scan must
    not match it — report ids postdate every archived row.
    """
    assert items.TYPE_BY_PREFIX["R"] == "report"
    assert items.ID_RE.findall("R7 and F3") == ["F3"]
    assert set(items.REF_RE.findall("R7 and F3")) == {"R7", "F3"}


def test_observation_renders_the_filed_id(tmp_path):
    loop, home = _loop(tmp_path)
    obs = handle_report(loop, {"title": "a defect", "detail": "d"})
    assert "R1" in format_observation(obs)
    assert home.is_dir()


def test_a_targeted_report_always_writes_its_inbox_delivery(tmp_path):
    """The invariant readmodels/orphans.find_undelivered depends on: the ONLY code producer of a
    report row (file_report, via the report action) writes the target's inbox `msg-rep-<id>.json`
    in the SAME call whenever the target resolves — so an addressed report can never be an orphan
    at the source — and an UNKNOWN target files NOTHING at all (no ledger row, never a half-written
    targeted row with no message). The 2026-08-29 / 2026-09-04 orphans came from an operator batch
    appended straight to the stream, never from this path."""
    loop, home = _loop(tmp_path, slug="self-audit")
    target = _routine(home, "routine-improver")

    handle_report(loop, {"target": "routine-improver", "title": "landed", "detail": "d"})
    assert (target / "inbox" / "msg-rep-R1.json").is_file()      # delivered in the same call
    # a closure is delivered too (it just does not buy the target a run)
    handle_report(loop, {"target": "routine-improver", "title": "closed",
                         "answers": "R1", "closes": True})
    assert (target / "inbox" / "msg-rep-R2.json").is_file()

    # an UNKNOWN target files NOTHING — no ledger row that could orphan
    obs = handle_report(loop, {"target": "no-such-routine", "title": "misrouted"})
    assert obs.get("unknown_target") is True
    assert [r["id"] for r in _rows(home)] == ["R1", "R2"]        # no third row written


def test_discard_undelivered_report_clears_an_orphan(tmp_path):
    """discard_undelivered_report (F435): clear an 'addressed, never delivered' orphan — a targeted
    row with no inbox message (batch-appended, so no run can ever drain it). It appends a retracted
    event, so the row reads `dropped` and leaves the banner + backlog; it REFUSES a report whose
    delivery is still waiting (retract's job) and an unknown id."""
    from rsched.readmodels.orphans import find_undelivered
    from rsched.reports import discard_undelivered_report

    loop, home = _loop(tmp_path, slug="self-audit")
    _routine(home, "routine-improver")

    # an orphan: a targeted row with NO inbox delivery, exactly as an operator batch leaves it
    path = reports_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "R1", "ts": "2026-09-04T10:00:00+02:00", "routine": "operator",
                             "target": "routine-improver", "title": "batch-appended orphan"}) + "\n")
    assert [o["id"] for o in find_undelivered(_rows(home), home)] == ["R1"]

    row = discard_undelivered_report(home, "R1")
    assert row["id"] == "R1"
    assert _items(home, home / "self-audit")["R1"]["status"] == "dropped"
    assert find_undelivered(_rows(home), home) == []            # gone from the banner

    # refuses a normally-delivered report whose message still waits — that is retract's job
    handle_report(loop, {"target": "routine-improver", "title": "waiting normally"})
    with pytest.raises(ValueError, match="still waiting"):
        discard_undelivered_report(home, "R2")
    with pytest.raises(LookupError):
        discard_undelivered_report(home, "R404")
