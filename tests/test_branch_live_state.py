"""D306 point 2's server half: what a branch IS RIGHT NOW, so the rail can show it. The operator
kept conversation branches and diagnosed their non-use as the console's fault — *"unused only
because the UI is bad"* — and this point names the gap: branches were "a lineage LINE in the
header: names joined by commas, no state, no way to tell a branch working from one long finished".
So every lineage row now carries its state, its last update and whether it has HANDED BACK, the
parent's row included; the hand-back fact is READ from the inbox and the run archive rather than
stored, because a flag written into a config would be a third copy of what the filesystem already
says and the one most likely to drift.
"""

from __future__ import annotations

import json

import pytest

from conftest import authed_client, seeded_server
from rsched import branches
from rsched import conversations as conv_mod
from rsched.paths import atomic_write_json
from rsched.web import branch_lineage


@pytest.fixture
def server(tmp_path):
    return seeded_server(tmp_path)


@pytest.fixture
def client(server):
    with authed_client(server) as c:
        yield c


def _conv(tmp_path, slug: str, *, name: str = "", parent: dict | None = None) -> None:
    """One conversation directory, the shape `registry.scan` catalogs."""
    d = tmp_path / "conversations" / slug
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"slug": slug, "name": name or slug, "workflow": "converse"}
    if parent:
        cfg["parent"] = parent
    (d / "routine.yaml").write_text(json.dumps(cfg), encoding="utf-8")


def _handback(tmp_path, parent: str, branch: str, *, consumed_run: str = "") -> None:
    """A hand-back message where one really lands: queued in the parent's inbox, or archived
    under one of its runs once a reply consumed it."""
    base = tmp_path / "conversations" / parent
    where = (base / "runs" / consumed_run / "consumed") if consumed_run else (base / "inbox")
    where.mkdir(parents=True, exist_ok=True)
    atomic_write_json(where / f"branch-{branch}-20261124-150000.json",
                      {"text": "what the branch concluded", "via": "branch"})


def test_a_queued_hand_back_is_read_as_handed_back(tmp_path):
    """The message sits in the parent's inbox until its next reply consumes it, so a branch that
    handed back a minute ago must already read as handed back — otherwise the rail says "running"
    about a branch that is finished and waiting."""
    _conv(tmp_path, "parent")
    _handback(tmp_path, "parent", "kid")
    assert branch_lineage.handed_back(tmp_path / "conversations" / "parent", "kid") is True


def test_a_consumed_hand_back_is_still_read_as_handed_back(tmp_path):
    """And once the parent's reply HAS consumed it, the message has moved to that run's
    `consumed/` — the same fact, in the other of its two homes. Reading only the inbox would make
    "handed back" a flag that disappears the moment the parent answers."""
    _conv(tmp_path, "parent")
    _handback(tmp_path, "parent", "kid", consumed_run="20261124-150500")
    assert branch_lineage.handed_back(tmp_path / "conversations" / "parent", "kid") is True


def test_another_vias_message_is_not_a_hand_back(tmp_path):
    """The parent's inbox carries everybody's freight — a user injection, a detached task's
    delivery, another routine's report. Matching on the name alone would make any of them read as
    a branch handing back, so the `via` is what decides."""
    _conv(tmp_path, "parent")
    inbox = tmp_path / "conversations" / "parent" / "inbox"
    inbox.mkdir(parents=True)
    atomic_write_json(inbox / "branch-kid-20261124-150000.json",
                      {"text": "not a hand-back", "via": "background"})
    assert branch_lineage.handed_back(tmp_path / "conversations" / "parent", "kid") is False


def test_a_branch_with_no_hand_back_reads_false_and_raises_nothing(tmp_path):
    """The ordinary case, and it must not raise on a conversation with no inbox and no runs at
    all: this is a READ for a rail section, and a page must not fail over it."""
    _conv(tmp_path, "parent")
    assert branch_lineage.handed_back(tmp_path / "conversations" / "parent", "kid") is False


def test_an_unreadable_message_is_skipped_rather_than_raised(tmp_path):
    """A half-written or hand-edited file in that directory must not take the page down — the
    filing is an atomic rename, but an operator can put anything there."""
    _conv(tmp_path, "parent")
    inbox = tmp_path / "conversations" / "parent" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "branch-kid-20261124-150000.json").write_text("{not json", encoding="utf-8")
    assert branch_lineage.handed_back(tmp_path / "conversations" / "parent", "kid") is False


