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
        "libraries_home": str(tmp_path / "library"),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "system_model": {"endpoint": "dummy", "model": "m"},
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
                             "payload": {"say": "s", "kind": "util", "name": "gu-list"}}) + "\n")
    return run_dir


def _mk_wizard(routines, ts, *, state="running", result=None, fragments=("global-utils",)):
    """A hidden .wizard-<ts> session on disk (no engine process), mirroring api_wizard.start()'s
    layout — enough for the list/detail/cancel/finalize endpoints to reconstruct it from disk."""
    wid = f".wizard-{ts}"
    d = routines / wid
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "inbox").mkdir(exist_ok=True)
    (d / "instruction.md").write_text("Collect new arxiv AI-agent papers and keep a reading list.\n")
    atomic_write_json(d / "state" / "wizard_meta.json",
                      {"wid": wid, "run_ts": ts, "created": "2026-07-10T09:00:00+02:00",
                       "fragments": list(fragments)})
    run_dir = d / "runs" / ts
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"{wid}:{ts}", "state": state, "pid": 4242, "turn": 1, "question": None})
    if result is not None:
        atomic_write_json(d / "state" / "wizard_result.json", result)
    return wid, d


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
    assert detail["workflow_ref"]["slug"] == "test-flow"   # workflow is REFERENCED, not a routine file
    assert isinstance(detail["fragments"], list)
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
    assert runs[0]["routine"] == "apir"          # log-tab feed keys off this
    assert "updated" in runs[0]
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


def test_model_switch_endpoint(client):
    """Switching a live run's model writes control.json.switch_model (keeping any pause); unknown
    endpoints and terminal runs are refused."""
    c, tmp = client
    run_dir = _mk_run(tmp / "routines", "apir", "20260710-150000", "running")
    rid = "apir:20260710-150000"
    assert c.post(f"/api/runs/{rid}/model", json={"endpoint": "nope", "model": "m"}).status_code == 400
    assert c.post(f"/api/runs/{rid}/pause").json()["pause"] is True
    r = c.post(f"/api/runs/{rid}/model", json={"endpoint": "dummy", "model": "big", "effort": "high"})
    assert r.status_code == 200
    ctrl = read_json(run_dir / "control.json")
    assert ctrl["pause"] is True                                   # pause preserved
    assert ctrl["switch_model"]["main"]["model"] == "big" and ctrl["switch_model"]["ts"]
    atomic_write_json(run_dir / "status.json", {"run_id": rid, "state": "finished"})
    assert c.post(f"/api/runs/{rid}/model", json={"endpoint": "dummy", "model": "m"}).status_code == 409


def test_resume_run_endpoint(client, monkeypatch):
    """A terminal run can be resumed (delegates to runner.resume with the same ts); an active run
    cannot."""
    c, tmp = client
    routines = tmp / "routines"
    _mk_run(routines, "apir", "20260710-160000", "failed")
    calls = {}

    async def fake_resume(cfg, ts, *, reason="resume"):
        calls["ts"], calls["slug"] = ts, cfg.slug
        return f"{cfg.slug}:{ts}"
    monkeypatch.setattr(c.app.state.runner, "resume", fake_resume)

    r = c.post("/api/runs/apir:20260710-160000/resume-run")
    assert r.status_code == 200 and calls == {"ts": "20260710-160000", "slug": "apir"}
    _mk_run(routines, "apir", "20260710-170000", "running")
    assert c.post("/api/runs/apir:20260710-170000/resume-run").status_code == 409


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
    blocking = next(q for q in qs if q["mode"] == "blocking")
    assert blocking["run_state"] == "waiting_user" and blocking["asked"] == "20260708-110000"
    r = c.post("/api/questions/q-old-1/answer", json={"text": "option a"})
    assert r.status_code == 200
    ans = read_json(routines / "apir" / "inbox" / "answer-q-old-1.json")
    assert ans["text"] == "option a"
    assert c.post("/api/questions/q-unknown/answer", json={"text": "x"}).status_code == 404


def test_deferred_question_links_back_to_its_run(client):
    """A deferred question's `asked` run_ts resolves to run_id + live run state when that run
    still exists — the Decisions view uses it to flag stale questions."""
    c, tmp = client
    routines = tmp / "routines"
    _mk_run(routines, "apir", "20260706-080000", "finished")
    atomic_write_json(routines / "apir" / "questions" / "pending" / "q-linked.json",
                      {"qid": "q-linked", "question": "Deferred?", "options": [],
                       "asked": "20260706-080000", "mode": "deferred"})
    atomic_write_json(routines / "apir" / "questions" / "pending" / "q-orphan.json",
                      {"qid": "q-orphan", "question": "Old?", "options": [],
                       "asked": "20200101-000000", "mode": "deferred"})
    by = {q["qid"]: q for q in c.get("/api/questions").json()}
    assert by["q-linked"]["run_id"] == "apir:20260706-080000"
    assert by["q-linked"]["run_state"] == "finished"
    assert "run_id" not in by["q-orphan"]            # pruned run → no dangling link


