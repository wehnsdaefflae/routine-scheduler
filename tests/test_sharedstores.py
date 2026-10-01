"""SHARED STORES and the notes channel between the routines sharing one (F335).

The design in one line: a routine SHARES a store by naming it among its own `fs_write_roots`;
nothing else records the sharing — so these pin that the set of sharers is read back out of
the routines' own files, that a note is delivered exactly once to a routine that shares the
store it was written into, and that a note nobody would ever read is refused where it is
WRITTEN (the engine's write gate), naming the channel that does reach its addressee.

A NOTE is coordination; a REPORT is work somebody must act on, tracked until answered. The
reader's half is here too: the state-digest block a drained note renders into, its backlog cap,
and the harness paragraph that names each store and who else shares it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from conftest import make_test_server
from helpers import run_context
from rsched import sharedstores
from rsched.config import load_routine
from rsched.engine.fileops import do_edit_file, do_write_file
from rsched.engine.fsops import do_delete, do_mkdir, do_move
from rsched.engine.run_context import RunContext
from rsched.grantpolicy import GrantPolicy


@pytest.fixture
def server(tmp_path):
    return make_test_server(tmp_path)


def _routine(server, slug: str, *roots) -> None:
    """A routine on disk whose OWN routine.yaml names `roots` as its write roots — the only
    place sharing is recorded, so the only place the channel looks."""
    d = server.routines_home / slug
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"slug": slug, "name": slug, "description": "shared store test routine",
           "fs_write_roots": [str(r) for r in roots]}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (d / "main.md").write_text("# recipe\n", encoding="utf-8")


@pytest.fixture
def team(server):
    """Three routines sharing FAU, one sharing only LABS, one sharing nothing, and a meta
    routine whose root is the whole routines home — it covers every store and shares none."""
    home = sharedstores.stores_home(server.routines_home)
    fau, labs = home / "grp-fau", home / "grp-labs"
    for store in (fau, labs):
        store.mkdir(parents=True)
    for slug in ("ingest", "steward", "sender"):
        _routine(server, slug, fau)
    _routine(server, "stranger", labs)
    _routine(server, "loner")
    _routine(server, "improver", server.routines_home)
    return SimpleNamespace(fau=fau, labs=labs)


def note(store, *, sender: str, to: str, text: str) -> None:
    """Write a note the way a ROUTINE does: an ordinary file into the shared store. There is
    no writer in `sharedstores` to call — deliberately, the engine exposes no note action."""
    d = sharedstores.notes_dir(store, to)
    d.mkdir(parents=True, exist_ok=True)
    seq = len(list(d.glob("note-*.json")))
    (d / f"note-20260902-120000-{seq:06d}.json").write_text(
        json.dumps({"from": sender, "ts": "2026-09-02T12:00:00+02:00", "text": text}),
        encoding="utf-8")


def _roots(server, slug: str) -> list:
    cfg, _ = load_routine(server.routines_home / slug)
    assert cfg is not None
    return list(cfg.fs_write_roots)


# ---- who shares what ------------------------------------------------------------------------


def test_a_store_is_a_write_root_directly_under_the_stores_home(server, team):
    assert sharedstores.stores_of(server.routines_home, _roots(server, "steward")) == [
        team.fau.resolve()]
    # a root ABOVE the stores covers every one of them and shares none
    assert sharedstores.stores_of(server.routines_home, _roots(server, "improver")) == []
    # and a directory inside a store is not a store either
    assert sharedstores.stores_of(server.routines_home, [team.fau / "sub"]) == []


def test_sharers_are_read_back_out_of_the_routines_own_files(server, team):
    assert sharedstores.sharers(server.routines_home, team.fau) == [
        "ingest", "sender", "steward"]
    assert sharedstores.sharers(server.routines_home, team.labs) == ["stranger"]
    # a symlink to the store names the same directory
    link = server.routines_home.parent / "fau-link"
    link.symlink_to(team.fau)
    _routine(server, "linked", link)
    assert "linked" in sharedstores.sharers(server.routines_home, team.fau)


def test_an_unreadable_routine_file_is_a_non_sharer_not_an_exception(server, team):
    (server.routines_home / "sender" / "routine.yaml").write_text("{not: yaml: [", "utf-8")
    assert sharedstores.sharers(server.routines_home, team.fau) == ["ingest", "steward"]


# ---- the notes channel ----------------------------------------------------------------------


def test_a_note_reaches_a_sharer_and_is_gone_once_read(server, team):
    note(team.fau, sender="ingest", to="steward", text="staged 4 new items in ingest-inbox.md")
    got = sharedstores.drain(server.routines_home, "steward", _roots(server, "steward"))
    assert len(got) == 1
    assert got[0]["from"] == "ingest" and "staged 4 new items" in got[0]["text"]
    # read-and-drop, exactly like inbox/: delivered once, never re-shown every run after
    assert sharedstores.drain(server.routines_home, "steward", _roots(server, "steward")) == []


def test_a_routine_reads_notes_only_from_the_stores_it_shares(server, team):
    note(team.fau, sender="ingest", to="stranger", text="hi")
    assert sharedstores.drain(server.routines_home, "stranger", _roots(server, "stranger")) == []
    # the meta routine COULD read the store — it just does not share it
    note(team.fau, sender="ingest", to="improver", text="hi")
    assert sharedstores.drain(server.routines_home, "improver", _roots(server, "improver")) == []


def test_an_oversized_note_is_capped_on_the_way_in(server, team):
    """The cap runs on the READ, because the read is the only half the engine owns — a
    routine writes the file itself with nothing validating it, straight into a prompt."""
    note(team.fau, sender="ingest", to="steward", text="x" * (sharedstores.TEXT_CAP + 500))
    got = sharedstores.drain(server.routines_home, "steward", _roots(server, "steward"))
    assert len(got[0]["text"]) == sharedstores.TEXT_CAP


def test_a_note_is_not_a_report_and_leaves_no_trace_to_close(server, team):
    note(team.fau, sender="ingest", to="steward", text="x")
    sharedstores.drain(server.routines_home, "steward", _roots(server, "steward"))
    assert not (server.routines_home / ".control" / "reports.jsonl").exists()
    assert not (server.routines_home / "steward" / "inbox").exists()


def test_a_corrupt_note_file_is_skipped_not_fatal(server, team):
    note(team.fau, sender="ingest", to="steward", text="good")
    d = sharedstores.notes_dir(team.fau, "steward")
    (d / "note-broken.json").write_text("{not json", encoding="utf-8")
    got = sharedstores.drain(server.routines_home, "steward", _roots(server, "steward"))
    assert [n["text"] for n in got] == ["good"]
    assert not list(d.glob("note-*.json"))       # the junk is cleared too, not left to recur


def test_leaving_a_store_cuts_the_channel_both_ways(server, team):
    """Sharing is read live from the routine's own file — a routine that drops the root stops
    being told the store exists and stops being reached through it. The note already written
    stays in the store, unreachable: leaving is not a delivery."""
    note(team.fau, sender="ingest", to="sender", text="for you")
    _routine(server, "sender")                             # rewritten with no roots
    assert sharedstores.contract_section(server.routines_home, "sender",
                                         _roots(server, "sender")) == ""
    assert sharedstores.drain(server.routines_home, "sender", _roots(server, "sender")) == []
    assert sharedstores.sharers(server.routines_home, team.fau) == ["ingest", "steward"]
    assert (team.fau / "notes" / "sender").is_dir()


# ---- what the run reads ---------------------------------------------------------------------


def test_digest_section_renders_the_drained_notes(server, team):
    roots = _roots(server, "steward")
    assert sharedstores.digest_section([]) == ""
    note(team.fau, sender="ingest", to="steward", text="alpha")
    note(team.fau, sender="sender", to="steward", text="beta")
    text = sharedstores.digest_section(sharedstores.drain(server.routines_home, "steward", roots))
    assert "NOTES FROM ROUTINES YOU SHARE A STORE WITH" in text
    assert "from ingest" in text and "alpha" in text
    assert "from sender" in text and "beta" in text
    assert "nobody is waiting on a reply" in text
    assert sharedstores.drain(server.routines_home, "steward", roots) == []


def test_digest_caps_a_backlog_and_says_it_dropped_the_oldest(server, team):
    """A nudge, not a mailbox: past the cap the run is handed the NEWEST notes and told how
    many older ones went — silence about the drop would read as 'that was everything'."""
    for i in range(sharedstores.MAX_NOTES_SHOWN + 3):
        note(team.fau, sender="ingest", to="steward", text=f"note {i:02d}")
    roots = _roots(server, "steward")
    text = sharedstores.digest_section(sharedstores.drain(server.routines_home, "steward", roots))
    assert text.count("- from ingest") == sharedstores.MAX_NOTES_SHOWN
    assert "3 older note(s) were dropped unread" in text
    assert "note 22" in text and "note 00" not in text
    assert sharedstores.drain(server.routines_home, "steward", roots) == []


def test_contract_names_each_store_and_the_other_routines_sharing_it(server, team):
    _routine(server, "steward", team.fau, team.labs)
    text = sharedstores.contract_section(server.routines_home, "steward",
                                         _roots(server, "steward"))
    fau_row = next(line for line in text.splitlines() if str(team.fau.resolve()) in line)
    labs_row = next(line for line in text.splitlines() if str(team.labs.resolve()) in line)
    assert "ingest" in fau_row and "sender" in fau_row and "steward" not in fau_row
    assert "stranger" in labs_row
    assert "note-<anything>.json" in text and "report" in text
    # a routine sharing no store is told nothing — there is nobody to leave a note for
    assert sharedstores.contract_section(server.routines_home, "loner",
                                         _roots(server, "loner")) == ""


# ---- the write-side guard -------------------------------------------------------------------


def test_note_refusal_judges_only_note_paths(server, team):
    home = server.routines_home
    assert sharedstores.note_refusal(home, team.fau / "notes" / "steward" / "note-1.json") is None
    assert sharedstores.note_refusal(home, team.fau / "ingest-inbox.md") is None
    assert sharedstores.note_refusal(home, server.routines_home / "steward" / "x.md") is None
    refusal = sharedstores.note_refusal(home, team.fau / "notes" / "stranger" / "note-1.json")
    assert refusal is not None
    assert "'stranger' does not share the store" in refusal
    assert "ingest, sender, steward" in refusal
    assert "`report` with target 'stranger'" in refusal
    # the mailbox directory itself is a note path too — creating it for a stranger is the
    # first half of the same mistake
    assert sharedstores.note_refusal(home, team.fau / "notes" / "stranger") is not None


def _ctx(server, slug: str) -> RunContext:
    return run_context(server.routines_home / slug, "20260929-070000", server=server,
                       grants=GrantPolicy())


def test_the_write_gate_refuses_a_note_nobody_would_read(server, team):
    """The cause-fix for a real leak: notes written to routines that did not share the store
    sat there unread while their writer believed them delivered. The write is refused; the
    refusal names the channel that does reach the addressee."""
    ctx = _ctx(server, "ingest")
    stray = str(team.fau / "notes" / "stranger" / "note-1.json")
    obs = do_write_file({"kind": "write_file", "path": stray,
                         "content": {"from": "ingest", "text": "hi"}}, ctx)
    assert "does not share the store" in obs.get("error", "")
    assert not (team.fau / "notes" / "stranger").exists()
    assert "does not share the store" in do_mkdir(
        {"kind": "mkdir", "path": str(team.fau / "notes" / "stranger"), "parents": True},
        ctx).get("error", "")
    # a note for a routine that DOES share the store lands
    ok = do_write_file({"kind": "write_file",
                        "path": str(team.fau / "notes" / "steward" / "note-1.json"),
                        "content": {"from": "ingest", "text": "staged"}}, ctx)
    assert "error" not in ok
    assert (team.fau / "notes" / "steward" / "note-1.json").is_file()


def test_the_write_gate_judges_the_destination_never_the_removal(server, team):
    """Moving a note INTO a stranger's mailbox is the same mistake; clearing a note already
    stranded there is the repair — delete and a move's source are never refused for it."""
    note(team.fau, sender="ingest", to="stranger", text="stranded")
    stranded = sharedstores.notes_dir(team.fau, "stranger") / "note-20260902-120000-000000.json"
    ctx = _ctx(server, "ingest")
    ctx.seen_paths.add(str(stranded.resolve()))
    moved = do_move({"kind": "move", "src": str(stranded),
                     "dst": str(team.fau / "notes" / "steward" / "note-rescued.json")}, ctx)
    assert "error" not in moved
    assert (team.fau / "notes" / "steward" / "note-rescued.json").is_file()
    back = do_move({"kind": "move",
                    "src": str(team.fau / "notes" / "steward" / "note-rescued.json"),
                    "dst": str(team.fau / "notes" / "loner" / "note-1.json")}, ctx)
    assert "dst: 'loner' does not share the store" in back.get("error", "")
    # an in-place edit is a write too; a stranded note can always be deleted
    note(team.fau, sender="ingest", to="loner", text="old")
    target = sharedstores.notes_dir(team.fau, "loner") / "note-20260902-120000-000000.json"
    ctx.seen_paths.add(str(target.resolve()))
    assert "does not share the store" in do_edit_file(
        {"kind": "edit_file", "path": str(target), "anchor": "old", "replacement": "new"},
        ctx).get("error", "")
    assert do_delete({"kind": "delete", "path": str(target)}, ctx).get("removed") is True


def test_a_run_is_handed_its_notes_once_at_boot(server, team, scripted):
    """Boot drains the notes beside the inbox and hands them to the (pure) digest builder:
    the first run's system prompt carries the note, the file is gone, the next run sees none."""
    from conftest import finish
    from rsched.engine.runtime import run_routine

    note(team.fau, sender="ingest", to="steward", text="staged the batch for you")
    look = {"say": "Reading the recipe.", "kind": "read_file", "path": "main.md"}
    ep = scripted([look, finish(summary="read it"), look, finish(summary="nothing new")])
    run_routine(server.routines_home / "steward", server, run_ts="20260902-120000")
    assert "staged the batch for you" in ep.calls[0]["messages"][0]["content"]
    assert list(sharedstores.notes_dir(team.fau, "steward").glob("note-*.json")) == []
    run_routine(server.routines_home / "steward", server, run_ts="20260902-130000")
    assert "staged the batch for you" not in ep.calls[2]["messages"][0]["content"]
