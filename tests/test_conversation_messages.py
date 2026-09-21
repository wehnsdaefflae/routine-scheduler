"""A queued message in a CONVERSATION can be seen, revised and withdrawn (D139).

The operator's ask: "when i inject a message into a conversation or routine run, i wanna see
that message and be able to delete / revise it until it is consumed by the model."

Half of it already existed. A ROUTINE's inbox has the full surface — list, edit in place
(same file, so queue position holds), withdraw — each valid right up until a run drains the
file. A CONVERSATION had only POST: no list, no edit, no withdraw, and the chat's optimistic
echo bubble carries no id, so there was nothing to address even if the verbs had existed.

The window is not small. A message to an idle conversation waits for the wake; mid-run it
waits for the next turn boundary — routinely minutes. The only remedy for a typo was a second
message correcting the first, which costs the model a turn reading both.

The principle is stated in the routine surface's own docstring and is surface-independent:
"the inbox file is the delivery vehicle, and what a routine's next run gets told is the user's
call right up until a run drains it". Consumed stays immutable — the transcript owns it then.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, make_test_server


@pytest.fixture
def api_client(tmp_path):
    """A hermetic console whose CONVERSATIONS home is under tmp_path — the default is
    ~/conversations, which no test may reach."""
    from rsched.web.app import create_app

    server = make_test_server(tmp_path,
                              conversations_home=str(tmp_path / "conversations"))
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c, tmp_path


def _conversation(tmp_path, slug: str = "convo"):
    """A conversation dir the registry will resolve: routine.yaml with kind=conversation."""
    import yaml

    d = tmp_path / "conversations" / slug
    (d / "inbox").mkdir(parents=True)
    (d / "state").mkdir()
    (d / "runs").mkdir()
    cfg = {"name": f"Conv {slug}", "slug": slug, "enabled": True, "kind": "conversation",
           "description": "a test conversation.",
           "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
           "workflow": {"library_slug": "converse", "library_commit": "abc123"}}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (d / "main.md").write_text("# converse\n", encoding="utf-8")
    return d


def _queue(conv_dir, text: str, *, via: str = "conversation") -> str:
    from rsched.engine import inbox

    return inbox.file_message(conv_dir, text, via=via).stem


# ---- see -------------------------------------------------------------------------------


def test_the_queued_messages_of_a_conversation_can_be_listed(api_client, tmp_path):
    c, _ = api_client
    d = _conversation(tmp_path)
    _queue(d, "check the seat map again")

    r = c.get("/api/conversations/convo/messages")
    assert r.status_code == 200, r.text
    queued = r.json()["queued"]
    assert [m["text"] for m in queued] == ["check the seat map again"]
    assert queued[0]["id"], "a message with no id cannot be revised or withdrawn"


def test_a_consumed_message_is_not_listed_as_queued(api_client, tmp_path):
    """Gone from the inbox = consumed = immutable. The transcript owns it from then on."""
    c, _ = api_client
    d = _conversation(tmp_path)
    mid = _queue(d, "already read")
    (d / "inbox" / f"{mid}.json").unlink()

    assert c.get("/api/conversations/convo/messages").json()["queued"] == []


# ---- revise ----------------------------------------------------------------------------


def test_revising_keeps_the_same_file_so_the_queue_position_holds(api_client, tmp_path):
    c, _ = api_client
    d = _conversation(tmp_path)
    first = _queue(d, "one")
    mid = _queue(d, "teh seat map")
    _queue(d, "three")

    r = c.put(f"/api/conversations/convo/messages/{mid}", json={"text": "the seat map"})
    assert r.status_code == 200, r.text

    path = d / "inbox" / f"{mid}.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["text"] == "the seat map"
    assert rec["edited"], "an edit is stamped, so the run can tell it was revised"
    assert len(list((d / "inbox").glob("msg-*.json"))) == 3, "no second file"
    assert (d / "inbox" / f"{first}.json").exists(), "siblings untouched"


def test_revising_a_consumed_message_is_refused_and_says_why(api_client, tmp_path):
    c, _ = api_client
    d = _conversation(tmp_path)
    mid = _queue(d, "gone")
    (d / "inbox" / f"{mid}.json").unlink()

    r = c.put(f"/api/conversations/convo/messages/{mid}", json={"text": "too late"})
    assert r.status_code == 404
    assert "no longer queued" in r.json()["detail"]


# ---- withdraw --------------------------------------------------------------------------


def test_withdrawing_removes_the_delivery(api_client, tmp_path):
    c, _ = api_client
    d = _conversation(tmp_path)
    mid = _queue(d, "never mind")

    assert c.delete(f"/api/conversations/convo/messages/{mid}").status_code == 200
    assert not (d / "inbox" / f"{mid}.json").exists()
    assert c.get("/api/conversations/convo/messages").json()["queued"] == []


# ---- the id the echo bubble needs ------------------------------------------------------


def test_a_message_that_only_queues_still_cannot_be_addressed_without_an_id(api_client,
                                                                           tmp_path):
    """The chat echo had no id, so even a full verb set would have had nothing to address.
    Whatever a queued message's delivery, the caller is told which message it now holds."""
    c, _ = api_client
    d = _conversation(tmp_path)
    mid = _queue(d, "addressable")

    listed = c.get("/api/conversations/convo/messages").json()["queued"]
    assert listed[0]["id"] == mid
