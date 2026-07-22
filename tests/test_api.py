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
        "models": {"m": {"endpoint": "dummy", "model": "m"}},
        "system_model": "m",
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
                       "usage": {"in": 10, "out": 4, "cost": 0.0123}, "elapsed_s": 95,
                       "question": question})
    with (run_dir / "transcript.jsonl").open("w") as fh:
        fh.write(json.dumps({"type": "header", "run_id": f"{slug}:{ts}"}) + "\n")
        fh.write(json.dumps({"ts": "t", "type": "assistant_action", "turn": 1,
                             "payload": {"say": "s", "kind": "util", "name": "gu-list"}}) + "\n")
    return run_dir


def _mk_wizard(routines, ts, *, state="running", result=None):
    """A hidden .wizard-<ts> session on disk (no engine process), mirroring api_wizard.start()'s
    layout — enough for the list/detail/cancel/finalize endpoints to reconstruct it from disk."""
    wid = f".wizard-{ts}"
    d = routines / wid
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "inbox").mkdir(exist_ok=True)
    (d / "instruction.md").write_text("Collect new arxiv AI-agent papers and keep a reading list.\n")
    atomic_write_json(d / "state" / "wizard_meta.json",
                      {"wid": wid, "run_ts": ts, "created": "2026-07-10T09:00:00+02:00"})
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
    # the bearer token is NEVER accepted via query string — it would leak into access logs
    assert bare.get(f"/api/routines?token={TOKEN}").status_code == 401
    assert c.get("/api/status").status_code == 200


def test_sse_ticket_flow(client):
    """EventSource auth: a short-lived ticket minted over the authed channel works in the
    query string; garbage and expired tickets do not."""
    c, _ = client
    bare = TestClient(c.app)
    minted = c.post("/api/sse-ticket").json()
    assert minted["ttl"] == 60
    ticket = minted["ticket"]
    assert bare.get(f"/api/routines?ticket={ticket}").status_code == 200
    assert bare.get("/api/routines?ticket=bogus").status_code == 401
    c.app.state.sse_tickets[ticket] = 0.0   # fast-forward: long expired
    assert bare.get(f"/api/routines?ticket={ticket}").status_code == 401
    # expired tickets are purged at the next mint
    c.post("/api/sse-ticket")
    assert ticket not in c.app.state.sse_tickets


def test_routine_cards_and_detail(client):
    c, tmp = client
    _mk_run(tmp / "routines", "apir", "20260707-070000", "finished")
    cards = c.get("/api/routines").json()
    assert len(cards) == 1 and cards[0]["slug"] == "apir" and cards[0]["cron"] == "0 7 * * 1"
    # the card carries the last run's stats — the dashboard sorts/filters on these
    lr = cards[0]["last_run"]
    assert lr["turns"] == 2 and lr["elapsed_s"] == 95
    assert lr["usage"] == {"in": 10, "out": 4, "cost": 0.0123}
    detail = c.get("/api/routines/apir").json()
    assert detail["workflow_ref"]["slug"] == "test-flow"   # workflow is REFERENCED, not a routine file
    assert isinstance(detail["permissions"], list)   # hermetic test library → may be empty
    assert all("requires" in p and "active" in p for p in detail["permissions"])
    assert set(detail["capabilities"]["active"]) == {"actions", "utils", "confirm", "runs",
                                                      "workflows"}
    assert detail["runs"][0]["state"] == "finished"
    assert c.get("/api/routines/nope").status_code == 404


def test_routine_card_recent_runs_window(client):
    """The heartbeat strip's data: recent_runs on every card, newest first, with the finish
    outcome — a partial finish reads state=finished, so `outcome` is what keeps it amber."""
    c, tmp = client
    routines = tmp / "routines"
    _mk_run(routines, "apir", "20260701-070000", "finished")
    run2 = _mk_run(routines, "apir", "20260702-070000", "finished")
    st = read_json(run2 / "status.json")
    st["outcome"] = "partial"
    atomic_write_json(run2 / "status.json", st)
    _mk_run(routines, "apir", "20260703-070000", "failed")
    _mk_run(routines, "apir", "20260704-070000", "aborted")
    rr = c.get("/api/routines").json()[0]["recent_runs"]
    assert [r["ts"] for r in rr] == ["20260704-070000", "20260703-070000",
                                     "20260702-070000", "20260701-070000"]
    assert rr[1]["state"] == "failed"
    assert rr[2]["state"] == "finished" and rr[2]["outcome"] == "partial"
    # flattened hover stats, exactly the fields the strip needs (tokens = in + out)
    assert rr[0] == {"run_id": "apir:20260704-070000", "ts": "20260704-070000",
                     "state": "aborted", "outcome": None, "turns": 2, "tokens": 14,
                     "cost": 0.0123, "elapsed_s": 95}


def test_patch_routine_and_409_guard(client):
    c, tmp = client
    r = c.patch("/api/routines/apir", json={"enabled": False, "schedule": {"cron": "0 9 * * 2"}})
    assert r.status_code == 200
    raw = yaml.safe_load((tmp / "routines" / "apir" / "routine.yaml").read_text())
    assert raw["enabled"] is False and raw["schedule"]["cron"] == "0 9 * * 2"
    assert raw["schedule"]["tz"] == "Europe/Berlin"  # merged, not replaced
    _mk_run(tmp / "routines", "apir", "20260708-090000", "running")
    assert c.patch("/api/routines/apir", json={"enabled": True}).status_code == 409
    assert c.put("/api/routines/apir/file",
                 json={"path": "main.md", "content": "x"}).status_code == 409


def test_patch_routine_resource_fields(client):
    """keep_runs (nested under retention:), the fs roots (top-level, stripped), and the
    schedule catchup policy all save through PATCH and surface in the detail read."""
    c, tmp = client

    def raw():
        return yaml.safe_load((tmp / "routines" / "apir" / "routine.yaml").read_text())

    r = c.patch("/api/routines/apir", json={
        "keep_runs": 5,
        "fs_read_roots": ["~/data", "  ~/more  "],
        "fs_write_roots": ["~/out"],
        "schedule": {"friendly": {"frequency": "daily", "time": "06:00"}, "catchup": "run_once"}})
    assert r.status_code == 200
    y = raw()
    assert y["retention"]["keep_runs"] == 5
    assert y["fs_read_roots"] == ["~/data", "~/more"]        # whitespace stripped
    assert y["fs_write_roots"] == ["~/out"]
    assert y["schedule"]["catchup"] == "run_once" and y["schedule"]["cron"] == "0 6 * * *"
    detail = c.get("/api/routines/apir").json()
    assert detail["keep_runs"] == 5 and detail["catchup"] == "run_once"
    assert detail["fs_read_roots"] and detail["fs_write_roots"]   # resolved to absolute server paths
    # validation: positive keep_runs, a known catchup policy, no empty root strings
    assert c.patch("/api/routines/apir", json={"keep_runs": 0}).status_code == 400
    assert c.patch("/api/routines/apir", json={"schedule": {"catchup": "bogus"}}).status_code == 400
    assert c.patch("/api/routines/apir", json={"fs_read_roots": ["ok", ""]}).status_code == 400


