"""Approval-free intra-DOMAIN notes (F335).

The design in one line: a NOTE is coordination between teammates, a REPORT is work somebody must
act on and is tracked until answered. These pin the two things that make the light channel safe
enough to be approval-free — a note **cannot leave the domain** and it is **delivered exactly
once** — plus the fact that it leaves none of the `report` ledger's tracked-work trace.

Teammates are a DOMAIN, not a lane: the channel reads the `domain:` key out of each routine's
own routine.yaml — the one place the shared store and the inherited config block come from
too — so its boundary is exactly the boundary the safety argument names and nothing wider. Finding
siblings by what fires together instead would make "who may write to me" a consequence of what
time I run: two routines sharing a permission surface could then reach each other only by also
firing in the same chain.

The reader's half is here too: the state-digest section a drained note renders into, its
backlog cap, and the contract line that names the siblings a run may write to.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from conftest import make_test_server
from rsched import domainnotes, domains


@pytest.fixture
def server(tmp_path):
    return make_test_server(tmp_path)


def _routine(server, slug: str, domain_id: str = "") -> None:
    """A routine on disk that names its domain in its OWN routine.yaml.

    That file is where the at-most-one cardinality lives, so it is the only place the notes
    channel looks — there is no membership list anywhere to disagree with it.
    """
    d = server.routines_home / slug
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"slug": slug, "name": slug, "description": "domain note test routine"}
    if domain_id:
        cfg["domain"] = domain_id
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


@pytest.fixture
def team(server):
    """Three routines in one domain, one in a DIFFERENT domain, and one in none at all."""
    fau = domains.create(server.routines_home, name="FAU", config={})["id"]
    other = domains.create(server.routines_home, name="Other", config={})["id"]
    for slug in ("ingest", "steward", "sender"):
        _routine(server, slug, fau)
    _routine(server, "stranger", other)
    _routine(server, "loner")
    return SimpleNamespace(fau=fau, other=other)


def note(server, domain_id, *, sender: str, to: str, text: str) -> None:
    """Write a note the way a ROUTINE does: an ordinary file into the domain's shared store.
    There is no writer in `domainnotes` to call — deliberately, because the engine exposes no
    note action; a member writes the JSON itself at the path `contract_line` hands it. These
    tests therefore have to write it the same way, or they would be testing a path production
    never takes."""
    d = domains.store_dir(server.routines_home, domain_id) / "notes" / to
    d.mkdir(parents=True, exist_ok=True)
    # zero-padded sequence, so the filename sort `drain` relies on IS write order
    seq = len(list(d.glob("note-*.json")))
    (d / f"note-20260902-120000-{seq:06d}.json").write_text(
        json.dumps({"from": sender, "ts": "2026-09-02T12:00:00+02:00", "text": text}),
        encoding="utf-8")


def test_a_note_reaches_a_sibling_and_is_gone_once_read(server, team):
    note(server, team.fau, sender="ingest", to="steward",
         text="staged 4 new items in ingest-inbox.md")
    got = domainnotes.drain(server.routines_home, "steward")
    assert len(got) == 1
    assert got[0]["from"] == "ingest" and "staged 4 new items" in got[0]["text"]
    # read-and-drop, exactly like inbox/: delivered once, never re-shown every run after
    assert domainnotes.drain(server.routines_home, "steward") == []


def test_a_note_cannot_leave_the_domain(server, team):
    """The whole safety argument, asserted where it actually lives. There is no writer to
    refuse a cross-domain note: `drain` only ever looks in the store of the ONE domain the
    reader's own routine.yaml names; a non-member's fs roots do not contain that store at all —
    so reaching outside the domain is not refused at a gate, it cannot be expressed."""
    note(server, team.fau, sender="ingest", to="steward", text="hi")
    assert domainnotes.drain(server.routines_home, "stranger") == []
    store = domains.store_dir(server.routines_home, team.fau)
    # the stranger is handed its OWN domain's store and nothing else
    assert domains.member_store_roots(server.routines_home, team.other) == [
        domains.store_dir(server.routines_home, team.other)]
    assert store not in domains.member_store_roots(server.routines_home, team.other)


def test_an_oversized_note_is_capped_on_the_way_in(server, team):
    """The cap runs on the READ, because the read is the only half this module owns — a routine
    writes the file itself with nothing validating it, straight into the reader's prompt."""
    note(server, team.fau, sender="ingest", to="steward",
         text="x" * (domainnotes.TEXT_CAP + 500))
    got = domainnotes.drain(server.routines_home, "steward")
    assert len(got[0]["text"]) == domainnotes.TEXT_CAP


