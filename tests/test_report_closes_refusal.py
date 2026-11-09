"""A closure with nothing to close is REFUSED, at both entrances (D196).

The operator, 2026-10-18, verbatim: *"Refuse it loudly — a report carrying closes with
supersedes but no answers/settles is rejected with a message naming what closes needs, which
changes behaviour for existing callers."* He accepted that break explicitly, so a caller that
starts failing here is intended and not a regression to soften.

What it used to do: `closes` was NORMALIZED to False. The filing was accepted, nothing recorded
the closure, and the sender believed a thread was closed that was not — the failure mode that
leaves a thread open while its sender has moved on. The action layer already refused the shape
for a RUN (tests/test_reports.py::test_validate_action_closes_needs_answers); this is the other
entrance, the out-of-band caller that builds its own `Disposal`.

`supersedes` is deliberately NOT one of the faces a closure can have: folding rows makes this
report their thread, which is the opposite of finishing it.
"""

from __future__ import annotations

import pytest

from rsched.engine.actions import validate_action
from rsched.reports import Disposal, file_report, read_reports, reports_path

# -- the ACTION entrance, where the target decides what the refusal can ask for --------------


def test_a_runtree_message_is_not_refused_at_all():
    """SUPERSEDED BY F859 (0.522.0) — this case used to assert a target-aware REFUSAL.

    The refusal was accurate and unactionable, and the clearer wording 0.511.0 shipped did not
    stop it being paid for: 19 of these refusals across 11 routines by 2026-11-06, then THREE
    MORE in a single `scheduler-builder` run on 2026-11-08 (turns 67, 223, 110 — all three of
    the event's all-time fires). `closes` on a run-tree target is INERT: the hook has no ledger
    row, `runtree.send` never reads the field, so refusing the action changed nothing except
    costing the turn.

    So the rule moved rather than being softened: the disposal grammar governs ledger rows, and
    `reportfields.field_problems` returns early for a run-tree target. What a sender is told
    instead lives in `tests/test_report_runtree_disposal.py` — the hook is delivered, and the
    observation NAMES any `answers`/`settles`/`supersedes` it ignored, which is the half this
    refusal never covered (those three validated and were then dropped unread).
    """
    for target in ("parent", "child:ragapi-standup"):
        assert validate_action({"say": "s", "kind": "report", "title": "answering the "
                                "hand-back", "target": target, "closes": True}) == []


def test_a_routine_target_still_gets_the_original_sentence():
    """The grammar is unchanged — only the explanation is target-aware. A slug target CAN carry
    an id, so it is still told what `closes` needs."""
    problems = validate_action({"say": "s", "kind": "report", "title": "t",
                                "target": "scheduler-builder", "closes": True})
    assert any("closes" in p and "answers" in p and "settles" in p for p in problems)
    # and with no target at all (triage) the original sentence stands too
    assert any("answers" in p for p in validate_action(
        {"say": "s", "kind": "report", "title": "t", "closes": True}))


def test_a_bare_closure_is_refused_with_what_closes_needs():
    with pytest.raises(ValueError, match="closes needs a disposal to complete") as caught:
        Disposal(closes=True)

    message = str(caught.value)
    assert "answers" in message
    assert "settles" in message
    # the message must also name the shape the row was RAISED about, or the caller it breaks
    # has to guess why its own call stopped working
    assert "supersedes" in message


@pytest.mark.parametrize("disposal", [{"answers": "R1"}, {"settles": ("R1",)}])
def test_a_closure_naming_either_disposal_still_stands(disposal):
    """The refusal is about a closure with NOTHING to close. Both real faces keep working —
    `answers` for the one exchange a reply belongs to, `settles` for the many-rows claim.
    """
    assert Disposal(closes=True, **disposal).closes is True


def test_a_fold_carrying_a_closure_is_refused_where_it_is_built(tmp_path):
    """The exact shape D196 names: `closes` beside `supersedes` alone. `supersedes` is a
    `file_report` argument rather than a `Disposal` field, so the refusal has to bite when the
    disposal is CONSTRUCTED — which is what makes it reachable for every out-of-band caller,
    whatever else it passes.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)

    with pytest.raises(ValueError, match="closes needs a disposal to complete"):
        file_report(home, routine="x", run_id="x:1", title="folding three rows",
                    target="self-audit", supersedes=("R1", "R2", "R3"),
                    disposal=Disposal(closes=True))

    # and nothing was written: a refused closure files no row at all
    assert read_reports(reports_path(home)) == []


def test_a_fold_that_also_settles_its_rows_is_the_accepted_form(tmp_path):
    """The way through, which the refusal's message teaches: say what is settled. A fold that
    genuinely ends the rows it took over names them in `settles` and may then be born settled.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    target = home / "self-audit"
    (target / "inbox").mkdir(parents=True)

    filed = file_report(home, routine="x", run_id="x:1", title="folded and finished",
                        target="self-audit", target_dir=target, supersedes=("R1",),
                        disposal=Disposal(settles=("R1",), closes=True))

    assert filed is not None
    row = read_reports(reports_path(home))[-1]
    assert row["closes"] is True
    assert row["settles"] == ["R1"]
