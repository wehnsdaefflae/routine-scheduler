"""The two one-shots 0.345.0 ships, and the property that matters for both: they must be
SAFE to run against a ledger and a library they were not written for.

A migration here runs at daemon boot on the production instance, unattended, and this box
boots from the working tree — so "conservative in both directions, and it names what it
skips" is the contract, not a nicety.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from rsched.migrate_problem_routing_rule import PREVIOUS_SHA, SLUG
from rsched.migrate_problem_routing_rule import migrate as migrate_rule
from rsched.migrate_routed_reports import ROUTED
from rsched.migrate_routed_reports import migrate as migrate_reports
from rsched.readmodels import items
from rsched.reports import read_reports, reports_path


def _ledger(home: Path, rows: list[dict]) -> None:
    (home / ".control").mkdir(parents=True, exist_ok=True)
    with reports_path(home).open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _order(rid: str, **extra) -> dict:
    return {"id": rid, "ts": "2026-09-14T10:00:00+02:00", "routine": "freelance-radar",
            "run_id": "freelance-radar:20260914-061838", "title": rid, "detail": "", **extra}


def _carriers(home: Path) -> list[dict]:
    return [_order(cid, routine="self-audit", target=target)
            for cid, (target, _) in ROUTED.items()]


def test_it_folds_exactly_the_rows_each_carrier_declares(tmp_path):
    home = tmp_path / "routines"
    home.mkdir()
    originals = [_order(rid) for _, ids in ROUTED.values() for rid in ids]
    _ledger(home, [*originals, *_carriers(home)])

    notes = migrate_reports(home)
    rows = {r["id"]: r for r in read_reports(reports_path(home))}
    for carrier, (target, ids) in ROUTED.items():
        for rid in ids:
            assert rows[rid]["superseded"]["by"] == carrier, rid
            assert rows[rid]["superseded"]["to"] == target
    assert all("folded" in n for n in notes)


def test_it_leaves_a_row_that_moved_on_alone(tmp_path):
    """Retracted, already folded, or since given a target of its own: all three mean something
    happened after the hand-off, and this migration only records the hand-off itself.
    """
    home = tmp_path / "routines"
    home.mkdir()
    _ledger(home, [
        _order("R1518"),                                   # the one R1525 routes
        _order("R1528", target="steward-hub-maintainer"),  # given its own target since
        _order("R1530"),
        {"id": "R1530", "event": "retracted", "ts": "2026-09-15T09:00:00+02:00"},
        *_carriers(home),
    ])
    migrate_reports(home)
    rows = {r["id"]: r for r in read_reports(reports_path(home))}
    assert rows["R1518"]["superseded"]["by"] == "R1525"
    assert "superseded" not in rows["R1528"], "it has an owner of its own now"
    assert "superseded" not in rows["R1530"], "retracted — the fold would rewrite history"


def test_it_is_idempotent(tmp_path):
    home = tmp_path / "routines"
    home.mkdir()
    originals = [_order(rid) for _, ids in ROUTED.values() for rid in ids]
    _ledger(home, [*originals, *_carriers(home)])
    migrate_reports(home)
    first = reports_path(home).read_text(encoding="utf-8")
    migrate_reports(home)
    assert reports_path(home).read_text(encoding="utf-8") == first


def test_a_ledger_it_was_not_written_for_is_untouched_and_says_so(tmp_path):
    home = tmp_path / "routines"
    home.mkdir()
    _ledger(home, [_order("R1")])
    before = reports_path(home).read_text(encoding="utf-8")
    notes = migrate_reports(home)
    assert reports_path(home).read_text(encoding="utf-8") == before
    assert all("not in this ledger" in n for n in notes)


def test_the_folded_rows_leave_triage(tmp_path):
    """The point of the whole one-shot: after it, the next triage pass does not re-route work
    that was handed off days ago.
    """
    home = tmp_path / "routines"
    home.mkdir()
    (home / "self-audit").mkdir()
    originals = [_order(rid) for _, ids in ROUTED.values() for rid in ids]
    _ledger(home, [*originals, *_carriers(home)])
    def untriaged() -> list[str]:
        built = items._build(*items.source_paths(home / "self-audit", home))
        return [i["id"] for i in built["items"]
                if i["type"] == "report" and i["status"] == "open" and not i["to"]]

    assert len(untriaged()) == len(originals)
    migrate_reports(home)
    assert untriaged() == [], "every routed row now reads its carrier's thread"


# ---- the rule one-shot -------------------------------------------------------------------


def test_the_rule_is_replaced_only_while_it_matches_the_seed_it_supersedes(tmp_path):
    rules, seed = tmp_path / "rules", tmp_path / "seed"
    rules.mkdir()
    seed.mkdir()
    (seed / f"{SLUG}.md").write_text("---\nassists:\n  - id: x\ntags: [a]\n---\n# new\n",
                                     encoding="utf-8")

    (rules / f"{SLUG}.md").write_text("locally edited by the operator\n", encoding="utf-8")
    assert "locally edited" in migrate_rule(rules, seed)[0]
    assert (rules / f"{SLUG}.md").read_text(encoding="utf-8") == "locally edited by the operator\n"


def test_the_rule_is_replaced_when_the_hash_matches(tmp_path, monkeypatch):
    rules, seed = tmp_path / "rules", tmp_path / "seed"
    rules.mkdir()
    seed.mkdir()
    old = "the previous seed text\n"
    (rules / f"{SLUG}.md").write_text(old, encoding="utf-8")
    (seed / f"{SLUG}.md").write_text("---\nassists:\n  - id: x\ntags: [a]\n---\n# new\n",
                                     encoding="utf-8")
    monkeypatch.setattr("rsched.migrate_problem_routing_rule.PREVIOUS_SHA",
                        hashlib.sha256(old.encode()).hexdigest())
    assert "revised" in migrate_rule(rules, seed)[0]
    assert "# new" in (rules / f"{SLUG}.md").read_text(encoding="utf-8")
    # …and a second pass finds it already revised rather than reverting it
    assert "already revised" in migrate_rule(rules, seed)[0]


def test_the_pinned_hash_is_the_rule_this_revision_actually_replaces():
    """The live library is byte-identical to the seed at the commit this shipped from, so the
    hash is checkable from the repo: it must NOT match the revised seed, or the migration
    would refuse to do anything on a library that still holds the old text.
    """
    current = (Path(__file__).resolve().parents[1] / "library-seed" / "rules" / f"{SLUG}.md")
    assert hashlib.sha256(current.read_bytes()).hexdigest() != PREVIOUS_SHA
    assert "assists:" in current.read_text(encoding="utf-8")