def test_put_routine_file(client):
    c, tmp = client
    r = c.put("/api/routines/apir/file", json={"path": "main.md", "content": "# New main"})
    assert r.status_code == 200
    assert (tmp / "routines" / "apir" / "main.md").read_text() == "# New main"


def test_file_read_guarded(client):
    c, _ = client
    assert c.get("/api/routines/apir/file", params={"path": "LEDGER.md"}).status_code == 200
    assert c.get("/api/routines/apir/file",
                 params={"path": "../../../etc/passwd"}).status_code == 404


def test_stategraph_endpoint(client):
    """The routine's stage modules ARE its state graph — nodes in main.md mention order,
    the current phase from the latest run's status.json (the stage module the run last
    read; the executor stamps it)."""
    c, tmp = client
    d = tmp / "routines" / "apir"
    (d / "stages").mkdir(exist_ok=True)
    (d / "stages" / "write.md").write_text("# Step: emit\n", encoding="utf-8")
    (d / "stages" / "gather.md").write_text("collect things\n", encoding="utf-8")
    d.joinpath("main.md").write_text(
        "## Run flow\n1. `stages/gather.md` — collect.\n2. `stages/write.md` — emit.\n",
        encoding="utf-8")
    run = d / "runs" / "20991231-070000"          # lexically latest, whatever else exists
    run.mkdir(parents=True, exist_ok=True)
    (run / "status.json").write_text('{"phase": "gather"}', encoding="utf-8")
    g = c.get("/api/routines/apir/stategraph").json()
    assert [s["name"] for s in g["states"]] == ["gather", "write"]
    assert g["states"][1]["desc"] == "Step: emit"
    assert g["current"] == "gather"


def test_recipe_endpoint(client):
    """The recipe tree — main.md + stage modules (in run-flow order) + trait modules, each with
    its heading outline; the routine page's file browser."""
    c, tmp = client
    d = tmp / "routines" / "apir"
    (d / "stages").mkdir(exist_ok=True)
    d.joinpath("main.md").write_text(
        "## Run flow\n1. **gather** — g.\n2. **write** — w.\n\n## Done\n- x\n", encoding="utf-8")
    (d / "stages" / "write.md").write_text("## Steps\n", encoding="utf-8")
    (d / "stages" / "gather.md").write_text("text\n", encoding="utf-8")
    r = c.get("/api/routines/apir/recipe").json()
    assert r["main"]["path"] == "main.md"
    assert [h["text"] for h in r["main"]["outline"]] == ["Run flow", "Done"]
    assert [s["name"] for s in r["stages"]] == ["gather", "write"]   # run-flow order, not alphabetical


def test_routine_artifacts_listed_and_served(client):
    """Routine artifacts get the conversations treatment: listed newest-first, served raw
    — but ONLY artifacts/ (recipe/config stays on the JSON /file endpoint)."""
    c, tmp = client
    art = tmp / "routines" / "apir" / "artifacts"
    art.mkdir()
    (art / "report.html").write_text("<h1>findings</h1>", encoding="utf-8")
    items = c.get("/api/routines/apir/artifacts").json()
    assert [i["name"] for i in items] == ["report.html"]
    r = c.get("/api/routines/apir/artifact", params={"path": "artifacts/report.html"})
    assert r.status_code == 200 and r.text == "<h1>findings</h1>"
    assert "text/html" in r.headers["content-type"]
    # traversal and non-artifact paths are refused on the RESOLVED path
    assert c.get("/api/routines/apir/artifact",
                 params={"path": "artifacts/../routine.yaml"}).status_code == 400
    assert c.get("/api/routines/apir/artifact",
                 params={"path": "routine.yaml"}).status_code == 400


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


