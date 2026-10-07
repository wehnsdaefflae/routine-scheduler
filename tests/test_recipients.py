"""Where a message goes when its addressee reads nothing (rsched/recipients.py).

A routine switched off by the operator, or retired by its finish line, starts no run — so a
report or a shared-store note addressed to it would never be read, and both channels refuse at
sending. The refusal used to stop at "it is off"; the sender had to guess where the work went.
The usual answer is that it MOVED — most plainly when several routines became the tasks of one —
so every refusal now names the routines that would read it, best first.
"""

from __future__ import annotations

import json

import pytest
import yaml

from conftest import make_test_server
from helpers import run_context
from rsched import lanes, recipients, sharedstores, tasks
from rsched.engine.fileops import do_write_file
from rsched.grantpolicy import GrantPolicy


@pytest.fixture
def server(tmp_path):
    return make_test_server(tmp_path)


def _routine(server, slug: str, *, enabled: bool = True, tags: tuple[str, ...] = (),
             roots: tuple = ()) -> None:
    d = server.routines_home / slug
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"slug": slug, "enabled": enabled, "tags": list(tags),
           "fs_write_roots": [str(r) for r in roots]}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (d / "main.md").write_text("# recipe\n", encoding="utf-8")


def _carry(server, slug: str, tid: str, origin: str) -> None:
    """`slug` keeps a task `tid` that used to be the routine `origin`."""
    task = tasks.new_task(tid, tid, "", by="operator")
    task["origin"] = origin
    doc = tasks.empty()
    doc["tasks"].append(task)
    tasks.save(server.routines_home / slug, doc)


@pytest.fixture
def merged(server):
    """The FAU merge in miniature: `old` was switched off when `umbrella` took it over as its
    task `old`; `peer` shares old's store, `cousin` shares two of its tags, `far` one, and
    `retired-peer` would rank first by every measure but reads nothing itself."""
    store = sharedstores.stores_home(server.routines_home) / "grp-fau"
    store.mkdir(parents=True)
    _routine(server, "old", enabled=False, tags=("fau", "grant"), roots=(store,))
    _routine(server, "umbrella", tags=("fau",), roots=(store,))
    _routine(server, "peer", roots=(store,))
    _routine(server, "cousin", tags=("fau", "grant"))
    _routine(server, "far", tags=("grant",))
    _routine(server, "retired-peer", enabled=False, tags=("fau", "grant"), roots=(store,))
    _routine(server, "unrelated")
    _carry(server, "umbrella", "old-work", "old")
    return store


def test_the_routine_carrying_the_work_comes_first_and_names_the_task(server, merged):
    got = recipients.suggest(server, server.routines_home, "old")
    assert [s.slug for s in got] == ["umbrella", "peer", "cousin", "far"]
    first = got[0]
    assert first.task == "old-work"
    assert "carries 'old' as its task 'old-work'" in first.why
    # store before tags, more shared tags before fewer; nobody who reads nothing is offered
    assert got[1].why == "shares the store grp-fau"
    assert got[2].why == "shares the tags fau, grant"
    assert got[3].why == "shares the tag grant"


def test_the_sender_and_the_unrelated_are_never_offered(server, merged):
    got = [s.slug for s in recipients.suggest(server, server.routines_home, "old",
                                              exclude=("umbrella",))]
    assert "umbrella" not in got          # a run cannot address a report to itself
    assert "unrelated" not in got and "retired-peer" not in got and "old" not in got


def test_lane_mates_rank_above_store_and_tag_sharers(server, merged):
    _routine(server, "next-in-line")
    lanes.create(server.routines_home, name="FAU",
                 members=[{"slug": "old"}, {"slug": "next-in-line"}])
    got = recipients.suggest(server, server.routines_home, "old")
    assert [s.slug for s in got][:2] == ["umbrella", "next-in-line"]
    assert "fires beside it in the lane 'FAU'" in got[1].why


def test_a_routine_whose_config_cannot_be_read_still_reads(server):
    """The registry substitutes a DISABLED config for an unloadable routine.yaml — and a report
    about that broken config is the one that must get through."""
    _routine(server, "broken")
    (server.routines_home / "broken" / "routine.yaml").write_text(": : not yaml : :\n",
                                                                  encoding="utf-8")
    assert "broken" in recipients.reader_slugs(server, server.routines_home)


def test_the_suggestions_read_as_one_sentence():
    text = recipients.render([{"slug": "umbrella", "why": "carries 'old' as its task 'old-work'",
                               "task": "old-work"},
                              {"slug": "peer", "why": "shares the store grp-fau"}])
    assert text.startswith("Routines that would read it: 'umbrella'")
    assert "address 'umbrella' and name the task 'old-work' in the title" in text
    assert "'peer' (shares the store grp-fau)" in text
    assert recipients.render([]).startswith("No routine that would read it")


# ---- the note channel ------------------------------------------------------------------------


def _ctx(server, slug: str):
    return run_context(server.routines_home / slug, "20261007-080000", server=server,
                       grants=GrantPolicy())


def test_a_note_to_a_switched_off_sharer_is_refused_with_the_sharers_that_read(server, merged):
    """A sharer that starts no run would never drain its notes — the write is refused, and the
    suggestions are narrowed to the store's own sharers: a note can reach nobody else."""
    path = sharedstores.notes_dir(merged, "old") / "note-1.json"
    refusal = recipients.note_refusal(server, path)
    assert refusal is not None
    assert "'old' shares the store grp-fau but is disabled" in refusal
    assert "'umbrella'" in refusal and "'peer'" in refusal
    assert "'cousin'" not in refusal          # shares tags, not the store — a report reaches it
    assert "name the task 'old-work' in the note" in refusal     # a note has no title
    assert "addressed `report`" in refusal
    obs = do_write_file({"kind": "write_file", "path": str(path),
                         "content": json.dumps({"from": "peer", "text": "hi"})},
                        _ctx(server, "peer"))
    assert "but is disabled" in obs.get("error", "")
    assert not path.exists()


def test_a_note_to_a_sharer_that_reads_still_lands(server, merged):
    assert recipients.note_refusal(
        server, sharedstores.notes_dir(merged, "umbrella") / "note-1.json") is None
    # and the store's own rule still comes first for a non-sharer
    refusal = recipients.note_refusal(
        server, sharedstores.notes_dir(merged, "cousin") / "note-1.json")
    assert refusal is not None and "does not share the store" in refusal


# ---- the task's origin -----------------------------------------------------------------------


def test_an_origin_that_is_not_a_slug_is_a_store_problem(server, merged):
    doc = tasks.load(server.routines_home / "umbrella")
    doc["tasks"][0]["origin"] = "Not A Slug"
    tasks.save(server.routines_home / "umbrella", doc)
    assert any("which is not a routine slug" in p
               for p in tasks.problems(server.routines_home / "umbrella"))