def test_answered_question_shows_settled_not_open(client):
    """Answering flips a question to `answered` on every subsequent read — the pending
    file lives on until the next run consumes it, but a reload of the Decisions page must
    not resurrect it as open."""
    c, tmp = client
    routines = tmp / "routines"
    pending = routines / "apir" / "questions" / "pending"
    atomic_write_json(pending / "q-a1.json", {"qid": "q-a1", "question": "Pick?", "options": [],
                                              "asked": "20260707", "mode": "deferred"})
    assert c.post("/api/questions/q-a1/answer", json={"text": "blue"}).status_code == 200
    q = next(x for x in c.get("/api/questions").json() if x["qid"] == "q-a1")
    assert q["answered"] is True and q["answer"] == "blue"


def test_wizard_questions_join_the_decisions_inbox(client):
    """A clarify session's questions surface on /api/questions (wizard-badged) even though
    the registry skips dot-hidden dirs, and are answerable through the same endpoint —
    the answer lands in the wizard's own inbox."""
    c, tmp = client
    routines = tmp / "routines"
    ts = "20260711-090000"
    wid, d = _mk_wizard(routines, ts, state="waiting_user")
    atomic_write_json(d / "runs" / ts / "status.json",
                      {"run_id": f"{wid}:{ts}", "state": "waiting_user", "pid": 4242, "turn": 1,
                       "question": {"qid": f"q-{ts}-1", "question": "Which arxiv areas?",
                                    "options": ["cs.AI", "cs.CL"]}})
    q = next(x for x in c.get("/api/questions").json() if x.get("wizard"))
    assert q["qid"] == f"q-{ts}-1" and q["routine"] == wid and q["mode"] == "blocking"
    assert not q.get("answered")
    r = c.post(f"/api/questions/q-{ts}-1/answer", json={"text": "cs.AI"})
    assert r.status_code == 200 and r.json()["routine"] == wid
    assert read_json(d / "inbox" / f"answer-q-{ts}-1.json")["text"] == "cs.AI"
    q2 = next(x for x in c.get("/api/questions").json() if x.get("wizard"))
    assert q2["answered"] is True and q2["answer"] == "cs.AI"


def test_subrun_transcript_nested_path(client):
    """?sub= takes a slash path of subrun numbers so the UI can unfold grandchildren;
    anything but digits/slashes is rejected."""
    c, tmp = client
    run_dir = _mk_run(tmp / "routines", "apir", "20260709-070000", "finished")
    nested = run_dir / "sub" / "1" / "sub" / "2"
    nested.mkdir(parents=True)
    (run_dir / "sub" / "1" / "transcript.jsonl").write_text(
        json.dumps({"type": "header", "run_id": "apir:x#sub1"}) + "\n")
    (nested / "transcript.jsonl").write_text(
        json.dumps({"type": "header", "run_id": "apir:x#sub1.2"}) + "\n")
    base = "/api/runs/apir:20260709-070000/transcript"
    assert c.get(f"{base}?sub=1").json()["events"][0]["run_id"] == "apir:x#sub1"
    assert c.get(f"{base}?sub=1/2").json()["events"][0]["run_id"] == "apir:x#sub1.2"
    assert c.get(f"{base}?sub=../evil").status_code == 400


