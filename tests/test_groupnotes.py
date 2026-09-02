"""Approval-free intra-group notes (F335).

The design in one line: a NOTE is coordination between teammates, a REPORT is work somebody must
act on and is tracked until answered. These pin the two things that make the light channel safe
enough to be approval-free — a note **cannot leave the group**, and it is **delivered exactly
once** — plus the fact that `report` is untouched.
"""

from __future__ import annotations

import json

import pytest

from conftest import make_test_server
from rsched import groupnotes, groups


@pytest.fixture
def server(tmp_path):
    return make_test_server(tmp_path)


@pytest.fixture
def team(server):
    """One group of three, and one routine outside it."""
    g = groups.create(server.routines_home, name="FAU",
                      members=[{"slug": "ingest"}, {"slug": "steward"}, {"slug": "sender"}])
    groups.create(server.routines_home, name="Other", members=[{"slug": "stranger"}])
    return g["id"]


def note(server, gid, *, sender: str, to: str, text: str) -> None:
    """Write a note the way a ROUTINE does: an ordinary file into the group's shared store.
    There is no writer in `groupnotes` to call — deliberately, because the engine exposes no
    note action; a member writes the JSON itself at the path `contract_line` hands it. These
    tests therefore have to write it the same way, or they would be testing a path production
    never takes."""
    d = groups.store_dir(server.routines_home, gid) / "notes" / to
    d.mkdir(parents=True, exist_ok=True)
    (d / f"note-20260902-120000-{abs(hash((sender, text))) % 10**6:06d}.json").write_text(
        json.dumps({"from": sender, "ts": "2026-09-02T12:00:00+02:00", "text": text}),
        encoding="utf-8")


def test_a_note_reaches_a_sibling_and_is_gone_once_read(server, team):
    note(server, team, sender="ingest", to="steward",
         text="staged 4 new items in ingest-inbox.md")
    got = groupnotes.drain(server.routines_home, "steward")
    assert len(got) == 1
    assert got[0]["from"] == "ingest" and "staged 4 new items" in got[0]["text"]
    # read-and-drop, exactly like inbox/: delivered once, never re-shown every run after
    assert groupnotes.drain(server.routines_home, "steward") == []


def test_a_note_cannot_leave_the_group(server, team):
    """The whole safety argument, asserted where it actually lives. There is no writer to
    refuse a cross-group note: `drain` only ever looks in the stores of groups the reader is a
    member of, and a non-member's fs roots do not contain the store at all — so reaching
    outside the group is not refused at a gate, it cannot be expressed."""
    note(server, team, sender="ingest", to="steward", text="hi")
    assert groupnotes.drain(server.routines_home, "stranger") == []
    store = groups.store_dir(server.routines_home, team)
    assert store not in groups.member_store_roots(server.routines_home, "stranger")


def test_an_oversized_note_is_capped_on_the_way_in(server, team):
    """The cap runs on the READ, because the read is the only half this module owns — a routine
    writes the file itself, unvalidated, and the text goes straight into the reader's prompt."""
    note(server, team, sender="ingest", to="steward", text="x" * (groupnotes.TEXT_CAP + 500))
    got = groupnotes.drain(server.routines_home, "steward")
    assert len(got[0]["text"]) == groupnotes.TEXT_CAP


def test_notes_land_in_the_group_store_nobody_else_can_reach(server, team):
    """It lives in the store D67 already injects into every member's fs roots — and only
    theirs. That placement IS the boundary; a new store would have needed a new one."""
    note(server, team, sender="ingest", to="steward", text="x")
    store = groups.store_dir(server.routines_home, team)
    written = list((store / "notes" / "steward").glob("note-*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text())["from"] == "ingest"
    # and the store is exactly what a member's fs roots already carry
    assert store in groups.member_store_roots(server.routines_home, "steward")
    assert store not in groups.member_store_roots(server.routines_home, "stranger")


def test_a_note_is_not_a_report_and_leaves_no_trace_to_close(server, team):
    """No ledger row, no Messages item, no inbox file — the tracked-work-item shape is exactly
    what this channel exists to avoid."""
    note(server, team, sender="ingest", to="steward", text="x")
    assert not (server.routines_home / ".control" / "reports.jsonl").exists()
    assert not (server.routines_home / "steward" / "inbox").exists()
    groupnotes.drain(server.routines_home, "steward")
    assert not (server.routines_home / ".control" / "reports.jsonl").exists()


def test_digest_section_renders_the_notes_and_drains_them(server, team):
    assert groupnotes.digest_section(server.routines_home, "steward") == ""
    note(server, team, sender="ingest", to="steward", text="alpha")
    note(server, team, sender="sender", to="steward", text="beta")
    text = groupnotes.digest_section(server.routines_home, "steward")
    assert "NOTES FROM YOUR GROUP" in text
    assert "from ingest" in text and "alpha" in text
    assert "from sender" in text and "beta" in text
    # the run is told what KIND of thing it is reading — nobody is waiting on a reply
    assert "nobody is waiting on a reply" in text
    assert groupnotes.digest_section(server.routines_home, "steward") == ""   # drained


def test_digest_caps_a_backlog_and_says_it_dropped_some(server, team):
    """A nudge, not a mailbox: past the cap the run is being handed a backlog it will not read,
    and silence about the drop would read as 'that was everything'."""
    for i in range(groupnotes.MAX_NOTES_SHOWN + 3):
        note(server, team, sender="ingest", to="steward", text=f"note {i}")
    text = groupnotes.digest_section(server.routines_home, "steward")
    assert text.count("- from ingest") == groupnotes.MAX_NOTES_SHOWN
    assert "3 older note(s) were dropped unread" in text
    assert groupnotes.digest_section(server.routines_home, "steward") == ""   # all drained


def test_contract_line_names_the_actual_siblings(server, team):
    """"Write to a member" is not actionable without their slugs, and a channel a run does not
    know about is a channel that does not exist."""
    line = groupnotes.contract_line(server.routines_home, "steward")
    assert "ingest" in line and "sender" in line
    assert "steward" not in line.split("Your group members:")[1]   # never itself
    assert "report" in line          # and when to use the heavy channel instead
    # an UNGROUPED routine is told nothing — there is no sibling to write to
    assert groupnotes.contract_line(server.routines_home, "loner") == ""


def test_leaving_a_group_cuts_the_channel_both_ways(server, team):
    """Membership is read live, not captured — a removed member stops being told the channel
    exists, and stops being reached by it. Both halves matter: the contract line is how a run
    learns it has teammates at all, and `drain` is what the store's placement enforces."""
    note(server, team, sender="ingest", to="sender", text="for you")
    groups.update(server.routines_home, team,
                  members=[{"slug": "ingest"}, {"slug": "steward"}])
    assert groupnotes.contract_line(server.routines_home, "sender") == ""
    assert groupnotes.drain(server.routines_home, "sender") == []


def test_a_corrupt_note_file_is_skipped_not_fatal(server, team):
    """Boot must never die on a junk file in a shared dir several routines write to."""
    note(server, team, sender="ingest", to="steward", text="good")
    d = groupnotes.notes_dir(groups.store_dir(server.routines_home, team), "steward")
    (d / "note-broken.json").write_text("{not json", encoding="utf-8")
    got = groupnotes.drain(server.routines_home, "steward")
    assert [n["text"] for n in got] == ["good"]
    assert not list(d.glob("note-*.json"))       # the junk is cleared too, not left to recur