def test_run_files_endpoint(client):
    """/runs/{id}/files serves the file-activity read-model — the rail's files card."""
    c, tmp = client
    run_dir = _mk_run(tmp / "routines", "apir", "20260707-080000", "finished")
    with (run_dir / "transcript.jsonl").open("a") as fh:
        fh.write(json.dumps({"type": "observation", "turn": 1, "payload": {
            "kind": "write_file", "path": "artifacts/out.md", "bytes": 42}}) + "\n")
    files = c.get("/api/runs/apir:20260707-080000/files").json()["files"]
    assert files == [{"path": "artifacts/out.md", "reads": 0, "writes": 1, "edits": 0,
                      "bytes": 42, "errors": 0, "sub": False}]
    assert c.get("/api/runs/apir:20990101-000000/files").status_code == 404


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
    catalog models and terminal runs are refused."""
    c, tmp = client
    run_dir = _mk_run(tmp / "routines", "apir", "20260710-150000", "running")
    rid = "apir:20260710-150000"
    assert c.post(f"/api/runs/{rid}/model", json={"model": "nope"}).status_code == 400
    assert c.post(f"/api/runs/{rid}/pause").json()["pause"] is True
    r = c.post(f"/api/runs/{rid}/model", json={"model": "m"})   # a catalog model name
    assert r.status_code == 200
    ctrl = read_json(run_dir / "control.json")
    assert ctrl["pause"] is True                                   # pause preserved
    assert ctrl["switch_model"]["main"] == "m" and ctrl["switch_model"]["ts"]
    atomic_write_json(run_dir / "status.json", {"run_id": rid, "state": "finished"})
    assert c.post(f"/api/runs/{rid}/model", json={"model": "m"}).status_code == 409


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


def test_question_snooze_lifecycle(client):
    """Snooze hides a deferred record until a timestamp (a `snoozed_until` field on the
    ONE record shape, `snoozed: true` derived on read); minutes<=0 clears it. A blocking
    question refuses to be snoozed — it parks a live run."""
    c, tmp = client
    routines = tmp / "routines"
    pending = routines / "apir" / "questions" / "pending"
    atomic_write_json(pending / "q-z1.json", {"qid": "q-z1", "question": "Later?",
                                              "options": [], "asked": "20260707",
                                              "mode": "deferred"})
    r = c.post("/api/questions/q-z1/snooze", json={"minutes": 60})
    assert r.status_code == 200 and r.json()["snoozed_until"]
    assert read_json(pending / "q-z1.json")["snoozed_until"] == r.json()["snoozed_until"]
    q = next(x for x in c.get("/api/questions").json() if x["qid"] == "q-z1")
    assert q["snoozed"] is True

    assert c.post("/api/questions/q-z1/snooze", json={"minutes": 0}).status_code == 200
    assert "snoozed_until" not in read_json(pending / "q-z1.json")
    q = next(x for x in c.get("/api/questions").json() if x["qid"] == "q-z1")
    assert not q.get("snoozed")

    # a blocking question cannot be snoozed — answer it or defer it
    _mk_run(routines, "apir", "20260708-110000", "waiting_user",
            question={"qid": "q-b1", "question": "Now?", "options": []})
    atomic_write_json(pending / "q-b1.json", {"qid": "q-b1", "question": "Now?",
                                              "options": [], "asked": "20260708-110000",
                                              "mode": "blocking"})
    assert c.post("/api/questions/q-b1/snooze", json={"minutes": 60}).status_code == 400


def test_question_defer_to_next_run(client):
    """Defer writes the inbox marker the engine's blocking wait consumes; only a blocking
    question can be deferred, and a queued answer wins over a late defer click."""
    c, tmp = client
    routines = tmp / "routines"
    pending = routines / "apir" / "questions" / "pending"
    _mk_run(routines, "apir", "20260708-110000", "waiting_user",
            question={"qid": "q-d1", "question": "Ship?", "options": []})
    atomic_write_json(pending / "q-d1.json", {"qid": "q-d1", "question": "Ship?",
                                              "options": [], "asked": "20260708-110000",
                                              "mode": "blocking"})
    assert c.post("/api/questions/q-d1/defer").status_code == 200
    marker = read_json(routines / "apir" / "inbox" / "answer-q-d1.json")
    assert marker["defer"] is True and "text" not in marker
    # the marker is already queued → a second defer (or any change) conflicts
    assert c.post("/api/questions/q-d1/defer").status_code == 409

    atomic_write_json(pending / "q-d2.json", {"qid": "q-d2", "question": "Deferred?",
                                              "options": [], "asked": "20260707",
                                              "mode": "deferred"})
    assert c.post("/api/questions/q-d2/defer").status_code == 400


def test_routine_card_spend_line(client):
    """Cards carry this month + last month from the durable spend series, so the
    dashboard can answer "what does this cost me and is it growing" at a glance."""
    import json as _j

    c, tmp = client
    ctrl = tmp / "routines" / ".control"
    ctrl.mkdir(parents=True, exist_ok=True)
    entries = [
        {"ts": "2026-06-10T08:00:00+00:00", "routine": "apir", "depth": 0,
         "tokens": 900, "cost": 0.9, "referrals": 2},
        {"ts": "2026-07-10T08:00:00+00:00", "routine": "apir", "depth": 0,
         "tokens": 2000, "cost": 2.0, "referrals": 1},
    ]
    (ctrl / "workflow-usage.jsonl").write_text(
        "".join(_j.dumps(e) + "\n" for e in entries), encoding="utf-8")
    card = next(x for x in c.get("/api/routines").json() if x["slug"] == "apir")
    assert card["spend"]["month"] == "2026-07"
    assert card["spend"]["current"]["tokens"] == 2000
    assert card["spend"]["prev"]["cost"] == 0.9
    detail = c.get("/api/routines/apir").json()
    assert detail["spend"]["current"]["cost"] == 2.0
    # the uncensored-referral audit rides the same durable stream
    assert detail["referrals_total"] == 3
    assert detail["spend"]["current"]["referrals"] == 1


def test_routine_card_flags_decision_backlog(client):
    c, tmp = client
    pending = tmp / "routines" / "apir" / "questions" / "pending"
    for i in range(6):   # one past DEFERRED_BACKLOG_N
        atomic_write_json(pending / f"q-p{i}.json",
                          {"qid": f"q-p{i}", "question": f"Q{i}?", "options": [],
                           "asked": "20260707", "mode": "deferred"})
    card = next(x for x in c.get("/api/routines").json() if x["slug"] == "apir")
    assert card["decision_backlog"] is True
    assert card["open_questions"] == 6


def test_routine_card_open_questions_excludes_snoozed(client):
    """A snoozed-into-the-future question waits silently — the card's open-question count
    must exclude it, exactly like the Decisions badge/page do, so the two surfaces never
    disagree (a card showing "1 open question" with no matching badge is the bug)."""
    c, tmp = client
    pending = tmp / "routines" / "apir" / "questions" / "pending"
    atomic_write_json(pending / "q-live.json",
                      {"qid": "q-live", "question": "still open?", "options": [],
                       "asked": "20260707", "mode": "deferred"})
    atomic_write_json(pending / "q-snoozed.json",
                      {"qid": "q-snoozed", "question": "later?", "options": [],
                       "asked": "20260707", "mode": "deferred",
                       "snoozed_until": "2099-01-01T00:00:00+00:00"})
    card = next(x for x in c.get("/api/routines").json() if x["slug"] == "apir")
    assert card["open_questions"] == 1   # the snoozed one is not counted


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
                                          "last_run": None, "pending_feedback": [],
                                          "answered_decisions": []}

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
    """The system_model is a catalog model NAME; the API keeps it from dangling."""
    c, tmp = client
    assert c.get("/api/status").json()["llm_ready"] is True          # fixture: system_model → m
    # the system_model must name a catalog model
    assert c.put("/api/settings/system-model", json={"name": "nope"}).status_code == 400
    # add a second catalog model and point the system model at it
    assert c.post("/api/settings/models",
                  json={"name": "x", "endpoint": "dummy", "model": "x-id"}).status_code == 200
    r = c.put("/api/settings/system-model", json={"name": "x"})
    assert r.status_code == 200 and r.json()["system_model"] == "x"
    assert yaml.safe_load((tmp / "config.yaml").read_text())["system_model"] == "x"
    # the current system model can't be deleted, nor its endpoint while catalog models use it
    assert c.delete("/api/settings/models/x").status_code == 400
    assert c.delete("/api/settings/endpoints/dummy").status_code == 400
    # reassign, then the freed model deletes cleanly and the instance stays ready
    assert c.put("/api/settings/system-model", json={"name": "m"}).status_code == 200
    assert c.delete("/api/settings/models/x").status_code == 200
    assert c.get("/api/status").json()["llm_ready"] is True


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


def test_settings_models_crud(client):
    c, tmp = client
    # the fixture seeds one catalog model "m" on endpoint "dummy", and it's the system model
    listing = c.get("/api/settings/endpoints").json()
    assert [m["name"] for m in listing["models"]] == ["m"] and listing["system_model"] == "m"
    # add a multimodal model with an explicit context window + effort
    r = c.post("/api/settings/models", json={
        "name": "gpt4o", "endpoint": "dummy", "model": "openai/gpt-4o",
        "multimodal": True, "context_chars": 512_000, "effort": "high"})
    assert r.status_code == 200
    raw = yaml.safe_load((tmp / "config.yaml").read_text())["models"]["gpt4o"]
    assert raw == {"endpoint": "dummy", "model": "openai/gpt-4o",
                   "multimodal": True, "context_chars": 512_000, "effort": "high"}
    gv = next(m for m in c.get("/api/settings/models").json()["models"] if m["name"] == "gpt4o")
    assert gv["multimodal_effective"] is True and gv["context_effective"] == 512_000
    # a model on an unknown endpoint is rejected
    assert c.post("/api/settings/models",
                  json={"name": "bad", "endpoint": "ghost", "model": "x"}).status_code == 400
    # unset attrs inherit the endpoint kind default: a plain openai model is text-only
    c.post("/api/settings/models", json={"name": "plain", "endpoint": "dummy", "model": "glm"})
    pv = next(m for m in c.get("/api/settings/models").json()["models"] if m["name"] == "plain")
    assert pv["multimodal"] is None and pv["multimodal_effective"] is False
    # delete a non-system model cleanly
    assert c.delete("/api/settings/models/gpt4o").status_code == 200
    assert "gpt4o" not in {m["name"] for m in c.get("/api/settings/models").json()["models"]}


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


def test_endpoint_credentials_env_preserved_on_edit(client):
    """Regression: editing a claude-cli endpoint must not wipe a custom credentials_env /
    key_env_file. A full-replace PUT that omits them used to reset the token path to the
    default; both are now in the preserve list and survive an unrelated edit."""
    c, tmp = client

    def stored():
        return yaml.safe_load((tmp / "config.yaml").read_text())["endpoints"]["cc"]

    assert c.post("/api/settings/endpoints", json={
        "name": "cc", "kind": "claude-cli", "credentials_env": "~/.creds/custom.env"}).status_code == 200
    assert stored()["credentials_env"] == "~/.creds/custom.env"
    # an edit that omits credentials_env preserves it (the bug was it fell through)
    assert c.put("/api/settings/endpoints/cc", json={
        "name": "cc", "kind": "claude-cli", "base_url": "http://x/v1"}).status_code == 200
    assert stored()["credentials_env"] == "~/.creds/custom.env"
    view = next(e for e in c.get("/api/settings/endpoints").json()["endpoints"] if e["name"] == "cc")
    assert view["credentials_env"] == "~/.creds/custom.env"   # surfaced for the UI


def test_endpoint_extra_body_set_view_and_preserved(client):
    """extra_body (openai provider routing) is settable, surfaced in the view, preserved
    across an unrelated edit, and clearable by sending {}."""
    c, tmp = client

    def stored():
        return yaml.safe_load((tmp / "config.yaml").read_text())["endpoints"]["dummy"]

    assert c.put("/api/settings/endpoints/dummy", json={
        "name": "dummy", "kind": "openai", "base_url": "http://x/v1",
        "extra_body": {"provider": {"ignore": ["foo"]}}}).status_code == 200
    assert stored()["extra_body"] == {"provider": {"ignore": ["foo"]}}
    view = next(e for e in c.get("/api/settings/endpoints").json()["endpoints"] if e["name"] == "dummy")
    assert view["extra_body"] == {"provider": {"ignore": ["foo"]}}
    # an omitting edit keeps it; an explicit {} clears it
    c.put("/api/settings/endpoints/dummy", json={"name": "dummy", "kind": "openai", "base_url": "http://y/v1"})
    assert stored()["extra_body"] == {"provider": {"ignore": ["foo"]}}
    c.put("/api/settings/endpoints/dummy",
          json={"name": "dummy", "kind": "openai", "base_url": "http://y/v1", "extra_body": {}})
    assert stored()["extra_body"] == {}


def test_settings_server_config(client):
    """The runtime server knobs (sandbox, concurrency, rescan, github client id) round-trip
    through config.yaml and the live ServerConfig, with validation."""
    c, tmp = client
    got = c.get("/api/settings/server").json()
    assert got["sandbox"] == "permissive" and got["max_concurrent_runs"] == 2
    r = c.put("/api/settings/server", json={
        "sandbox": "strict", "max_concurrent_runs": 4, "registry_rescan_s": 15,
        "github_client_id": "abc123"})
    assert r.status_code == 200 and "max_concurrent_runs" in r.json()["restart_for"]
    raw = yaml.safe_load((tmp / "config.yaml").read_text())
    assert raw["sandbox"] == "strict" and raw["max_concurrent_runs"] == 4
    assert raw["registry_rescan_s"] == 15 and raw["github_client_id"] == "abc123"
    assert c.get("/api/settings/server").json()["sandbox"] == "strict"   # live object mirrors it
    assert c.put("/api/settings/server", json={"sandbox": "bogus"}).status_code == 400
    assert c.put("/api/settings/server", json={"max_concurrent_runs": 0}).status_code == 400
    assert c.put("/api/settings/server", json={"registry_rescan_s": 0}).status_code == 400


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
    """Store → util env, but SCOPED: only the vars a util DECLARES on `secrets:` reach it;
    LLM keys stay stripped unconditionally; endpoints read their key_var from the store."""
    monkeypatch.setattr("rsched.secrets.secrets_path", lambda: tmp_path / "secrets.env")
    from rsched.secrets import set_secret
    set_secret("FOO_TOKEN", "tok")
    set_secret("BAR_TOKEN", "other")
    set_secret("OPENROUTER_KEY", "sk-or-xyz")

    from rsched import utils_lib
    home = tmp_path / "library"
    utils_lib.ensure_library(home)
    utils_lib.write_util_file(home, "declarer", (
        "# /// script\n# ///\n"
        '"""declarer — d.\n\nusage: gu declarer\nsecrets: FOO_TOKEN\ntags: t\nnet: none\n"""\n'))
    env = utils_lib._child_env(home, "declarer")
    assert env["FOO_TOKEN"] == "tok"                 # the declared credential flows through
    assert "BAR_TOKEN" not in env                    # …an UNdeclared store key does not
    assert "OPENROUTER_KEY" not in env               # …and LLM keys never reach utils

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


def test_settings_library_sync_roundtrip(client):
    """GET reflects defaults; PUT persists to config.yaml + live-patches; run-now responds
    with a contained outcome even when the library repo is not a git repo yet."""
    import subprocess

    c, tmp = client
    g = c.get("/api/settings/library-sync").json()
    assert g["enabled"] is False and g["schedule_friendly"]["frequency"] == "daily"
    r = c.put("/api/settings/library-sync",
              json={"enabled": True,
                    "schedule": {"friendly": {"frequency": "hourly", "minute": 30}}})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert r.json()["cron"] == "30 * * * *"
    raw = yaml.safe_load((tmp / "config.yaml").read_text())
    assert raw["library_sync"]["enabled"] is True and raw["library_sync"]["cron"] == "30 * * * *"
    bad = c.put("/api/settings/library-sync",
                json={"schedule": {"friendly": {"frequency": "hourly", "minute": 99}}})
    assert bad.status_code == 400
    lib = tmp / "library"
    lib.mkdir(exist_ok=True)
    subprocess.run(["git", "-C", str(lib), "init", "-q", "-b", "main"], check=True)
    out = c.post("/api/settings/library-sync/run").json()
    assert out["status"] == "ok", out
    assert c.get("/api/settings/library-sync").json()["last"]["status"] == "ok"


def test_status_reports_version_and_build(client):
    from rsched import __version__

    c, _ = client
    s = c.get("/api/status").json()
    assert s["version"] == __version__
    assert isinstance(s["build"], str)            # commit stamp of the running checkout
    assert "library_sync_next" in s


def test_patch_improve_flag(client):
    """Inclusion in the routine-improver's passes is the DEFAULT; `improve: false` opts out."""
    c, tmp = client
    assert c.get("/api/routines/apir").json()["improve"] is True
    r = c.patch("/api/routines/apir", json={"improve": False})
    assert r.status_code == 200
    raw = yaml.safe_load((tmp / "routines" / "apir" / "routine.yaml").read_text())
    assert raw["improve"] is False
    assert c.get("/api/routines/apir").json()["improve"] is False


