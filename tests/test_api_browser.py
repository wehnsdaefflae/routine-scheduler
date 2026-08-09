"""The conversation browser-session API (D86 / R262 pt2): session rows derived from the
`browser-session` util's persisted handles, the auth-served view screenshot, and the
server-side stop that signals the recorded process group. Handles are MODEL-WRITTEN state,
so the escape and garbage paths matter as much as the happy path."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched import conversations as conv_mod
from rsched.web.app import create_app

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "library-seed"
TOKEN = "test-token"

# a 1x1 transparent PNG, byte-for-byte
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8ffff3f0300050001a5f645400000000049454e44ae426082")


@pytest.fixture
def server(tmp_path):
    lib = tmp_path / "library"
    shutil.copytree(SEED / "workflows", lib / "workflows")
    shutil.copytree(SEED / "rules", lib / "rules")
    shutil.copytree(SEED / "permissions", lib / "permissions")
    return make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"),
                            libraries_home=str(lib))


@pytest.fixture
def client(server):
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c, server


def _conv(server) -> Path:
    return conv_mod.create_conversation(server, slug="c-brow", first_message="drive a browser")


def _handle(conv_dir: Path, *, name: str = "default", view: str = "state/browser-view.png",
            pid: int = 999_999_999, port: int = 1) -> Path:
    """A handle exactly as `gu browser-session start` persists it."""
    state = conv_dir / "state"
    state.mkdir(exist_ok=True)
    suffix = "" if name == "default" else f"-{name}"
    hf = state / f"browser-session{suffix}.json"
    hf.write_text(json.dumps({
        "cdp": f"http://127.0.0.1:{port}", "host": "127.0.0.1", "port": port,
        "pid": pid, "url": "https://example.com", "name": name,
        "view": view, "started": 1754700000.0}), encoding="utf-8")
    return hf


def test_no_sessions_is_empty(client):
    c, server = client
    _conv(server)
    r = c.get("/api/conversations/c-brow/browser")
    assert r.status_code == 200 and r.json() == []


def test_rows_view_and_liveness(client):
    c, server = client
    d = _conv(server)
    _handle(d)   # port 1: nothing listens there -> alive False
    (d / "state" / "browser-view.png").write_bytes(PNG)
    rows = c.get("/api/conversations/c-brow/browser").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "default" and row["alive"] is False
    assert row["url"] == "https://example.com"
    assert isinstance(row["view"]["mtime"], int)
    v = c.get("/api/conversations/c-brow/browser/view")
    assert v.status_code == 200
    assert v.headers["content-type"] == "image/png"
    assert v.content == PNG


def test_view_absent_404_and_named_handles(client):
    c, server = client
    d = _conv(server)
    _handle(d, name="runA", view="state/browser-view-a.png")
    rows = c.get("/api/conversations/c-brow/browser").json()
    assert [r["name"] for r in rows] == ["runA"]
    assert rows[0]["view"] is None          # recorded but never captured
    assert c.get("/api/conversations/c-brow/browser/view",
                 params={"name": "runA"}).status_code == 404
    assert c.get("/api/conversations/c-brow/browser/view",
                 params={"name": "nope"}).status_code == 404


def test_escaping_view_path_rejected(client):
    """The handle is model-written — a view path outside the conversation dir must never
    be served (the row lists the session, but with no view)."""
    c, server = client
    d = _conv(server)
    _handle(d, view="../../../etc/passwd")
    rows = c.get("/api/conversations/c-brow/browser").json()
    assert rows[0]["view"] is None
    r = c.get("/api/conversations/c-brow/browser/view")
    assert r.status_code == 400


def test_stop_clears_handle_without_live_process(client):
    c, server = client
    d = _conv(server)
    hf = _handle(d, pid=999_999_999)        # no such pid: nothing to kill, handle still cleared
    r = c.post("/api/conversations/c-brow/browser/default/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["stopped"] is True and body["killed_process"] is False
    assert not hf.exists()
    assert c.get("/api/conversations/c-brow/browser").json() == []
    assert c.post("/api/conversations/c-brow/browser/default/stop").status_code == 404


def test_garbage_handle_is_skipped(client):
    c, server = client
    d = _conv(server)
    (d / "state").mkdir(exist_ok=True)
    (d / "state" / "browser-session.json").write_text("{not json", encoding="utf-8")
    assert c.get("/api/conversations/c-brow/browser").json() == []