def test_audit_report_and_feedback(client):
    c, tmp = client
    routines = tmp / "routines"
    # no self-audit routine yet → friendly empty payload
    assert c.get("/api/audit").json() == {"exists": False, "routine": "self-audit",
                                          "report": None, "changelog": [],
                                          "last_run": None, "pending_feedback": []}

    adir = routines / "self-audit" / "audit"
    adir.mkdir(parents=True)
    report = {"schema": 1, "run_id": "self-audit:20260709-090000", "generated": "2026-07-09T09:00:00+02:00",
              "since": {"commit": "abc1234f", "window": "2 runs"}, "summary": "healthy",
              "findings": [{"id": "F1", "severity": "problem", "title": "t", "detail": "d", "evidence": ["x:1"]}],
              "decisions": [{"id": "D1", "title": "q", "detail": "c", "options": ["a", "b", "leave as-is"]}]}
    (adir / "report.json").write_text(json.dumps(report))
    (adir / "changelog.jsonl").write_text(
        json.dumps({"ts": "2026-07-01T09:00:00+02:00", "commit": "0000001", "summary": "old change"}) + "\n" +
        json.dumps({"ts": "2026-07-08T09:00:00+02:00", "commit": "def5678a", "summary": "recent change"}) + "\n")

    a = c.get("/api/audit").json()
    assert a["exists"] is True
    assert a["report"]["findings"][0]["id"] == "F1"
    assert a["changelog"][0]["summary"] == "recent change"  # newest-first

    def inbox_texts():
        return [read_json(p)["text"] for p in (routines / "self-audit" / "inbox").glob("msg-*.json")]

    r = c.post("/api/audit/feedback", json={"kind": "comment", "target": "F1", "text": "please fix"})
    assert r.status_code == 200 and r.json()["delivery"] == "next-run"
    assert r.json()["id"].startswith("msg-")   # the handle for editing/withdrawing while queued
    assert c.post("/api/audit/feedback",
                  json={"kind": "decision", "target": "D1", "choice": "a", "text": "do it"}).status_code == 200
    assert c.post("/api/audit/feedback", json={"kind": "general", "text": "focus on speed"}).status_code == 200
    texts = inbox_texts()
    assert len(texts) == 3  # unique filenames — no clobbering within the same second
    assert "[AUDIT feedback · finding F1] please fix" in texts
    assert "[AUDIT decision · D1] selected: a — do it" in texts
    assert "[AUDIT note] focus on speed" in texts

    # unconsumed web feedback is surfaced back (the Audit tab's "waiting for the next run" list)
    pend = c.get("/api/audit").json()["pending_feedback"]
    assert {p["text"] for p in pend} == set(texts) and all(p["ts"] for p in pend)

    # validation + missing-routine guard
    assert c.post("/api/audit/feedback", json={"kind": "comment", "target": "F1"}).status_code == 400
    assert c.post("/api/audit/feedback", json={"kind": "bogus", "text": "x"}).status_code == 400


def test_audit_feedback_editable_until_consumed(client):
    """Queued feedback is live: pending items carry their structured fields + id, edits
    rewrite the same inbox file in place, withdraw removes it — and once the file is gone
    (= a run consumed it) both mutations answer 404."""
    c, tmp = client
    inbox = tmp / "routines" / "self-audit" / "inbox"
    inbox.mkdir(parents=True)

    mid = c.post("/api/audit/feedback",
                 json={"kind": "comment", "target": "F1", "text": "first take"}).json()["id"]
    p = c.get("/api/audit").json()["pending_feedback"][0]
    assert (p["id"], p["kind"], p["target"], p["raw"]) == (mid, "comment", "F1", "first take")

    # edit in place: same file (same id), re-formatted text, original ts kept + edited stamped
    ts0 = p["ts"]
    r = c.put(f"/api/audit/feedback/{mid}",
              json={"kind": "comment", "target": "F1", "text": "second take"})
    assert r.status_code == 200 and r.json()["id"] == mid
    pend = c.get("/api/audit").json()["pending_feedback"]
    assert len(pend) == 1 and pend[0]["raw"] == "second take" and pend[0]["ts"] == ts0
    assert pend[0]["text"] == "[AUDIT feedback · finding F1] second take"
    assert read_json(inbox / f"{mid}.json")["edited"]
    # an edit is re-validated like a fresh submission
    assert c.put(f"/api/audit/feedback/{mid}",
                 json={"kind": "comment", "target": "F1", "text": ""}).status_code == 400

    # a pre-editability message (formatted text only) still surfaces its fields for editing
    (inbox / "msg-legacy.json").write_text(json.dumps(
        {"text": "[AUDIT note] old style", "ts": "2026-07-01T09:00:00+02:00", "via": "web-audit"}))
    legacy = next(p for p in c.get("/api/audit").json()["pending_feedback"] if p["id"] == "msg-legacy")
    assert legacy["kind"] == "general" and legacy["raw"] == "old style"

    # non-web-audit inbox files are invisible to this channel — never editable or removable
    (inbox / "msg-injected.json").write_text(json.dumps({"text": "hi", "ts": "t"}))
    assert c.delete("/api/audit/feedback/msg-injected").status_code == 404
    assert (inbox / "msg-injected.json").exists()

    # withdraw; afterwards the id behaves exactly like a consumed message
    assert c.delete(f"/api/audit/feedback/{mid}").status_code == 200
    assert not (inbox / f"{mid}.json").exists()
    assert c.put(f"/api/audit/feedback/{mid}",
                 json={"kind": "comment", "target": "F1", "text": "x"}).status_code == 404
    assert c.delete(f"/api/audit/feedback/{mid}").status_code == 404
    assert c.delete("/api/audit/feedback/msg-..%2Fescape").status_code == 404  # malformed id