def test_put_util_rejects_bad_header(client):
    """The web util editor enforces the same doc standard as write_util: no tags or an
    undeclared credential env var -> 422, nothing written."""
    c, tmp = client
    bad = ('"""x — no tags here.\n\nusage: gu x\n"""\n'
           'import os\nk = os.environ["SOME_API_KEY"]\n')
    r = c.put("/api/library/utils/x", json={"content": bad})
    assert r.status_code == 422
    assert "tags" in r.json()["detail"] and "SOME_API_KEY" in r.json()["detail"]
    assert not (tmp / "library" / "utils" / "x").exists()


def test_workflow_delete_and_no_proposals_flow(client):
    """Workflows are edited and DELETED, never accepted: DELETE removes + commits, and the
    retired proposals endpoints are gone."""
    c, tmp = client
    wf_dir = tmp / "library" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "doomed.py").write_text("META = {}\n")
    r = c.delete("/api/workflows/doomed")
    assert r.status_code == 200
    assert not (wf_dir / "doomed.py").exists()
    assert c.delete("/api/workflows/doomed").status_code == 404
    assert c.get("/api/proposals").status_code == 404          # flow retired


def test_clarify_instruction_workflow_is_undeletable(client):
    """The new-routine wizard runs clarify-instruction to create every routine — the API
    refuses to delete it even though every other workflow is deletable."""
    c, tmp = client
    wf_dir = tmp / "library" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "clarify-instruction.py").write_text("META = {}\n")
    r = c.delete("/api/workflows/clarify-instruction")
    assert r.status_code == 400 and "wizard" in r.json()["detail"]
    assert (wf_dir / "clarify-instruction.py").exists()


