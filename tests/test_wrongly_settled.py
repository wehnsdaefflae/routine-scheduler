"""`rsched reports --wrongly-settled` — which rows a NON-OWNER disposed of (R3246).

D182 (0.425.0) closed the gate in `report_threads.disposable`: `answers` and `settles` dispose
of a row only for the routine it was DELIVERED to, which nothing enforced before. Measured over
the 2,110-row ledger before that fix, 492 rows were disposed of by someone who was not their
owner, 317 of them never routed to anyone — so they left triage with no owner having read them.
The operator's own bug report R2552 vanished that way 14 seconds after he filed it.

Leaving those rows settled is deliberate and is not undone here: re-deriving them would hand
triage hundreds of rows at once. But "do not re-open them" and "nobody can ever see which ones
they were" are two different decisions, and only the first was made — until this verb the only
artefact that knew the number was a docstring comment. So this is a READ: no row re-opens, no
read-model verdict changes, and the listing is built from the same predicate the WRITE gate
refuses with, so the two can never disagree about who owns a row.

The ledger rows here are written through `reports.file_report` directly, which carries no
disposal gate — that is where the historical rows came from, and it is the only way to produce
the shape `handle_report` now refuses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rsched import cli
from rsched.report_threads import disposed_by_non_owner
from rsched.reports import (
    Disposal,
    file_report,
    read_reports,
    reports_path,
    retract_report,
)


def _file(home: Path, routine: str, title: str, *, target: str = "",
          disposal: Disposal | None = None) -> None:
    filed = file_report(home, routine=routine, run_id=f"{routine}:20260915-120000",
                        title=title, target=target, disposal=disposal)
    assert filed is not None


def _rows(home: Path) -> list[dict]:
    return read_reports(reports_path(home))


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "routines"
    (h / ".control").mkdir(parents=True)
    return h


# ---- the predicate ----------------------------------------------------------------------


def test_a_sender_settling_its_own_unrouted_row_is_listed(home):
    """The measured specimen: R2552's shape — an untargeted row retired by its own follow-up."""
    _file(home, "freelance-radar", "web-request clobbers --out on a 403")
    _file(home, "freelance-radar", "more on the same", disposal=Disposal(answers="R1"))

    [found] = disposed_by_non_owner(_rows(home))
    assert found["id"] == "R1"
    assert found["sender"] == "freelance-radar"
    assert found["title"] == "web-request clobbers --out on a 403"
    assert found["owner"] == "", "never routed to anyone — it left triage unread"
    assert found["disposed_by"] == "R2"
    assert found["disposer"] == "freelance-radar"
    assert found["ts"] == {r["id"]: r for r in _rows(home)}["R1"]["ts"]


def test_the_owners_own_disposal_is_not_listed(home):
    """The allowed shape: the routine a row was DELIVERED to disposed of it."""
    _file(home, "self-audit", "a util defect", target="global-utils-review")
    _file(home, "global-utils-review", "fixed", target="self-audit",
          disposal=Disposal(answers="R1", closes=True))

    assert disposed_by_non_owner(_rows(home)) == []


def test_triage_disposing_of_an_unaddressed_row_is_not_listed(home):
    """F840: an unaddressed row's recipient IS triage, so triage settling it is its owner's
    disposal — the listing must not read the fix as the defect."""
    _file(home, "self-audit", "permission_audit cannot see a declaration change")
    _file(home, "self-audit", "fixed in scripts/permission_audit.py",
          disposal=Disposal(answers="R1", closes=True))

    assert disposed_by_non_owner(_rows(home)) == []


def test_a_third_routine_settling_someone_elses_row_names_the_owner(home):
    """The other half of D182's two refusals: delivered elsewhere, so the owner is known and
    named — which is what separates these from the 317 that had no owner at all."""
    _file(home, "self-audit", "a util defect", target="global-utils-review")
    _file(home, "freelance-radar", "I'll take this", target="self-audit",
          disposal=Disposal(settles=("R1",)))

    [found] = disposed_by_non_owner(_rows(home))
    assert found["id"] == "R1"
    assert found["owner"] == "global-utils-review"
    assert found["disposer"] == "freelance-radar"
    assert found["disposed_by"] == "R2"


def test_the_first_disposal_decides(home):
    """"An earlier settlement wins over a later one" (docs/items.md): a row its OWNER settled
    was properly settled, and a later report naming it must not make it read as wrongly
    settled — otherwise every thread that goes on being discussed joins the list."""
    _file(home, "self-audit", "a util defect", target="global-utils-review")
    _file(home, "global-utils-review", "fixed", target="self-audit",
          disposal=Disposal(answers="R1", closes=True))
    _file(home, "freelance-radar", "mentioning it again", disposal=Disposal(settles=("R1",)))

    assert disposed_by_non_owner(_rows(home)) == []


def test_a_retracted_reply_disposed_of_nothing(home):
    """A retracted reply's answer never arrived (`_disposed`), so it never took a row out."""
    _file(home, "self-audit", "a util defect", target="global-utils-review")
    _file(home, "freelance-radar", "I'll take this", target="self-audit",
          disposal=Disposal(settles=("R1",)))
    retract_report(home, "R2")

    assert disposed_by_non_owner(_rows(home)) == []


def test_an_id_the_ledger_never_had_is_not_a_lost_row(home):
    """A disposal naming an unknown id is `unrouted` to the write gate, but there is no row to
    have lost — listing it would invent a victim."""
    _file(home, "freelance-radar", "a real row")
    _file(home, "freelance-radar", "typo", disposal=Disposal(settles=("R99",)))

    assert [r["id"] for r in disposed_by_non_owner(_rows(home))] == []


# ---- the CLI verb -----------------------------------------------------------------------


def test_the_cli_verb_prints_one_line_per_row(cli_server, capsys):
    home = cli_server.routines_home
    _file(home, "freelance-radar", "web-request clobbers --out on a 403")
    _file(home, "freelance-radar", "more on the same", disposal=Disposal(answers="R1"))

    assert cli.main(["reports", "--wrongly-settled"]) == 0
    captured = capsys.readouterr()
    assert "R1" in captured.out
    assert "freelance-radar" in captured.out
    assert "web-request clobbers --out on a 403" in captured.out
    assert "non-owner disposals: 1" in captured.err


def test_the_cli_verb_answers_in_json(cli_server, capsys):
    home = cli_server.routines_home
    _file(home, "self-audit", "a util defect", target="global-utils-review")
    _file(home, "freelance-radar", "I'll take this", target="self-audit",
          disposal=Disposal(settles=("R1",)))

    assert cli.main(["reports", "--wrongly-settled", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in payload["wrongly_settled"]] == ["R1"]
    assert payload["wrongly_settled"][0]["owner"] == "global-utils-review"
    assert payload["count"] == 1


def test_the_cli_verb_on_an_instance_with_no_ledger_yet(cli_server, capsys):
    """A read that answers zero is the healthy instance's answer, not an error."""
    assert cli.main(["reports", "--wrongly-settled", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"wrongly_settled": [], "count": 0}
