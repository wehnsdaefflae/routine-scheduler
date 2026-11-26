"""A misplaced `closes` is ACCEPTED and CORRECTED — never refused, never silently dropped: the
report is filed, `closes` is dropped, and the run is TOLD so in the observation (what D196
rejected was a SILENT drop, which left the sender believing a thread was closed). Three
properties travel together here because dropping any one of them re-creates a defect the
measured events named — the notice is DELIVERED and stays visible to the run gate's inbox
predicate (a closure is kept OUT of that predicate, so marking one would hide it from its
recipient), it owes NO reply back (what all eight measured refusal events were reaching for),
and it settles NOTHING, with no closure inferred on its behalf. A closure with a real disposal
beside it is untouched: it still closes, and is still kept out of the gate's predicate.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import bare_routine
from rsched.daemon.gate_builtin import pending_inbox
from rsched.engine.actions import validate_action
from rsched.engine.admin_handlers import handle_report
from rsched.engine.inbox import drain_messages
from rsched.engine.observations import format_observation
from rsched.report_detail import message_text
from rsched.reports import Disposal, file_report, read_reports, reports_path, stamp_delivered

#: The sentence the correction MUST put in front of the run. Asserted whole: the explicitness
#: is the behaviour here, so a wording that stops saying what was dropped is a regression.
CORRECTION = ("Your `closes` was DROPPED — it completes a disposal, and this report names "
              "none: the notice is filed as owing NO reply back, and it settled no row. Name "
              "the row in `answers` or `settles` if you meant to dispose of one.")


def _loop(home: Path, slug: str, *, owed: list[str] | None = None):
    bare_routine(home, slug)
    return SimpleNamespace(ctx=SimpleNamespace(
        server=SimpleNamespace(routines_home=home), routine=SimpleNamespace(slug=slug),
        run_id=f"{slug}:20260915-120000", reports_open=list(owed or [])))


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "routines"
    (h / ".control").mkdir(parents=True)
    return h


# -- the value object: both entrances agree, because the correction is IN the value ----------


def test_a_bare_closure_is_accepted_and_says_it_was_corrected():
    disposal = Disposal(closes=True)
    assert disposal.closes is False          # no inferred settlement
    assert disposal.closes_dropped is True   # and not silently: the drop is recorded


@pytest.mark.parametrize("disposal", [{"answers": "R1"}, {"settles": ("R1",)}])
def test_a_closure_naming_either_disposal_still_stands(disposal):
    """Both real faces keep working — `answers` for the one exchange a reply belongs to,
    `settles` for the many-rows claim — and neither is marked as corrected.
    """
    built = Disposal(closes=True, **disposal)
    assert built.closes is True
    assert built.closes_dropped is False


def test_the_action_grammar_accepts_the_shape_it_used_to_refuse():
    """The shape the measured refusal events actually sent: `closes` with no disposal, with a
    target and without one. Neither costs the run its turn any more.
    """
    for action in (
        {"say": "s", "kind": "report", "title": "fixed, no reply needed",
         "target": "scheduler-builder", "closes": True},
        {"say": "s", "kind": "report", "title": "fyi", "closes": True},
    ):
        assert validate_action(action) == []


def test_a_fold_carrying_a_closure_is_corrected_where_it_is_built(home):
    """The exact shape D196 was raised about: `closes` beside `supersedes` alone. `supersedes`
    is a `file_report` argument rather than a `Disposal` field, so the correction has to bite
    when the disposal is CONSTRUCTED — which is what makes it reachable for every out-of-band
    caller, whatever else it passes.
    """
    bare_routine(home, "self-audit")
    filed = file_report(home, routine="x", run_id="x:1", title="folding three rows",
                        target="self-audit", target_dir=home / "self-audit",
                        supersedes=("R1", "R2", "R3"), disposal=Disposal(closes=True))

    assert filed is not None                      # accepted, not refused
    row = read_reports(reports_path(home))[-1]
    assert "closes" not in row                    # and nothing claims a closure
    assert "settles" not in row


# -- end to end: the ledger row, the delivery, the gate, the owed set, the observation -------


def test_the_notice_is_delivered_owing_nothing_and_settling_nothing(home):
    sender, recipient = _loop(home, "sender"), home / "recipient"
    bare_routine(home, "recipient")

    out = handle_report(sender, {"target": "recipient", "title": "fixed, no reply needed",
                                 "detail": "the util is patched", "closes": True})
    assert out["filed"] is True
    assert out["closes_dropped"] is True

    row = read_reports(reports_path(home))[-1]
    assert "closes" not in row and "settles" not in row and "answers" not in row

    delivered = drain_messages(recipient, home / "consumed")
    assert len(delivered) == 1, delivered
    assert delivered[0]["no_reply"] is True       # owes nothing back
    assert "closes" not in delivered[0]           # and claims no closure
    assert stamp_delivered(home, delivered, run_id="recipient:20260921-010000") == []


def test_the_run_gate_still_sees_the_corrected_notice_as_freight(home):
    """The correction must not HIDE the notice: a closure is kept out of the gate's inbox
    predicate on purpose, so a corrected notice marked as one would have been the quiet way to
    be heard — the opposite of delivering it.
    """
    sender = _loop(home, "sender")
    bare_routine(home, "recipient")
    handle_report(sender, {"target": "recipient", "title": "fyi", "closes": True})
    assert pending_inbox(home / "recipient") is True

    # and a REAL closure is still not freight, which is the behaviour this change preserves
    bare_routine(home, "other")
    handle_report(sender, {"target": "other", "title": "ordinary work"})
    stamp_delivered(home, drain_messages(home / "other", home / "c1"), run_id="other:1")
    back = _loop(home, "other", owed=["R2"])
    handle_report(back, {"target": "sender", "title": "done", "answers": "R2", "closes": True})
    assert pending_inbox(home / "sender") is False


def test_the_observation_tells_the_run_exactly_what_was_dropped(home):
    sender = _loop(home, "sender")
    bare_routine(home, "recipient")
    rendered = format_observation(handle_report(
        sender, {"target": "recipient", "title": "fyi", "closes": True}))
    assert CORRECTION in rendered, rendered
    # the filing itself is still reported, so the run knows the notice went out
    assert "report filed as R1" in rendered

    # an ordinary report says nothing about `closes`
    plain = format_observation(handle_report(
        sender, {"target": "recipient", "title": "ordinary work"}))
    assert "DROPPED" not in plain, plain


def test_the_delivered_head_says_it_owes_no_reply_and_settled_no_row():
    head = message_text("R7", "sender", "fyi", "", Disposal(closes=True)).splitlines()[0]
    assert head == "REPORT R7 from routine `sender` (no reply needed — settles no row)"


def test_the_report_bullet_describes_the_drop_instead_of_a_refusal():
    """The always-on bullet is the first line of defence and must match the grammar: prose
    still promising a refusal would teach runs a rule that no longer exists.
    """
    from rsched.engine.kindprose import kind_bullets

    bullet = kind_bullets({"report"}, util_confirm="", ask_timeout_min=480)
    assert "alone it is DROPPED" in bullet, bullet
    assert "refused" not in bullet.split("Past a few open threads")[0], bullet