def test_library_trait_delete_and_permission_guard(client):
    """Traits are deletable (committed; routines keep their adapted copies) — permission
    docs are NOT (the capability layer's conduct surface)."""
    c, tmp = client
    traits = tmp / "library" / "traits"
    traits.mkdir(parents=True, exist_ok=True)
    (traits / "doomed.md").write_text("# trait: doomed — a goner\n\nbody\n")
    assert c.delete("/api/library/traits/doomed").status_code == 200
    assert not (traits / "doomed.md").exists()
    assert c.delete("/api/library/traits/doomed").status_code == 404

    perms = tmp / "library" / "permissions"
    perms.mkdir(parents=True, exist_ok=True)
    (perms / "keepme.md").write_text(
        "---\nrequires: {}\n---\n# permission: keepme — stays\n\nbody\n")
    r = c.delete("/api/library/permissions/keepme")
    assert r.status_code == 400 and "cannot be deleted" in r.json()["detail"]
    assert (perms / "keepme.md").exists()


def test_library_util_delete(client):
    c, tmp = client
    udir = tmp / "library" / "utils" / "doomed"
    udir.mkdir(parents=True, exist_ok=True)
    (udir / "main.py").write_text(
        '"""doomed — a goner.\n\nusage: gu doomed\ntags: [test]\n"""\n')
    assert c.delete("/api/library/utils/doomed").status_code == 200
    assert not udir.exists()
    assert c.delete("/api/library/utils/doomed").status_code == 404


def test_workflow_ref_reports_library_presence(client):
    """Provenance honesty: workflow_ref.in_library is False when the claimed origin
    pattern is not in this instance's library (and for hand-authored empty slugs)."""
    c, tmp = client
    d = c.get("/api/routines/apir").json()
    assert d["workflow_ref"]["in_library"] is False            # tmp library has no patterns
    wf_dir = tmp / "library" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / (d["workflow_ref"]["slug"] + ".py")).write_text("META = {}\n")
    assert c.get("/api/routines/apir").json()["workflow_ref"]["in_library"] is True


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


def test_finalize_refused_while_draining(client):
    """While the daemon is draining for a self-restart, new wizard builds are refused (503) so the
    drain converges — the restart waits for in-flight builds but must not keep accepting new ones."""
    c, tmp = client
    wid = ".wizard-20260101-000001"
    d = tmp / "routines" / wid
    (d / "state").mkdir(parents=True)
    atomic_write_json(d / "state" / "wizard_result.json",
                      {"refined_instruction": "do the thing", "suggested_slug": "x"})
    body = {"slug": "drainr", "name": "Drain R", "workflow_slug": "general-task",
            "friendly": {"frequency": "manual"}, "tags": ["a", "b", "c"], "run_now": False}
    c.app.state.runner.draining = True
    try:
        assert c.post(f"/api/wizard/{wid}/finalize", json=body).status_code == 503
        assert not (d / "state" / "finalize.json").exists()    # nothing started
        assert wid not in c.app.state.scheduler.wizard_builds
    finally:
        c.app.state.runner.draining = False


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
    assert det["stage"] == "chat"
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


