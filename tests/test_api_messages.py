"""The per-routine MESSAGES write surface (D74, docs/messages.md): inbox create/edit/delete
plus outbox retraction — the four-folder GET itself is covered in test_api.py."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched import reports
from rsched.paths import read_json
from rsched.web.app import create_app

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="apir")
    make_routine(slug="peer")
    server = make_test_server(tmp_path)
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c, tmp_path


def test_inbox_message_create_edit_delete(client):
    """The inbox is the user's queue: create lands a msg-* file the next run drains, edit
    rewrites the SAME file (queue position holds, ts kept, edited stamped), delete
    withdraws it. Empty text is rejected on both writes."""
    c, tmp = client
    inbox = tmp / "routines" / "apir" / "inbox"

    r = c.post("/api/routines/apir/messages", json={"text": "check the portals\r\n"})
    assert r.status_code == 200
    mid = r.json()["id"]
    assert mid.startswith("msg-") and r.json()["delivery"] == "next-run"
    rec = read_json(inbox / f"{mid}.json")
    assert rec["text"] == "check the portals" and rec["source"] == "web-routine-queue"
    assert c.post("/api/routines/apir/messages", json={"text": "  "}).status_code == 400
    assert c.post("/api/routines/nope/messages", json={"text": "x"}).status_code == 404

    ts0 = rec["ts"]
    assert c.put(f"/api/routines/apir/messages/{mid}",
                 json={"text": "check the portals FIRST"}).status_code == 200
    rec = read_json(inbox / f"{mid}.json")
    assert rec["text"] == "check the portals FIRST"
    assert rec["ts"] == ts0 and rec["edited"]           # same file, edit stamped
    assert len(list(inbox.glob("msg-*.json"))) == 1
    assert c.put(f"/api/routines/apir/messages/{mid}", json={"text": ""}).status_code == 400

    assert c.delete(f"/api/routines/apir/messages/{mid}").status_code == 200
    assert list(inbox.glob("msg-*.json")) == []
    # gone = consumed = immutable
    assert c.put(f"/api/routines/apir/messages/{mid}", json={"text": "x"}).status_code == 404
    assert c.delete(f"/api/routines/apir/messages/{mid}").status_code == 404


def test_inbox_edit_keeps_engine_keys_and_drops_feedback_fields(client):
    """An edit rewrites `text` only: engine keys (`report`/`from` — delivery stamping
    matches on them) survive, while structured reviewer-feedback fields (they describe the
    OLD text) are dropped. `answer-*` files are unreachable by id pattern."""
    c, tmp = client
    inbox = tmp / "routines" / "apir" / "inbox"
    (inbox / "msg-rep-R9.json").write_text(json.dumps(
        {"text": "REPORT R9 from routine `peer`", "ts": "t", "via": "report",
         "report": "R9", "from": "peer", "kind": "comment", "raw": "old"}), encoding="utf-8")

    assert c.put("/api/routines/apir/messages/msg-rep-R9",
                 json={"text": "amended delivery text"}).status_code == 200
    rec = read_json(inbox / "msg-rep-R9.json")
    assert rec["report"] == "R9" and rec["from"] == "peer" and rec["via"] == "report"
    assert "kind" not in rec and "raw" not in rec

    (inbox / "answer-q1.json").write_text('{"text": "yes"}', encoding="utf-8")
    assert c.delete("/api/routines/apir/messages/answer-q1").status_code == 404
    assert (inbox / "answer-q1.json").exists()


def test_outbox_retract(client):
    """The outbox's ONE write: retracting a not-yet-consumed addressed report unlinks the
    delivery from the target's inbox and appends a `retracted` ledger event — the row
    leaves the outbox, reads `dropped` on the Messages page, and a consumed or foreign
    report refuses."""
    c, tmp = client
    home = tmp / "routines"
    (home / "self-audit").mkdir()      # /api/items keys "exists" off this dir
    peer = home / "peer"
    _, rid = reports.file_report(home, routine="apir", run_id="apir:1", title="hand-off",
                                 detail="d", target="peer", target_dir=peer)
    _, rid2 = reports.file_report(home, routine="apir", run_id="apir:1", title="landed",
                                  detail="d", target="peer", target_dir=peer)
    reports.stamp_delivered(home, [{"report": rid2}], run_id="peer:2")

    assert [m["report"] for m in c.get("/api/routines/apir/messages").json()["outbox"]] == [rid]
    r = c.delete(f"/api/routines/apir/outbox/{rid}")
    assert r.status_code == 200
    assert not (peer / "inbox" / f"msg-rep-{rid}.json").exists()
    data = c.get("/api/routines/apir/messages").json()
    assert data["outbox"] == []                        # retracted: neither waiting nor consumed
    assert [m["report"] for m in data["received"]] == [rid2]
    assert next(i for i in c.get("/api/items").json()["items"]
                if i["id"] == rid)["status"] == "dropped"

    assert c.delete(f"/api/routines/apir/outbox/{rid}").status_code == 409   # already retracted
    assert c.delete(f"/api/routines/apir/outbox/{rid2}").status_code == 409  # already picked up
    assert c.delete(f"/api/routines/peer/outbox/{rid2}").status_code == 404  # not the filer
    assert c.delete("/api/routines/apir/outbox/R99").status_code == 404