def test_settings_restart_sentinel(client):
    """The Settings restart button drops/withdraws the SAME sentinel the self-audit routine
    uses; /api/status carries the pending flag + process start so the UI can watch the cycle.
    (The drain→exit state machine itself is covered in test_restart.py — here we only assert
    the web layer's writes.)"""
    c, tmp = client
    st = c.get("/api/status").json()
    assert st["restart_requested"] is False and st["started"]

    r = c.post("/api/settings/restart")
    assert r.status_code == 200 and r.json() == {"ok": True, "active_runs": 0, "parked": 0}
    sentinel = tmp / "routines" / ".control" / "restart.request"
    assert sentinel.exists()
    st2 = c.get("/api/status").json()
    assert st2["restart_requested"] is True
    assert st2["started"] == st["started"]          # same process until the scheduler acts

    assert c.post("/api/settings/restart").status_code == 200   # idempotent re-request
    assert c.delete("/api/settings/restart").status_code == 200
    assert not sentinel.exists()
    assert c.get("/api/status").json()["restart_requested"] is False
    assert c.delete("/api/settings/restart").status_code == 200  # idempotent withdraw


def test_routine_tags(client):
    c, tmp = client
    apir = next(r for r in c.get("/api/routines").json() if r["slug"] == "apir")
    assert "tags" in apir  # present on the card (possibly empty)
    r = c.patch("/api/routines/apir", json={"tags": ["meta", "demo"]})
    assert r.status_code == 200 and "tags" in r.json()["updated"]
    assert yaml.safe_load((tmp / "routines" / "apir" / "routine.yaml").read_text())["tags"] == ["meta", "demo"]
    apir2 = next(r for r in c.get("/api/routines").json() if r["slug"] == "apir")
    assert apir2["tags"] == ["meta", "demo"]  # reflected back on the card


def test_system_model_and_llm_ready(client):
    """Setting the system_model to a live endpoint is what flips llm_ready true."""
    c, tmp = client
    assert c.get("/api/status").json()["llm_ready"] is True          # fixture: system_model → dummy
    # the system_model must point at a real endpoint
    assert c.put("/api/settings/system-model", json={"endpoint": "nope", "model": "m"}).status_code == 400
    r = c.put("/api/settings/system-model", json={"endpoint": "dummy", "model": "x"})
    assert r.status_code == 200 and r.json()["system_model"]["endpoint"] == "dummy"
    assert yaml.safe_load((tmp / "config.yaml").read_text())["system_model"]["endpoint"] == "dummy"
    # remove the only endpoint → the system_model dangles → no longer ready
    c.delete("/api/settings/endpoints/dummy")
    assert c.get("/api/status").json()["llm_ready"] is False


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


def test_endpoint_inline_key_saved_and_preserved(client):
    """Paste an API key in the UI → stored in config, never echoed back, kept across edits."""
    c, tmp = client
    assert c.put("/api/settings/endpoints/dummy", json={
        "name": "dummy", "kind": "openai", "base_url": "http://x/v1", "api_key": "sk-secret"}).status_code == 200
    ep = next(e for e in c.get("/api/settings/endpoints").json()["endpoints"] if e["name"] == "dummy")
    assert ep["has_inline_key"] is True and "api_key" not in ep          # flagged, never returned
    assert yaml.safe_load((tmp / "config.yaml").read_text())["endpoints"]["dummy"]["api_key"] == "sk-secret"
    # editing another field with the key box left blank keeps the saved key
    c.put("/api/settings/endpoints/dummy", json={"name": "dummy", "kind": "openai", "base_url": "http://y/v1"})
    assert yaml.safe_load((tmp / "config.yaml").read_text())["endpoints"]["dummy"]["api_key"] == "sk-secret"


def test_endpoints_prefer_inline_key(monkeypatch):
    """Inline key (UI-set) wins over a missing key_env_file, for openai + claude-cli."""
    from rsched.config import EndpointConfig
    from rsched.endpoints import make_endpoint
    from rsched.endpoints.claude_cli import resolve_token

    ep = make_endpoint(EndpointConfig(name="x", kind="openai", api_key="inline-123",
                                      key_env_file="/nonexistent.env", key_var="K"))
    assert ep._resolve_key() == "inline-123"
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert resolve_token("/nonexistent.env", "tok-abc") == "tok-abc"
    assert resolve_token("/nonexistent.env", "") is None