def test_notes_land_in_the_domain_store_nobody_else_can_reach(server, team):
    """It lives in the store D67 already injects into every member's fs roots — and only
    theirs. That placement IS the boundary; a new store would have needed a new one."""
    note(server, team.fau, sender="ingest", to="steward", text="x")
    store = domains.store_dir(server.routines_home, team.fau)
    written = list((store / "notes" / "steward").glob("note-*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text())["from"] == "ingest"
    # and the store is exactly what a member's fs roots already carry — membership being the
    # thing each routine declares for itself is what makes those two the same set
    assert domains.members(server.routines_home, team.fau) == ["ingest", "sender", "steward"]
    assert store in domains.member_store_roots(server.routines_home, team.fau)
    assert "stranger" not in domains.members(server.routines_home, team.fau)


def test_a_note_is_not_a_report_and_leaves_no_trace_to_close(server, team):
    """No ledger row, no Messages item, no inbox file — the tracked-work-item shape is exactly
    what this channel exists to avoid."""
    note(server, team.fau, sender="ingest", to="steward", text="x")
    assert not (server.routines_home / ".control" / "reports.jsonl").exists()
    assert not (server.routines_home / "steward" / "inbox").exists()
    domainnotes.drain(server.routines_home, "steward")
    assert not (server.routines_home / ".control" / "reports.jsonl").exists()


def test_digest_section_renders_the_notes_and_drains_them(server, team):
    assert domainnotes.digest_section(server.routines_home, "steward") == ""
    note(server, team.fau, sender="ingest", to="steward", text="alpha")
    note(server, team.fau, sender="sender", to="steward", text="beta")
    text = domainnotes.digest_section(server.routines_home, "steward")
    assert "NOTES FROM YOUR DOMAIN" in text
    assert "from ingest" in text and "alpha" in text
    assert "from sender" in text and "beta" in text
    # the run is told what KIND of thing it is reading — nobody is waiting on a reply
    assert "nobody is waiting on a reply" in text
    assert domainnotes.digest_section(server.routines_home, "steward") == ""   # drained


def test_digest_caps_a_backlog_and_says_it_dropped_some(server, team):
    """A nudge, not a mailbox: past the cap the run is being handed a backlog it will not read;
    silence about the drop would read as 'that was everything'."""
    for i in range(domainnotes.MAX_NOTES_SHOWN + 3):
        note(server, team.fau, sender="ingest", to="steward", text=f"note {i}")
    text = domainnotes.digest_section(server.routines_home, "steward")
    assert text.count("- from ingest") == domainnotes.MAX_NOTES_SHOWN
    assert "3 older note(s) were dropped unread" in text
    assert domainnotes.digest_section(server.routines_home, "steward") == ""   # all drained


def test_contract_line_names_the_actual_siblings(server, team):
    """"Write to a member" is not actionable without their slugs; a channel a run does not know
    about is a channel that does not exist."""
    line = domainnotes.contract_line(server.routines_home, "steward")
    assert "ingest" in line and "sender" in line
    siblings = line.split("Routines you share a domain with:")[1]
    assert "steward" not in siblings                     # never itself
    assert "stranger" not in siblings                    # and never another domain's member
    assert "report" in line          # and when to use the heavy channel instead
    # a routine in NO domain is told nothing — there is no sibling to write to
    assert domainnotes.contract_line(server.routines_home, "loner") == ""


def test_leaving_a_domain_cuts_the_channel_both_ways(server, team):
    """Membership is read live from the routine's own file, not captured — a routine that drops
    its `domain:` key stops being told the channel exists and stops being reached by it. Both
    halves matter — the contract line is how a run learns it has teammates at all; `drain` is
    what the store's placement enforces. The note already written for it stays in the domain's
    store, unreachable — leaving is not a delivery."""
    note(server, team.fau, sender="ingest", to="sender", text="for you")
    _routine(server, "sender")                           # rewritten with no domain
    assert domainnotes.contract_line(server.routines_home, "sender") == ""
    assert domainnotes.drain(server.routines_home, "sender") == []
    assert domains.members(server.routines_home, team.fau) == ["ingest", "steward"]
    assert (domains.store_dir(server.routines_home, team.fau) / "notes" / "sender").is_dir()


def test_a_corrupt_note_file_is_skipped_not_fatal(server, team):
    """Boot must never die on a junk file in a shared dir several routines write to."""
    note(server, team.fau, sender="ingest", to="steward", text="good")
    d = domainnotes.notes_dir(domains.store_dir(server.routines_home, team.fau), "steward")
    (d / "note-broken.json").write_text("{not json", encoding="utf-8")
    got = domainnotes.drain(server.routines_home, "steward")
    assert [n["text"] for n in got] == ["good"]
    assert not list(d.glob("note-*.json"))       # the junk is cleared too, not left to recur