def test_build_routine_threads_params_traits_permissions(client, monkeypatch):
    """The background build threads the picked traits/permissions + the clarifier's resolved
    params into scaffold; on failure it records the error and stays retryable."""
    import asyncio

    from rsched.web import api_wizard, wizard_store

    c, tmp = client
    wid, d = _mk_wizard(tmp / "routines", "20260710-130000",
                        result={"refined_instruction": "do the thing", "suggested_slug": "x",
                                "params": {"DELIVERABLE": "a weekly report"}})
    captured = {}

    def fake_scaffold(*a, **k):
        captured.update(k)
        raise ValueError("probe stop")
    monkeypatch.setattr(api_wizard, "scaffold", fake_scaffold)

    body = api_wizard.FinalizeBody(slug="newr", name="New R", workflow_slug="general-task",
                                   friendly={"frequency": "manual"}, tags=["a", "b", "c"],
                                   traits=["ask-policy", "ledger-discipline"],
                                   permissions=["util-authoring", "memory"], run_now=False)
    asyncio.run(api_wizard._build_routine(c.app.state, wid, d, body, wizard_store.read_result(d)))
    assert captured["traits"] == ["ask-policy", "ledger-discipline"]
    assert captured["permissions"] == ["util-authoring", "memory"]
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
    state/, so the clarifier can suggest + marry by reading one file. All patterns are included."""
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
    assert "clarify-instruction" in text              # meta patterns are candidates like any other tag now (D15)


def test_library_reports_defaults_and_both_doc_sets(client):
    """/api/library carries DEFAULT_TRAITS + DEFAULT_PERMISSIONS so pickers pre-check from
    config instead of a hard-coded frontend list — and lists traits and permissions apart."""
    from rsched.config import DEFAULT_PERMISSIONS, DEFAULT_TRAITS

    c, tmp = client
    (tmp / "library" / "workflows").mkdir(parents=True, exist_ok=True)
    lib = c.get("/api/library").json()
    assert lib["default_traits"] == list(DEFAULT_TRAITS)
    assert lib["default_permissions"] == list(DEFAULT_PERMISSIONS)
    assert isinstance(lib["traits"], list) and isinstance(lib["permissions"], list)


def test_wizard_transcript_paging_and_event_offset(client):
    """The clarify chat is tailable like a run: a paged transcript endpoint returns a byte
    offset, and /events accepts that offset — the UI's reconnect-with-resume path."""
    c, tmp = client
    wid, d = _mk_wizard(tmp / "routines", "20260710-180000")
    run_dir = d / "runs" / "20260710-180000"
    with (run_dir / "transcript.jsonl").open("w") as fh:
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


def test_revise_endpoint(client, monkeypatch):
    """Revise-recipe: on a FINISHED routine run, /revise drops the recipe-unlock marker AND
    injects the framed instruction, then resumes with reason='revise'. It refuses an active
    run and empty text."""
    c, tmp = client
    run_dir = _mk_run(tmp / "routines", "apir", "20260709-120000", "running")
    rid = "apir:20260709-120000"
    # only once the run has finished
    assert c.post(f"/api/runs/{rid}/revise",
                  json={"text": "make the report shorter"}).status_code == 409
    atomic_write_json(run_dir / "status.json", {"run_id": rid, "state": "finished"})
    resumed = {}

    async def fake_resume(cfg, ts, *, reason=""):
        resumed.update(slug=cfg.slug, ts=ts, reason=reason)
        return f"{cfg.slug}:{ts}"

    monkeypatch.setattr(c.app.state.runner, "resume", fake_resume)
    r = c.post(f"/api/runs/{rid}/revise", json={"text": "make the report shorter"})
    assert r.status_code == 200 and r.json()["run_id"] == rid
    assert resumed == {"slug": "apir", "ts": "20260709-120000", "reason": "revise"}
    # the marker unlocks recipe self-write for the resumed leg…
    assert read_json(run_dir / "revise.json")["instruction"] == "make the report shorter"
    # …and the framed directive rode the inbox
    msgs = [read_json(p)["text"] for p in (tmp / "routines" / "apir" / "inbox").glob("msg-*.json")]
    assert any("REVISE YOUR OWN RECIPE" in m and "make the report shorter" in m for m in msgs)
    assert c.post(f"/api/runs/{rid}/revise", json={"text": "  "}).status_code == 400


def test_audit_decision_answer_survives_inbox_consumption(client):
    """The D2 re-surfacing loop: a mid-run delivery consumes the feedback message
    instantly, and with the report still listing the decision open it re-entered the
    Decisions inbox — the user answered the same decision again and again. The durable
    answered-marker (audit/decisions-answered.json) keeps it hidden until a NEWER
    report explicitly lists it open again."""
    c, tmp = client
    rdir = tmp / "routines" / "self-audit"
    adir = rdir / "audit"
    adir.mkdir(parents=True)
    (rdir / "inbox").mkdir(exist_ok=True)
    atomic_write_json(adir / "report.json", {
        "generated": "2026-07-11T09:00:00+00:00",
        "findings": [],
        "decisions": [{"id": "D2", "title": "Pick a path", "detail": "context",
                       "status": "open", "options": ["A", "B"]}]})
    assert [q["qid"] for q in c.get("/api/questions").json() if q.get("meta")] == ["audit:D2"]

    # answer it → marker persisted alongside the queued message
    assert c.post("/api/questions/audit:D2/answer", json={"text": "A"}).status_code == 200
    marker = read_json(adir / "decisions-answered.json")
    assert isinstance(marker, dict) and marker.get("D2")
    assert not [q for q in c.get("/api/questions").json() if q.get("meta")]

    # a run consumes the inbox message (mid-run delivery) — the decision must STAY hidden
    for p in (rdir / "inbox").glob("msg-*.json"):
        p.unlink()
    assert not [q for q in c.get("/api/questions").json() if q.get("meta")]

    # a NEWER report listing it open again re-opens it (the routine deliberately re-asks).
    # Far-future stamp: the answered-marker is written with REAL now(), so a near-past
    # constant here turns into a time-bomb the moment the wall clock passes it.
    atomic_write_json(adir / "report.json", {
        "generated": "2099-01-01T00:00:00+00:00",
        "findings": [],
        "decisions": [{"id": "D2", "title": "Pick a path (round 2)", "detail": "new context",
                       "status": "open", "options": ["A", "B"]}]})
    assert [q["qid"] for q in c.get("/api/questions").json() if q.get("meta")] == ["audit:D2"]


def test_audit_page_reflects_answered_decision_after_consumption(client):
    """The Audit page and the Decisions page must AGREE (reviewer note: responses to
    decisions were not synced everywhere): a decision answered on the Decisions page reads
    as answered on the Audit page too (`answered_decisions`), even after a run consumes its
    inbox message. The Audit page previously reconstructed answered-state from
    pending_feedback ALONE, so the decision re-presented as open the moment a run drained
    the queued message."""
    c, tmp = client
    rdir = tmp / "routines" / "self-audit"
    adir = rdir / "audit"
    adir.mkdir(parents=True)
    (rdir / "inbox").mkdir(exist_ok=True)
    atomic_write_json(adir / "report.json", {
        "generated": "2026-07-11T09:00:00+00:00",
        "findings": [],
        "decisions": [{"id": "D2", "title": "Pick a path", "detail": "context",
                       "status": "open", "options": ["A", "B"]}]})
    # not yet answered → the Audit page carries no answered marker for it
    assert c.get("/api/audit").json()["answered_decisions"] == []

    # answer it, then a run consumes the queued feedback message (mid-run delivery)
    assert c.post("/api/questions/audit:D2/answer", json={"text": "A"}).status_code == 200
    for p in (rdir / "inbox").glob("msg-*.json"):
        p.unlink()
    # pending_feedback is now empty, yet the Audit page still knows D2 is answered
    audit = c.get("/api/audit").json()
    assert audit["pending_feedback"] == []
    assert audit["answered_decisions"] == ["D2"]

    # a NEWER report (generated after the marker) re-opens it — answered_decisions drops D2.
    # Far-future stamp: the marker is written with REAL now(), so a near-past constant would
    # turn into a time-bomb the moment the wall clock passes it.
    atomic_write_json(adir / "report.json", {
        "generated": "2099-01-01T00:00:00+00:00",
        "findings": [],
        "decisions": [{"id": "D2", "title": "Pick a path (round 2)", "detail": "new",
                       "status": "open", "options": ["A", "B"]}]})
    assert c.get("/api/audit").json()["answered_decisions"] == []