def test_secrets_store(client, tmp_path, monkeypatch):
    """Central secrets: write-only via the API (values never echoed), readable by the engine."""
    c, _ = client
    store = tmp_path / "secrets.env"
    monkeypatch.setattr("rsched.secrets.secrets_path", lambda: store)
    from rsched.secrets import load_secrets

    assert c.get("/api/settings/secrets").json()["keys"] == []
    assert c.put("/api/settings/secrets", json={"key": "FOO_TOKEN", "value": "abc123"}).status_code == 200
    j = c.get("/api/settings/secrets").json()
    assert j["keys"] == ["FOO_TOKEN"] and "abc123" not in str(j)      # names only, never the value
    assert load_secrets()["FOO_TOKEN"] == "abc123"                    # the engine reads the value
    assert c.put("/api/settings/secrets", json={"key": "bad key", "value": "x"}).status_code == 400
    assert c.delete("/api/settings/secrets/FOO_TOKEN").status_code == 200
    assert load_secrets() == {} and c.delete("/api/settings/secrets/FOO_TOKEN").status_code == 404


def test_secrets_injected_into_utils_and_endpoints(tmp_path, monkeypatch):
    """Store → util env (env-first) but LLM keys stay stripped; endpoints read their key_var."""
    monkeypatch.setattr("rsched.secrets.secrets_path", lambda: tmp_path / "secrets.env")
    from rsched.secrets import set_secret
    set_secret("FOO_TOKEN", "tok")
    set_secret("OPENROUTER_KEY", "sk-or-xyz")

    from rsched.utils_lib import _child_env
    env = _child_env()
    assert env["FOO_TOKEN"] == "tok"                 # a util credential flows through
    assert "OPENROUTER_KEY" not in env               # …but LLM keys never reach utils

    from rsched.config import EndpointConfig
    from rsched.endpoints import make_endpoint
    ep = make_endpoint(EndpointConfig(name="or", kind="openai", key_var="OPENROUTER_KEY"))
    assert ep._resolve_key() == "sk-or-xyz"          # the endpoint picks it up from the store


def test_github_device_poll_unknown_flow(client):
    """Polling an unknown/expired device flow is rejected before any network call."""
    c, _ = client
    assert c.post("/api/settings/github/device-poll", json={"flow_id": "nope"}).status_code == 404


def test_github_device_flow_resume(client):
    """A pending device flow is resumable by id after a reload (the UI keeps ?flow=<id> in the URL);
    unknown/expired flows 404 and are evicted. The device_code is never echoed back."""
    import time

    from rsched.web.settings import github

    c, _ = client
    assert c.get("/api/settings/github/device-flow/nope").status_code == 404
    github._device_flows["fl-resume"] = {
        "device_code": "dc", "client_id": "cid", "user_code": "WXYZ-1234",
        "verification_uri": "https://github.com/login/device", "interval": 5,
        "expires_at": time.time() + 300}
    try:
        j = c.get("/api/settings/github/device-flow/fl-resume").json()
        assert j["user_code"] == "WXYZ-1234" and j["expires_in"] > 0 and "device_code" not in j
        github._device_flows["fl-exp"] = {"user_code": "X", "verification_uri": "u",
                                          "expires_at": time.time() - 1}
        assert c.get("/api/settings/github/device-flow/fl-exp").status_code == 404
        assert "fl-exp" not in github._device_flows          # expired → evicted
    finally:
        github._device_flows.pop("fl-resume", None)


def test_status_meta_routines(client):
    """/api/status lists meta-tagged routines with their enabled state — the UI's
    'self-improvement is off' first-launch notice keys off this."""
    c, _ = client
    assert c.get("/api/status").json()["meta_routines"] == []     # nothing meta-tagged yet
    assert c.patch("/api/routines/apir",
                   json={"enabled": False, "tags": ["meta", "demo"]}).status_code == 200
    assert c.get("/api/status").json()["meta_routines"] == [{"slug": "apir", "enabled": False}]


def test_first_run_setup_flag(client):
    """Fresh install → needs_setup true (drives the redirect); completing it writes the marker."""
    c, tmp = client
    assert c.get("/api/status").json()["needs_setup"] is True
    assert c.post("/api/setup/complete").json()["ok"] is True
    assert (tmp / ".setup-complete").exists()
    assert c.get("/api/status").json()["needs_setup"] is False


def test_finalize_launches_background_build(client):
    """finalize() returns immediately (the slow decompose build runs in the background) and rejects
    an obvious conflict up front — a routine already using that slug."""
    c, tmp = client
    wid = ".wizard-20260101-000000"
    d = tmp / "routines" / wid
    (d / "state").mkdir(parents=True)
    atomic_write_json(d / "state" / "wizard_result.json",
                      {"refined_instruction": "do the thing", "suggested_slug": "x"})
    body = {"slug": "newr", "name": "New R", "workflow_slug": "general-task",
            "friendly": {"frequency": "manual"}, "tags": ["a", "b", "c"], "run_now": False}
    r = c.post(f"/api/wizard/{wid}/finalize", json=body)
    assert r.status_code == 200 and r.json()["building"] is True and r.json()["slug"] == "newr"
    (tmp / "routines" / "taken").mkdir()                       # up-front slug conflict → 409
    assert c.post(f"/api/wizard/{wid}/finalize", json={**body, "slug": "taken"}).status_code == 409


