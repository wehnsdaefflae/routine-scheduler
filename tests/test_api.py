"""Web API: auth, routine CRUD + 409 guard, runs/transcripts, questions, settings."""

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from rsched.config import load_server_config
from rsched.paths import atomic_write_json, read_json
from rsched.web.app import create_app

TOKEN = "test-token"


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="apir")  # lives under tmp_path/routines via the shared fixture
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "library_home": str(tmp_path / "library"),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "default_roles": {"orchestrator": {"endpoint": "dummy", "model": "m"}},
    }))
    server, problems = load_server_config(cfg_path)
    assert not problems
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c, tmp_path


def _mk_run(routines, slug, ts, state, question=None):
    run_dir = routines / slug / "runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"{slug}:{ts}", "state": state, "pid": 4242, "turn": 2,
                       "usage": {"in": 10, "out": 4}, "question": question})
    with open(run_dir / "transcript.jsonl", "w") as fh:
        fh.write(json.dumps({"type": "header", "run_id": f"{slug}:{ts}"}) + "\n")
        fh.write(json.dumps({"ts": "t", "type": "assistant_action", "turn": 1,
                             "payload": {"say": "s", "kind": "shell", "command": "gu list"}}) + "\n")
    return run_dir


def test_auth_required(client):
    c, _ = client
    bare = TestClient(c.app)
    assert bare.get("/api/routines").status_code == 401
    assert bare.get(f"/api/routines?token={TOKEN}").status_code == 200
    assert c.get("/api/status").status_code == 200


def test_routine_cards_and_detail(client):
    c, tmp = client
    _mk_run(tmp / "routines", "apir", "20260707-070000", "finished")
    cards = c.get("/api/routines").json()
    assert len(cards) == 1 and cards[0]["slug"] == "apir" and cards[0]["cron"] == "0 7 * * 1"
    detail = c.get("/api/routines/apir").json()
    assert "Test instruction" in detail["instruction"]
    assert "## Run flow" in detail["workflow"]
    assert detail["runs"][0]["state"] == "finished"
    assert c.get("/api/routines/nope").status_code == 404


def test_patch_routine_and_409_guard(client):
    c, tmp = client
    r = c.patch("/api/routines/apir", json={"enabled": False, "schedule": {"cron": "0 9 * * 2"}})
    assert r.status_code == 200
    raw = yaml.safe_load((tmp / "routines" / "apir" / "routine.yaml").read_text())
    assert raw["enabled"] is False and raw["schedule"]["cron"] == "0 9 * * 2"
    assert raw["schedule"]["tz"] == "Europe/Berlin"  # merged, not replaced
    _mk_run(tmp / "routines", "apir", "20260708-090000", "running")
    assert c.patch("/api/routines/apir", json={"enabled": True}).status_code == 409
    assert c.put("/api/routines/apir/instruction", json={"content": "x"}).status_code == 409


def test_put_docs(client):
    c, tmp = client
    r = c.put("/api/routines/apir/instruction", json={"content": "# New instruction"})
    assert r.status_code == 200
    assert (tmp / "routines" / "apir" / "instruction.md").read_text() == "# New instruction"


def test_file_read_guarded(client):
    c, _ = client
    assert c.get("/api/routines/apir/files", params={"path": "LEDGER.md"}).status_code == 200
    assert c.get("/api/routines/apir/files",
                 params={"path": "../../../etc/passwd"}).status_code == 404


def test_runs_and_transcript(client):
    c, tmp = client
    _mk_run(tmp / "routines", "apir", "20260707-070000", "finished")
    runs = c.get("/api/runs", params={"routine": "apir"}).json()
    assert runs[0]["run_id"] == "apir:20260707-070000"
    tr = c.get("/api/runs/apir:20260707-070000/transcript").json()
    assert [e["type"] for e in tr["events"]] == ["header", "assistant_action"]
    tr2 = c.get("/api/runs/apir:20260707-070000/transcript",
                params={"offset": tr["offset"]}).json()
    assert tr2["events"] == []
    assert c.get("/api/runs/apir:20990101-000000/transcript").status_code == 404
    assert c.get("/api/runs/garbage/transcript").status_code == 400


def test_intervention_endpoints(client):
    c, tmp = client
    run_dir = _mk_run(tmp / "routines", "apir", "20260708-100000", "running")
    rid = "apir:20260708-100000"
    r = c.post(f"/api/runs/{rid}/inject", json={"text": "look at the moon"})
    assert r.json()["delivery"] == "mid-run"
    inbox = list((tmp / "routines" / "apir" / "inbox").glob("msg-*.json"))
    assert len(inbox) == 1 and read_json(inbox[0])["text"] == "look at the moon"
    assert c.post(f"/api/runs/{rid}/pause").json()["pause"] is True
    assert read_json(run_dir / "control.json")["pause"] is True
    assert c.post(f"/api/runs/{rid}/resume").json()["pause"] is False
    # terminal runs refuse pause
    atomic_write_json(run_dir / "status.json", {"run_id": rid, "state": "finished"})
    assert c.post(f"/api/runs/{rid}/pause").status_code == 409


def test_questions_flow(client):
    c, tmp = client
    routines = tmp / "routines"
    _mk_run(routines, "apir", "20260708-110000", "waiting_user",
            question={"qid": "q-20260708-110000-3", "question": "Blocking Q?", "options": []})
    pending = routines / "apir" / "questions" / "pending"
    atomic_write_json(pending / "q-old-1.json",
                      {"qid": "q-old-1", "question": "Deferred Q?", "options": ["a"],
                       "asked": "20260707", "mode": "deferred"})
    qs = c.get("/api/questions").json()
    assert {q["qid"] for q in qs} == {"q-20260708-110000-3", "q-old-1"}
    r = c.post("/api/questions/q-old-1/answer", json={"text": "option a"})
    assert r.status_code == 200
    ans = read_json(routines / "apir" / "inbox" / "answer-q-old-1.json")
    assert ans["text"] == "option a"
    assert c.post("/api/questions/q-unknown/answer", json={"text": "x"}).status_code == 404


def test_settings_endpoints_crud(client):
    c, tmp = client
    eps = c.get("/api/settings/endpoints").json()
    assert eps["endpoints"][0]["name"] == "dummy"
    r = c.post("/api/settings/endpoints", json={
        "name": "vllm", "kind": "openai", "base_url": "http://10.0.0.5:8000/v1",
        "schema_mode": "json_object", "context_chars": 60000})
    assert r.status_code == 200
    raw = yaml.safe_load((tmp / "config.yaml").read_text())
    assert raw["endpoints"]["vllm"]["base_url"] == "http://10.0.0.5:8000/v1"
    names = {e["name"] for e in c.get("/api/settings/endpoints").json()["endpoints"]}
    assert names == {"dummy", "vllm"}
    # unknown/harness kinds are rejected; claude-cli (stripped transport) is allowed
    r = c.post("/api/settings/endpoints", json={"name": "cc", "kind": "agент-sdk"})
    assert r.status_code == 400
    r = c.post("/api/settings/endpoints", json={"name": "cc", "kind": "claude-cli"})
    assert r.status_code == 200
    assert c.delete("/api/settings/endpoints/vllm").status_code == 200
    assert c.delete("/api/settings/endpoints/vllm").status_code == 404
