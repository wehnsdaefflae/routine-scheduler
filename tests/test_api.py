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


def test_audit_report_and_feedback(client):
    c, tmp = client
    routines = tmp / "routines"
    # no self-audit routine yet → friendly empty payload
    assert c.get("/api/audit").json() == {"exists": False, "report": None, "changelog": [], "last_run": None}

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
    assert c.post("/api/audit/feedback",
                  json={"kind": "decision", "target": "D1", "choice": "a", "text": "do it"}).status_code == 200
    assert c.post("/api/audit/feedback", json={"kind": "general", "text": "focus on speed"}).status_code == 200
    texts = inbox_texts()
    assert len(texts) == 3  # unique filenames — no clobbering within the same second
    assert "[AUDIT feedback · finding F1] please fix" in texts
    assert "[AUDIT decision · D1] selected: a — do it" in texts
    assert "[AUDIT note] focus on speed" in texts

    # validation + missing-routine guard
    assert c.post("/api/audit/feedback", json={"kind": "comment", "target": "F1"}).status_code == 400
    assert c.post("/api/audit/feedback", json={"kind": "bogus", "text": "x"}).status_code == 400


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


def test_first_run_setup_flag(client):
    """Fresh install → needs_setup true (drives the redirect); completing it writes the marker."""
    c, tmp = client
    assert c.get("/api/status").json()["needs_setup"] is True
    assert c.post("/api/setup/complete").json()["ok"] is True
    assert (tmp / ".setup-complete").exists()
    assert c.get("/api/status").json()["needs_setup"] is False


def test_finalize_offloads_blocking_scaffold(client, monkeypatch):
    """finalize() runs scaffold() — which makes a blocking decompose LLM call (up to 180s) — and
    MUST run it off the event loop, or a single 'create routine' freezes the whole web server."""
    import asyncio

    from rsched.web import api_wizard

    c, tmp = client
    wid = ".wizard-20260101-000000"
    d = tmp / "routines" / wid
    (d / "state").mkdir(parents=True)
    atomic_write_json(d / "state" / "wizard_result.json",
                      {"refined_instruction": "do the thing", "suggested_slug": "x", "suggested_name": "X"})

    probe = {}

    def fake_scaffold(*a, **k):
        try:
            asyncio.get_running_loop()
            probe["offloaded"] = False       # executing ON the event loop → the freeze bug
        except RuntimeError:
            probe["offloaded"] = True        # executing in a worker thread → correct
        raise ValueError("probe stop")       # short-circuit the rest of finalize
    monkeypatch.setattr(api_wizard, "scaffold", fake_scaffold)

    r = c.post(f"/api/wizard/{wid}/finalize", json={
        "slug": "newr", "name": "New R", "workflow_slug": "general-task",
        "friendly": {"frequency": "manual"}, "tags": ["a", "b", "c"], "run_now": False})
    assert r.status_code == 422              # reached the (patched) scaffold
    assert probe.get("offloaded") is True    # ...and it ran off the event loop


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


def test_finalize_recovers_fragments_from_meta(client, monkeypatch):
    """Finalize with no fragments in the body recovers the standards chosen on the draft page from
    the session meta on disk — so a finalize after a restart still applies them."""
    from rsched.web import api_wizard

    c, tmp = client
    wid, _ = _mk_wizard(tmp / "routines", "20260710-130000",
                        result={"refined_instruction": "do the thing", "suggested_slug": "x"},
                        fragments=("global-utils", "ledger-discipline"))
    captured = {}

    def fake_scaffold(*a, **k):
        captured["fragments"] = k.get("fragments")
        raise ValueError("probe stop")
    monkeypatch.setattr(api_wizard, "scaffold", fake_scaffold)

    r = c.post(f"/api/wizard/{wid}/finalize", json={
        "slug": "newr", "name": "New R", "workflow_slug": "general-task",
        "friendly": {"frequency": "manual"}, "tags": ["a", "b", "c"], "run_now": False})
    assert r.status_code == 422
    assert captured["fragments"] == ["global-utils", "ledger-discipline"]


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