def test_wizard_list_detail_and_stage(client):
    """In-flight sessions are discoverable + resumable from disk; the stage is derived from what
    the clarify run has actually produced (chat → still clarifying, suggest → result ready)."""
    c, tmp = client
    routines = tmp / "routines"
    wid_chat, _ = _mk_wizard(routines, "20260710-090000", state="running")
    wid_ready, _ = _mk_wizard(routines, "20260710-100000",
                              result={"refined_instruction": "do X", "suggested_slug": "x"})
    lst = c.get("/api/wizard").json()
    assert lst[0]["wid"] == wid_ready                      # newest first
    by = {w["wid"]: w for w in lst}
    assert by[wid_chat]["stage"] == "chat" and by[wid_chat]["has_result"] is False
    assert by[wid_ready]["stage"] == "suggest" and by[wid_ready]["has_result"] is True
    det = c.get(f"/api/wizard/{wid_chat}").json()
    assert det["stage"] == "chat" and det["fragments"] == ["global-utils"]
    assert "arxiv" in det["draft"]                         # preview recovered from instruction.md
    assert c.get("/api/wizard/.wizard-nope").status_code == 404


def test_wizard_stage_error_when_terminal_without_result(client):
    """A clarify run that reached a terminal state without producing a result → 'error' stage."""
    c, tmp = client
    wid, _ = _mk_wizard(tmp / "routines", "20260710-110000", state="failed")
    assert c.get(f"/api/wizard/{wid}").json()["stage"] == "error"


def test_wizard_cancel_archives_session(client):
    """Cancel stops tracking the session and moves it out of routines_home so it is no longer
    in-flight (the setup banner clears). pid 4242 isn't alive → no real process is signaled."""
    c, tmp = client
    routines = tmp / "routines"
    wid, d = _mk_wizard(routines, "20260710-120000")
    assert c.delete(f"/api/wizard/{wid}").json()["ok"] is True
    assert not d.exists()
    assert (routines / ".archive" / f"{wid.lstrip('.')}-canceled").exists()
    assert c.get("/api/wizard").json() == []
    assert c.delete(f"/api/wizard/{wid}").status_code == 404


def test_build_routine_threads_params_and_fragments(client, monkeypatch):
    """The background build recovers the session's fragments + the clarifier's resolved params and
    threads them into scaffold; on failure it records the error and stays retryable."""
    import asyncio

    from rsched.web import api_wizard, wizard_store

    c, tmp = client
    wid, d = _mk_wizard(tmp / "routines", "20260710-130000",
                        result={"refined_instruction": "do the thing", "suggested_slug": "x",
                                "params": {"DELIVERABLE": "a weekly report"}},
                        fragments=("global-utils", "ledger-discipline"))
    captured = {}

    def fake_scaffold(*a, **k):
        captured.update(k)
        raise ValueError("probe stop")
    monkeypatch.setattr(api_wizard, "scaffold", fake_scaffold)

    body = api_wizard.FinalizeBody(slug="newr", name="New R", workflow_slug="general-task",
                                   friendly={"frequency": "manual"}, tags=["a", "b", "c"], run_now=False)
    asyncio.run(api_wizard._build_routine(c.app.state, wid, d, body, wizard_store.read_result(d)))
    assert captured["fragments"] == ["global-utils", "ledger-discipline"]
    assert captured["params"] == {"DELIVERABLE": "a weekly report"}
    fin = read_json(d / "state" / "finalize.json")           # failure recorded, session retryable
    assert fin["state"] == "error" and "probe stop" in fin["error"]


def test_wizard_suggest_leads_with_clarifier_choice(client, monkeypatch):
    """The clarifier suggested a pattern; /suggest returns it at the head of the pick list (so the
    wizard pre-selects it) and passes through the resolved params."""
    from rsched.web import api_wizard, wizard_store

    c, tmp = client
    wid, _ = _mk_wizard(tmp / "routines", "20260710-140000",
                        result={"refined_instruction": "do X", "suggested_slug": "x",
                                "workflow_choice": {"slug": "general-task"},
                                "params": {"DELIVERABLE": "a report"}})
    monkeypatch.setattr(api_wizard, "suggest_tags", lambda *a, **k: ["a", "b", "c"])
    monkeypatch.setattr(wizard_store, "candidate_patterns", lambda server: [
        {"slug": "other-flow", "description": "another"},
        {"slug": "general-task", "description": "the default"}])
    r = c.post(f"/api/wizard/{wid}/suggest").json()
    assert r["suggestions"][0]["slug"] == "general-task" and r["suggestions"][0]["confidence"] == 1.0
    assert r["none_fit"] is False and r["wizard_result"]["params"]["DELIVERABLE"] == "a report"


