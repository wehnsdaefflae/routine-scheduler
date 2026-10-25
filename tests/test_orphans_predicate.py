"""F768: the two surfaces that ask "can anything ever drain this row?" use ONE predicate.

The Messages page shows both answers at once, over the same ledger: the `unroutable` count
(`readmodels/item_counts.unroutable`, off `registry.scan`'s keys) and the "addressed, never
delivered" banner (`readmodels/orphans.find_undelivered`, which used to probe
`<home>/<target>/routine.yaml` by hand). `registry.scan` keys its catalog by the slug inside
each `routine.yaml` and skips `.archive/`; a raw path probe keys by DIRECTORY NAME. So the two
diverged wherever a routine's directory name and its slug disagree — which `registry.py:226`
records as a reported problem rather than refusing — and one surface could call a row drainable
while the other called it stranded. Two surfaces disagreeing about whether work is drainable is
the shape of the defect F738 was built to end, so it is pinned here rather than left to drift.
"""
from __future__ import annotations

from rsched.readmodels import item_counts
from rsched.readmodels.orphans import find_undelivered


def _report_item(row: dict) -> dict:
    """The same report as the item index sees it (`readmodels/items`): `to` is its target."""
    return {"type": "report", "status": "open", "to": row["target"], "id": row["id"]}


def test_the_banner_and_the_unroutable_count_agree_on_every_row(tmp_path):
    """A routine whose DIRECTORY NAME is not its slug makes the two predicates disagree twice,
    in opposite directions: a row addressed to the directory reads deliverable to the banner
    (the path is there) and stranded to the count (the daemon fires no such slug), and a row
    addressed to the slug reads the other way round. The daemon's own authority wins both.
    """
    # a routine the daemon holds as `radar`, living in a directory called `radar-v2`
    (tmp_path / "radar-v2" / "inbox").mkdir(parents=True)
    (tmp_path / "radar-v2" / "routine.yaml").write_text("slug: radar\n", encoding="utf-8")
    installed = {"radar"}            # exactly what registry.scan would key

    rows = [
        {"id": "R20", "target": "radar-v2", "routine": "operator", "title": "to the directory",
         "ts": "2026-10-25T10:00:00+02:00"},
        {"id": "R21", "target": "radar", "routine": "operator", "title": "to the slug",
         "ts": "2026-10-25T11:00:00+02:00"},
    ]
    found = {o["id"]: o for o in find_undelivered(rows, tmp_path, installed)}
    assert set(found) == {"R20", "R21"}        # neither has an inbox message

    for row in rows:
        stranded = item_counts.unroutable(_report_item(row), installed)
        # the banner's wording turns on this field: False prints "which is not a routine on
        # this instance", True prints "whose inbox holds no message for it"
        assert found[row["id"]]["target_installed"] is not stranded, row["id"]

    assert found["R20"]["target_installed"] is False   # the directory is not the fleet's name
    assert found["R21"]["target_installed"] is True    # the daemon fires `radar`


def test_an_archived_routine_is_stranded_on_both_surfaces(tmp_path):
    """The live specimen (R2629 → `grants-radar`, archived 2026-10-19): an archived incarnation
    starts no run and owns no inbox, so nobody can drain the row. `registry.scan` skips
    `.archive/`, so it is absent from `installed` — and the banner says so too.
    """
    (tmp_path / ".archive" / "grants-radar-20261019-023848").mkdir(parents=True)
    (tmp_path / ".archive" / "grants-radar-20261019-023848" / "routine.yaml").write_text(
        "slug: grants-radar\n", encoding="utf-8")
    row = {"id": "R2629", "target": "grants-radar", "routine": "steward-hub-maintainer",
           "title": "addressed to an archived routine", "ts": "2026-10-19T09:00:00+02:00"}

    found = find_undelivered([row], tmp_path, {"self-audit"})
    assert [o["id"] for o in found] == ["R2629"]
    assert found[0]["target_installed"] is False
    assert item_counts.unroutable(_report_item(row), {"self-audit"}) is True


def test_without_fleet_knowledge_nothing_is_called_stranded(tmp_path):
    """`installed` empty means the caller has not said what the fleet is. `unroutable` already
    judged nothing then; the banner must not claim a routine is gone on no knowledge either.
    """
    (tmp_path / "radar" / "inbox").mkdir(parents=True)
    row = {"id": "R30", "target": "radar", "routine": "operator", "title": "no fleet given",
           "ts": "2026-10-25T12:00:00+02:00"}

    found = find_undelivered([row], tmp_path, ())
    assert [o["id"] for o in found] == ["R30"]
    assert found[0]["target_installed"] is True
    assert item_counts.unroutable(_report_item(row), ()) is False