def test_post_traits_adds_and_removes_practice_modules(client):
    """The user's post-creation trait switch: the traits/ dir is the state, main.md's
    Standing-practices tail is derived from it, and an unknown slug is a 400 rather than a
    silent skip (the picker offers only real ones, so an unknown slug means a stale client).
    """
    c, tmp = client
    traits_home = tmp / "library" / "traits"
    traits_home.mkdir(parents=True, exist_ok=True)
    (traits_home / "alpha.md").write_text(
        "---\ntags: [a, b, c]\n---\n# trait: alpha — the first practice\nbody\n",
        encoding="utf-8")
    rdir = tmp / "routines" / "apir"
    r = c.post("/api/routines/apir/traits", json={"add": ["alpha"], "remove": []})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == ["alpha"] and "alpha" in r.json()["traits"]
    assert (rdir / "traits" / "alpha.md").is_file()
    assert "traits/alpha.md" in (rdir / "main.md").read_text(encoding="utf-8")
    assert c.get("/api/routines/apir").json()["traits"] == ["alpha"]
    # re-adding is a no-op, not a duplicate or an error
    assert c.post("/api/routines/apir/traits", json={"add": ["alpha"]}).json()["added"] == []
    # removal prunes the derived tail with it
    out = c.post("/api/routines/apir/traits", json={"remove": ["alpha"]})
    assert out.json()["removed"] == ["alpha"]
    assert not (rdir / "traits" / "alpha.md").exists()
    assert "traits/alpha.md" not in (rdir / "main.md").read_text(encoding="utf-8")
    bad = c.post("/api/routines/apir/traits", json={"add": ["ghost"]})
    assert bad.status_code == 400 and "ghost" in bad.text


def test_put_permissions_cascades_capabilities(client):
    """The two-layer PUT: activating a doc RAISES capabilities to cover its requires AND
    FLOORS them back to the held docs (D8) — a gated action survives only as the means of a
    held permission; the confirm level (user policy) is preserved. Round-trips into
    routine.yaml."""
    c, tmp = client
    perms_home = tmp / "library" / "permissions"
    perms_home.mkdir(parents=True, exist_ok=True)
    (perms_home / "communication.md").write_text(
        "---\ntags: [a, b, c]\nrequires:\n  utils: [discord]\n---\n"
        "# permission: communication — discord\nbody\n", encoding="utf-8")
    (perms_home / "memory.md").write_text(
        "---\ntags: [a, b, c]\nrequires:\n  actions: [memory_read, memory_write]\n---\n"
        "# permission: memory — notebook\nbody\n", encoding="utf-8")
    # ask for memory_read WITHOUT holding the memory permission → floored away (D8)
    r = c.put("/api/routines/apir/permissions",
              json={"active": ["communication", "ghost"],
                    "capabilities": {"actions": ["memory_read"], "confirm": "creations"}})
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == ["communication"]           # unknown doc slugs dropped
    assert body["capabilities"]["utils"] == ["discord"]  # activation cascade (raise)
    assert body["capabilities"]["actions"] == []         # orphan action floored (no memory perm)
    assert body["capabilities"]["confirm"] == "creations"  # user policy dial preserved
    raw = yaml.safe_load((tmp / "routines" / "apir" / "routine.yaml").read_text())
    assert raw["permissions"] == ["communication"]
    assert raw["capabilities"] == body["capabilities"]
    # holding the memory permission grants its actions (the means of that permission)
    r2 = c.put("/api/routines/apir/permissions",
               json={"active": ["communication", "memory"],
                     "capabilities": {"confirm": "creations"}})
    assert r2.status_code == 200
    caps2 = r2.json()["capabilities"]
    assert set(caps2["actions"]) == {"memory_read", "memory_write"} and caps2["utils"] == ["discord"]
    # junk capabilities from the client are a 422, not a silent drop
    bad = c.put("/api/routines/apir/permissions",
                json={"active": [], "capabilities": {"actions": "write_util"}})
    assert bad.status_code == 422


def test_library_permission_doc_requires_roundtrip(client):
    """The Library editor's structured requires: panel — GET returns the parsed mapping
    for prefill; PUT with a `requires` object merges it into the frontmatter server-side
    (authoritative for that key) and the linter still gates the result."""
    c, tmp = client
    perms_home = tmp / "library" / "permissions"
    perms_home.mkdir(parents=True, exist_ok=True)
    (perms_home / "communication.md").write_text(
        "---\ntags: [a, b, c]\nrequires:\n  utils: [discord]\n---\n"
        "# permission: communication — discord\nbody\nmore lines here\n", encoding="utf-8")
    d = c.get("/api/library/permissions/communication").json()
    assert d["requires"] == {"utils": ["discord"]}
    r = c.put("/api/library/permissions/communication",
              json={"content": d["content"], "requires": {"utils": ["discord", "zulip"]}})
    assert r.status_code == 200
    d2 = c.get("/api/library/permissions/communication").json()
    assert d2["requires"] == {"utils": ["discord", "zulip"]}
    assert "# permission: communication" in d2["content"]   # body untouched
    # a requires panel demanding a confirm level is rejected (it is user policy)
    bad = c.put("/api/library/permissions/communication",
                json={"content": d2["content"], "requires": {"confirm": "never"}})
    assert bad.status_code == 422


