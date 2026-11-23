"""A closure with nothing to close is REFUSED, at both entrances (D196)."""

from __future__ import annotations

import pytest

from rsched.engine.actions import validate_action
from rsched.reports import Disposal, file_report, read_reports, reports_path

# -- the ACTION entrance, where the target decides what the refusal can ask for --------------


def test_a_runtree_message_is_not_refused_at_all():
    """SUPERSEDED BY F859 (0.522.0) — this case used to assert a target-aware REFUSAL."""
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


def test_the_refusal_tells_an_originating_notice_what_to_do():
    """The shape 10 of 13 measured rejections actually sent: `target` + `closes`, no
    `supersedes` — a run FILING a notice it wants nobody to reply to.
    """
    for problems in (
        validate_action({"say": "s", "kind": "report", "title": "fixed, no reply needed",
                         "target": "scheduler-builder", "closes": True}),
        validate_action({"say": "s", "kind": "report", "title": "fyi", "closes": True}),
    ):
        assert problems, "the grammar is unchanged — a bare closure is still refused"
        said = " ".join(problems)
        assert "leave 'closes' out" in said, said

    with pytest.raises(ValueError, match="closes needs a disposal to complete") as caught:
        Disposal(closes=True)
    assert "leave `closes` out" in str(caught.value)


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


def test_the_report_bullet_teaches_the_pairing_before_a_run_is_refused_for_it():
    """F942: the refusal above was exemplary and runs kept earning it anyway — 8 rejections
    across 5 routines in 4 days — because the always-on `report` bullet said only that a reply
    without `closes` is "a new open report waiting for one more reply" and never that `closes`
    needs `answers` or `settles` beside it, so a run filing an originating notice set it and
    lost the turn. A refusal that teaches is the second line of defence; the bullet every turn
    carries is the first.
    """
    from rsched.engine.kindprose import kind_bullets

    bullet = kind_bullets({"report"}, util_confirm="", ask_timeout_min=480)
    assert "`answers`/`settles`" in bullet, bullet
    assert "refused" in bullet, bullet
    # and the refusal it warns about must still be the one the grammar gives, so the bullet
    # cannot drift into describing a rule that no longer exists
    problems = validate_action({"say": "s", "kind": "report", "title": "fyi", "closes": True})
    assert any("answers" in p and "settles" in p for p in problems), problems


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
