"""The `report` action — the ONE channel a run uses for work that is not its own task.

Ungated and in ALWAYS_KINDS, so every routine holds it. What varies is whether the reporting
run can name an owner: UNADDRESSED goes to the triage stream, ADDRESSED (`target`) is also
delivered into that routine's inbox for its NEXT SCHEDULED RUN.

The load-bearing property, asserted directly below: an addressed report NEVER starts a run.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from rsched.engine.actions import ALWAYS_KINDS, KIND_EXAMPLES, KINDS, validate_action
from rsched.engine.inbox import drain_messages
from rsched.engine.interact import handle_report
from rsched.engine.observations import format_observation
from rsched.grants import GATED_KINDS, GrantPolicy
from rsched.readmodels import items
from rsched.reports import next_id, read_reports, reports_path, stamp_delivered


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