def test_wizard_blocking_question_listed_once(client):
    """A live blocking clarify question also has a durable pending record on disk — the
    Decisions page must list it ONCE, not twice (observed 2026-07-16: every clarify question
    showed doubled). Genuinely separate deferred records still surface."""
    c, tmp = client
    routines = tmp / "routines"
    ts = "20260711-091500"
    wid, d = _mk_wizard(routines, ts, state="waiting_user")
    atomic_write_json(d / "runs" / ts / "status.json",
                      {"run_id": f"{wid}:{ts}", "state": "waiting_user", "pid": 4242, "turn": 1,
                       "question": {"qid": f"q-{ts}-1", "question": "Which areas?"}})
    pend = d / "questions" / "pending"
    pend.mkdir(parents=True)
    atomic_write_json(pend / f"q-{ts}-1.json",
                      {"qid": f"q-{ts}-1", "question": "Which areas?", "mode": "blocking"})
    atomic_write_json(pend / f"q-{ts}-2.json",
                      {"qid": f"q-{ts}-2", "question": "Another, deferred one",
                       "mode": "deferred"})
    qs = [q for q in c.get("/api/questions").json() if q.get("wizard")]
    assert [q["qid"] for q in qs].count(f"q-{ts}-1") == 1
    assert {q["qid"] for q in qs} == {f"q-{ts}-1", f"q-{ts}-2"}
    live = next(q for q in qs if q["qid"] == f"q-{ts}-1")
    assert live["mode"] == "blocking" and live["run_state"] == "waiting_user"


def test_wizard_start_refused_while_draining(client):
    """The restart drain waits for live clarify runs — accepting a NEW one mid-drain would
    never converge, and the restart would kill it mid-conversation (2026-07-16 incident)."""
    c, tmp = client
    c.app.state.runner.draining = True
    try:
        r = c.post("/api/wizard/start", json={"draft": "a brand new routine"})
        assert r.status_code == 503
        assert not list((tmp / "routines").glob(".wizard-*"))     # no session dir created
    finally:
        c.app.state.runner.draining = False


def test_settings_model_max_tokens_and_fallbacks(client):
    """The catalog carries per-model max_tokens (audit-flagged when unset/implausible) and
    the ordered failover chain; the API validates chain entries and guards deletion."""
    c, tmp = client
    # the seeded model has no max_tokens anywhere → flagged as unset (the audit flag)
    mv = next(m for m in c.get("/api/settings/models").json()["models"] if m["name"] == "m")
    assert "unset" in mv["max_tokens_warning"]
    # fallbacks must name existing catalog models, never the model itself
    assert c.post("/api/settings/models", json={
        "name": "a", "endpoint": "dummy", "model": "a-id",
        "fallbacks": ["ghost"]}).status_code == 400
    assert c.post("/api/settings/models", json={
        "name": "a", "endpoint": "dummy", "model": "a-id",
        "fallbacks": ["a"]}).status_code == 400
    r = c.post("/api/settings/models", json={
        "name": "a", "endpoint": "dummy", "model": "a-id", "max_tokens": 32_000,
        "context_chars": 400_000, "fallbacks": ["m"]})
    assert r.status_code == 200
    raw = yaml.safe_load((tmp / "config.yaml").read_text())["models"]["a"]
    assert raw["max_tokens"] == 32_000 and raw["fallbacks"] == ["m"]
    av = next(m for m in c.get("/api/settings/models").json()["models"] if m["name"] == "a")
    assert av["max_tokens_effective"] == 32_000 and av["max_tokens_warning"] is None
    assert av["fallbacks"] == ["m"]
    # a fallback target can't be deleted while referenced; clearing the chain frees it
    c.post("/api/settings/models", json={"name": "b", "endpoint": "dummy", "model": "b-id"})
    c.put("/api/settings/models/a", json={
        "name": "a", "endpoint": "dummy", "model": "a-id", "fallbacks": ["b"]})
    assert c.delete("/api/settings/models/b").status_code == 400
    c.put("/api/settings/models/a", json={"name": "a", "endpoint": "dummy", "model": "a-id"})
    assert "fallbacks" not in yaml.safe_load(
        (tmp / "config.yaml").read_text())["models"]["a"]   # an empty chain leaves no key
    assert c.delete("/api/settings/models/b").status_code == 200
    # implausible values are flagged: too low, and larger than the context window
    c.put("/api/settings/models/a", json={
        "name": "a", "endpoint": "dummy", "model": "a-id", "max_tokens": 2000})
    av = next(m for m in c.get("/api/settings/models").json()["models"] if m["name"] == "a")
    assert "implausibly low" in av["max_tokens_warning"]
    c.put("/api/settings/models/a", json={
        "name": "a", "endpoint": "dummy", "model": "a-id", "max_tokens": 50_000})
    av = next(m for m in c.get("/api/settings/models").json()["models"] if m["name"] == "a")
    assert "exceeds" in av["max_tokens_warning"]   # 200k chars vs the 100k default window
    # an endpoint-level default satisfies the audit and is inherited as effective
    c.put("/api/settings/endpoints/dummy", json={
        "name": "dummy", "kind": "openai", "base_url": "http://127.0.0.1:1/v1",
        "max_tokens": 8192})
    mv = next(m for m in c.get("/api/settings/models").json()["models"] if m["name"] == "m")
    assert mv["max_tokens_warning"] is None and mv["max_tokens_effective"] == 8192


def test_endpoint_credential_source_labels(client, monkeypatch):
    """Settings shows WHICH credential rung is live per endpoint — labels only, never
    values — and warns when an inline key shadows a set secret (Mark's 'why isn't it
    using the store?' confusion made visible)."""
    c, _tmp = client
    # no key anywhere → none; keyless is legitimate for openai (Ollama, vLLM)
    ep = next(e for e in c.get("/api/settings/endpoints").json()["endpoints"]
              if e["name"] == "dummy")
    assert ep["key_source"]["source"] == "none" and ep["key_source"]["keyless_ok"] is True
    assert ep["key_source"]["var"] == "OPENAI_API_KEY"       # the openai kind default
    # a secret under the endpoint's key_var → the store serves it
    c.put("/api/settings/secrets", json={"key": "OPENAI_API_KEY", "value": "sk-stored"})
    ep = next(e for e in c.get("/api/settings/endpoints").json()["endpoints"]
              if e["name"] == "dummy")
    assert ep["key_source"] == {"source": "secret", "var": "OPENAI_API_KEY"}
    # an inline key WINS and shadows the set secret — flagged; values never in the response
    c.put("/api/settings/endpoints/dummy", json={
        "name": "dummy", "kind": "openai", "base_url": "http://x/v1", "api_key": "sk-inline"})
    listing = c.get("/api/settings/endpoints").json()
    ep = next(e for e in listing["endpoints"] if e["name"] == "dummy")
    assert ep["key_source"]["source"] == "inline"
    assert ep["key_source"]["shadowed_secret"] is True
    assert "sk-inline" not in str(listing) and "sk-stored" not in str(listing)
    # claude-cli: the subscription token ladder — the secrets store outranks the env file
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    c.post("/api/settings/endpoints", json={"name": "cc", "kind": "claude-cli"})
    c.put("/api/settings/secrets", json={"key": "CLAUDE_CODE_OAUTH_TOKEN", "value": "tok"})
    ep = next(e for e in c.get("/api/settings/endpoints").json()["endpoints"]
              if e["name"] == "cc")
    assert ep["key_source"] == {"source": "secret", "var": "CLAUDE_CODE_OAUTH_TOKEN"}
