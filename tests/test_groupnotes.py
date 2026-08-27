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


def test_a_note_reaches_a_sibling_and_is_gone_once_read(server, team):
    groupnotes.write_note(server.routines_home, sender="ingest", to="steward",
                          text="staged 4 new items in ingest-inbox.md")
    got = groupnotes.drain(server.routines_home, "steward")
    assert len(got) == 1
    assert got[0]["from"] == "ingest" and "staged 4 new items" in got[0]["text"]
    # read-and-drop, exactly like inbox/: delivered once, never re-shown every run after
    assert groupnotes.drain(server.routines_home, "steward") == []


def test_a_note_cannot_leave_the_group(server, team):
    """The whole safety argument. Reaching outside the group is not something this channel
    refuses at a gate — it is something it cannot express."""
    with pytest.raises(ValueError, match="share no group"):
        groupnotes.write_note(server.routines_home, sender="ingest", to="stranger", text="hi")
    with pytest.raises(ValueError, match="share no group"):
        groupnotes.write_note(server.routines_home, sender="stranger", to="ingest", text="hi")
    assert groupnotes.drain(server.routines_home, "stranger") == []


def test_a_note_to_yourself_is_refused(server, team):
    with pytest.raises(ValueError, match="SIBLING"):
        groupnotes.write_note(server.routines_home, sender="ingest", to="ingest", text="hi")


def test_an_empty_note_is_refused(server, team):
    with pytest.raises(ValueError, match="needs text"):
        groupnotes.write_note(server.routines_home, sender="ingest", to="steward", text="   ")


def test_notes_land_in_the_group_store_nobody_else_can_reach(server, team):
    """It lives in the store D67 already injects into every member's fs roots — and only
    theirs. That placement IS the boundary; a new store would have needed a new one."""
    groupnotes.write_note(server.routines_home, sender="ingest", to="steward", text="x")
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
    groupnotes.write_note(server.routines_home, sender="ingest", to="steward", text="x")
    assert not (server.routines_home / ".control" / "reports.jsonl").exists()
    assert not (server.routines_home / "steward" / "inbox").exists()
    groupnotes.drain(server.routines_home, "steward")
    assert not (server.routines_home / ".control" / "reports.jsonl").exists()


def test_digest_section_renders_the_notes_and_drains_them(server, team):
    assert groupnotes.digest_section(server.routines_home, "steward") == ""
    groupnotes.write_note(server.routines_home, sender="ingest", to="steward", text="alpha")
    groupnotes.write_note(server.routines_home, sender="sender", to="steward", text="beta")
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
        groupnotes.write_note(server.routines_home, sender="ingest", to="steward",
                              text=f"note {i}")
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
    """Membership is read live, not captured — a member removed from the group can no longer
    write in, and notes are not re-routed to it."""
    groups.update(server.routines_home, team,
                  members=[{"slug": "ingest"}, {"slug": "steward"}])
    with pytest.raises(ValueError, match="share no group"):
        groupnotes.write_note(server.routines_home, sender="sender", to="steward", text="x")
    assert groupnotes.contract_line(server.routines_home, "sender") == ""


def test_a_corrupt_note_file_is_skipped_not_fatal(server, team):
    """Boot must never die on a junk file in a shared dir several routines write to."""
    groupnotes.write_note(server.routines_home, sender="ingest", to="steward", text="good")
    d = groupnotes.notes_dir(groups.store_dir(server.routines_home, team), "steward")
    (d / "note-broken.json").write_text("{not json", encoding="utf-8")
    got = groupnotes.drain(server.routines_home, "steward")
    assert [n["text"] for n in got] == ["good"]
    assert not list(d.glob("note-*.json"))       # the junk is cleared too, not left to recur