def test_wizard_candidates_inline_pattern_source(tmp_path):
    """start() writes the workflow patterns (with their full Python control flow) into the session's
    state/, so the clarifier can suggest + marry by reading one file. Meta patterns are excluded."""
    from pathlib import Path

    from rsched.config import ServerConfig
    from rsched.web import wizard_store

    server = ServerConfig()
    server.libraries_home = Path(__file__).resolve().parents[1] / "library-seed"
    d = tmp_path / "wiz"
    (d / "state").mkdir(parents=True)
    wizard_store.write_candidates(server, d)
    text = (d / "state" / "candidates.md").read_text()
    assert "general-task" in text and "```python" in text and "def main():" in text
    assert "clarify-instruction" not in text          # meta patterns are excluded from candidates


def test_library_reports_default_fragments(client):
    """/api/library carries the server's DEFAULT_FRAGMENTS so the wizard's standards picker
    pre-checks from config instead of a hard-coded frontend list."""
    from rsched.config import DEFAULT_FRAGMENTS

    c, tmp = client
    (tmp / "library" / "workflows").mkdir(parents=True, exist_ok=True)
    lib = c.get("/api/library").json()
    assert lib["default_fragments"] == list(DEFAULT_FRAGMENTS)


def test_wizard_transcript_paging_and_event_offset(client):
    """The clarify chat is tailable like a run: a paged transcript endpoint returns a byte
    offset, and /events accepts that offset — the UI's reconnect-with-resume path."""
    c, tmp = client
    wid, d = _mk_wizard(tmp / "routines", "20260710-180000")
    run_dir = d / "runs" / "20260710-180000"
    with open(run_dir / "transcript.jsonl", "w") as fh:
        fh.write(json.dumps({"type": "header", "run_id": f"{wid}:20260710-180000"}) + "\n")
        fh.write(json.dumps({"ts": "t", "type": "assistant_action", "turn": 1,
                             "payload": {"say": "hi", "kind": "ask_user", "question": "?"}}) + "\n")
    tr = c.get(f"/api/wizard/{wid}/transcript").json()
    assert [e["type"] for e in tr["events"]] == ["header", "assistant_action"]
    assert tr["offset"] > 0
    tr2 = c.get(f"/api/wizard/{wid}/transcript", params={"offset": tr["offset"]}).json()
    assert tr2["events"] == [] and tr2["offset"] == tr["offset"]
    assert c.get("/api/wizard/.wizard-nope/transcript").status_code == 404


def test_test_remote_endpoint(client):
    """The Settings 'Test' button: reachable+authorized remote → ok; junk → surfaced error."""
    import subprocess
    c, tmp = client

    def git(d, *a):
        subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)

    assert c.post("/api/settings/test-remote", json={"remote": ""}).json()["ok"] is False

    bare = tmp / "r.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    work = tmp / "w"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "t@t")
    git(work, "config", "user.name", "t")
    (work / "f").write_text("x")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "i")
    git(work, "push", "-q", str(bare), "main")

    ok = c.post("/api/settings/test-remote", json={"remote": str(bare)}).json()
    assert ok["ok"] is True and ok["branches"] >= 1, ok
    bad = c.post("/api/settings/test-remote", json={"remote": str(tmp / "nope.git")}).json()
    assert bad["ok"] is False and bad["error"], bad


def test_source_repo_settings(tmp_path):
    """The self-audit push target: GET reports the scheduler's own repo; PUT points origin
    at a fork + pushes. Uses a throwaway repo + local bare remote — never the real tree."""
    import subprocess

    def git(d, *a):
        subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)

    src = tmp_path / "src_repo"
    src.mkdir()
    git(src, "init", "-q", "-b", "main")
    git(src, "config", "user.email", "t@t")
    git(src, "config", "user.name", "t")
    (src / "f.txt").write_text("x")
    git(src, "add", "-A")
    git(src, "commit", "-qm", "init")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    (tmp_path / "routines").mkdir()

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN, "routines_home": str(tmp_path / "routines"), "source_repo": str(src)}))
    server, problems = load_server_config(cfg_path)
    assert not problems and server.source_repo == src
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        g = c.get("/api/settings/source").json()
        assert g["home"] == str(src) and g["exists"] is True
        assert g["branch"] == "main" and g["remote"] == ""        # no origin yet
        # PUT points origin at the fork, pushes, and persists the choice to config.yaml
        r = c.put("/api/settings/source", json={"remote": str(bare)}).json()
        assert r["ok"] and r["pushed"] is True, r
        assert yaml.safe_load(cfg_path.read_text())["source_remote"] == str(bare)
        assert c.get("/api/settings/source").json()["remote"] == str(bare)   # now visible as origin