def test_live_fields_come_from_the_catalog_the_page_already_has():
    """No filesystem read of its own: the catalog is memoized and single-flighted, and point 5 of
    this same order had just made the lineage read CONDITIONAL — a section that walked the homes
    again would undo that. A slug the catalog does not carry (a deleted branch) reads as `new`."""
    rows = {"kid": {"state": "running", "updated": "20261124-150000"}}
    assert branch_lineage.live_fields(rows, "kid") == {"state": "running",
                                                       "updated": "20261124-150000"}
    assert branch_lineage.live_fields(rows, "gone") == {"state": "new", "updated": ""}


def test_a_branch_row_adds_the_live_fields_without_losing_the_structural_ones(tmp_path):
    """The route's answer GAINS keys and loses none: a reader of the old shape — the header's own
    lineage line, which has rendered `turn` and `name` since F325 — keeps working unchanged."""
    _conv(tmp_path, "parent")
    _handback(tmp_path, "parent", "kid")
    row = branch_lineage.branch_row(
        {"slug": "kid", "name": "Kid", "turn": 3, "forked": "20261124-140000"},
        tmp_path / "conversations" / "parent",
        {"kid": {"state": "finished", "updated": "20261124-150000"}})
    assert row == {"slug": "kid", "name": "Kid", "turn": 3, "forked": "20261124-140000",
                   "state": "finished", "updated": "20261124-150000", "handed_back": True}


def test_the_lineage_route_carries_state_for_the_parent_and_every_branch(server, client):
    """END TO END on the route the rail reads, over a REAL fork rather than hand-written config:
    both directions carry the live fields, because a branch needs to offer the way back as
    readily as the way down — and a parent that is still working is exactly as worth knowing
    about as a branch that is."""
    parent_dir = conv_mod.create_conversation(server, slug="c-root", first_message="do it")
    run = parent_dir / "runs" / "20261124-140000"
    run.mkdir(parents=True)
    (run / "transcript.jsonl").write_text(
        json.dumps({"type": "header", "run_id": "c-root:20261124-140000",
                    "routine": "c-root", "depth": 0}) + "\n"
        + json.dumps({"type": "assistant_action", "turn": 1,
                      "payload": {"kind": "read_file", "say": "s"}}) + "\n"
        + json.dumps({"type": "observation", "turn": 1,
                      "payload": {"kind": "read_file", "content": "x"}}) + "\n",
        encoding="utf-8")
    atomic_write_json(run / "status.json", {"state": "finished", "turn": 1})
    slug = branches.fork_conversation(server, parent_dir=parent_dir, parent_slug="c-root",
                                      at_turn=1)["slug"]
    _handback(server.conversations_home.parent, "c-root", slug)

    down = client.get("/api/conversations/c-root/lineage")
    assert down.status_code == 200, down.text
    kids = down.json()["branches"]
    assert [b["slug"] for b in kids] == [slug]
    assert kids[0]["turn"] == 1
    assert kids[0]["handed_back"] is True
    assert "state" in kids[0] and "updated" in kids[0]

    up = client.get(f"/api/conversations/{slug}/lineage")
    assert up.status_code == 200, up.text
    parent = up.json()["parent"]
    assert parent["slug"] == "c-root"
    assert parent["exists"] is True
    # The VALUE, not merely the key. A route answering a constant `new` for every relative would
    # satisfy a presence check while telling the reader nothing — and "no way to tell a branch
    # working from one long finished" is the exact gap this point exists to close. `c-root` has
    # one finished run, so its row must say so.
    assert parent["state"] == "finished", (
        f"the parent's row does not carry its real state: {parent!r}")
    assert parent["updated"], "the parent's row carries no stamp, so 'when' cannot be shown"


def test_a_conversation_with_no_family_answers_empty_without_failing(server, client):
    """The overwhelming majority. The route still answers — the console asks only when its own
    list rows say there IS a family (point 5), and an API caller may always ask."""
    conv_mod.create_conversation(server, slug="c-lonely", first_message="alone")
    got = client.get("/api/conversations/c-lonely/lineage")
    assert got.status_code == 200, got.text
    assert got.json() == {"parent": None, "branches": []}