def test_static_and_index_are_no_cache(client):
    c, _ = client
    for path in ("/", "/static/app.js"):
        r = c.get(path)
        assert r.status_code == 200
        assert r.headers.get("cache-control") == "no-cache"


def test_audit_reports_routine_slug(client):
    c, _ = client
    r = c.get("/api/audit")
    assert r.status_code == 200
    assert r.json()["routine"] == "self-audit"


def test_ui_trace_ingest_and_retention(client):
    c, tmp = client
    r = c.post("/api/ui-trace", json={"events": [
        {"kind": "nav", "view": "audit"},
        {"kind": "click", "view": "audit", "target": "run self-audit now"},
        {"kind": "bogus", "view": "x"},
        {"kind": "error", "view": "run", "target": "toast", "detail": "X" * 500},
    ]})
    assert r.status_code == 200 and r.json()["recorded"] == 3
    tdir = tmp / "routines" / ".ui-traces"
    files = list(tdir.glob("*.jsonl"))
    assert len(files) == 1
    lines = [json.loads(x) for x in files[0].read_text().splitlines()]
    assert [e["kind"] for e in lines] == ["nav", "click", "error"]
    assert len(lines[2]["detail"]) == 200            # server-side truncation
    stale = tdir / "20200101.jsonl"
    stale.write_text("{}\n")
    c.post("/api/ui-trace", json={"events": [{"kind": "nav", "view": "log"}]})
    assert not stale.exists()                        # pruned on write
    assert c.post("/api/ui-trace", json={"events": []}).json()["recorded"] == 0


def test_audit_decisions_merge_into_questions(client):
    c, tmp = client
    adir = tmp / "routines" / "self-audit" / "audit"
    adir.mkdir(parents=True)
    (tmp / "routines" / "self-audit" / "inbox").mkdir(exist_ok=True)
    atomic_write_json(adir / "report.json", {
        "generated": "2026-07-11T09:00:00+00:00",
        "findings": [],
        "decisions": [
            {"id": "D1", "title": "Old one", "detail": "SETTLED by user: done.", "options": []},
            {"id": "D2", "title": "Pick a path", "detail": "context", "status": "open",
             "options": ["A", "B", "leave as-is"]},
            {"id": "D3", "title": "Closed", "detail": "x", "status": "settled", "options": []},
        ]})
    qs = c.get("/api/questions").json()
    metas = [q for q in qs if q.get("meta")]
    assert [q["qid"] for q in metas] == ["audit:D2"]      # settled ones stay out of the inbox
    q = metas[0]
    assert q["routine"] == "self-audit" and q["options"] == ["A", "B", "leave as-is"]
    assert "Pick a path" in q["question"]

    # answering with an exact option → decision feedback with that choice in the inbox
    r = c.post("/api/questions/audit:D2/answer", json={"text": "A"})
    assert r.status_code == 200 and r.json()["meta"] is True
    msgs = list((tmp / "routines" / "self-audit" / "inbox").glob("msg-*.json"))
    assert len(msgs) == 1
    assert read_json(msgs[0])["text"] == "[AUDIT decision · D2] selected: A"
    # queued now → gone from the open list
    assert not [q for q in c.get("/api/questions").json() if q.get("meta")]
    # unknown decision → 404
    assert c.post("/api/questions/audit:D9/answer", json={"text": "x"}).status_code == 404


def test_converse_endpoint(client, monkeypatch):
    """Live run: converse = an ordinary mid-run injection. Terminal run: the message lands in
    the inbox AND the run is resumed in place (runner.resume) — the open-ended conversation."""
    c, tmp = client
    run_dir = _mk_run(tmp / "routines", "apir", "20260708-110000", "running")
    rid = "apir:20260708-110000"
    assert c.post(f"/api/runs/{rid}/converse", json={"text": "and the stars?"}).json()["delivery"] == "mid-run"
    msgs = [read_json(p)["text"] for p in (tmp / "routines" / "apir" / "inbox").glob("msg-*.json")]
    assert "and the stars?" in msgs

    atomic_write_json(run_dir / "status.json", {"run_id": rid, "state": "finished"})
    resumed = {}

    async def fake_resume(cfg, ts, *, reason=""):
        resumed.update(slug=cfg.slug, ts=ts, reason=reason)
        return f"{cfg.slug}:{ts}"

    monkeypatch.setattr(c.app.state.runner, "resume", fake_resume)
    r = c.post(f"/api/runs/{rid}/converse", json={"text": "one more thing"})
    assert r.json()["delivery"] == "resumed"
    assert resumed == {"slug": "apir", "ts": "20260708-110000", "reason": "converse"}
    assert c.post(f"/api/runs/{rid}/converse", json={"text": "  "}).status_code == 400
