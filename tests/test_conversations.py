"""Conversations: disk scaffolding, the converse seed pattern, the API surface
(create/message/artifacts/delete + home-aware run resolution), the runner's reserved
interactive slots, and the boot-time library-doc seed sync."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from rsched import conversations as conv_mod
from rsched.config import load_routine, load_server_config
from rsched.paths import atomic_write_json
from rsched.web.app import create_app

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "library-seed"
TOKEN = "test-token"


@pytest.fixture
def server(tmp_path):
    """A ServerConfig with tmp homes and the REAL library-seed copied in (no git)."""
    lib = tmp_path / "library"
    shutil.copytree(SEED / "workflows", lib / "workflows")
    shutil.copytree(SEED / "traits", lib / "traits")
    shutil.copytree(SEED / "permissions", lib / "permissions")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "conversations_home": str(tmp_path / "conversations"),
        "libraries_home": str(lib),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "system_model": {"endpoint": "dummy", "model": "m"},
    }))
    server, problems = load_server_config(cfg_path)
    assert not problems
    (tmp_path / "routines").mkdir(exist_ok=True)
    return server


@pytest.fixture
def client(server):
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        # the API must never spawn real engine subprocesses in tests
        fired: list[tuple[str, str]] = []

        async def fake_fire(cfg, *, reason="x"):
            ts = "20260712-120000"
            run_dir = cfg.dir / "runs" / ts
            run_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(run_dir / "status.json",
                              {"run_id": f"{cfg.slug}:{ts}", "state": "running", "turn": 0})
            fired.append(("fire", cfg.slug))
            return f"{cfg.slug}:{ts}"

        async def fake_resume(cfg, ts, *, reason="x"):
            fired.append(("resume", cfg.slug))
            return f"{cfg.slug}:{ts}"

        c.app.state.runner.fire = fake_fire
        c.app.state.runner.resume = fake_resume
        c.app.state.runner.calls = fired
        yield c, server


# ---- the seed pattern + trait -----------------------------------------------------------------

def test_converse_seed_lints_clean():
    from rsched import library_docs
    from rsched.workflows.lint import lint_trait_text, lint_workflow_py

    traits = library_docs.slugs(SEED / "traits")
    assert "git-checkpoint" in traits
    src = (SEED / "workflows" / "converse.py").read_text()
    assert lint_workflow_py(src, filename="converse.py", trait_slugs=traits) == []
    raw = (SEED / "traits" / "git-checkpoint.md").read_text()
    assert lint_trait_text(raw, filename="git-checkpoint.md") == []


# ---- disk scaffolding ---------------------------------------------------------------------------

def test_create_conversation_disk_shape(server):
    d = conv_mod.create_conversation(server, slug="c-test", first_message="Fix the flaky test\nin repo X",
                                     workdir=str(server.routines_home))
    assert not (d / ".git").exists()            # unversioned by design — delete means gone
    cfg, problems = load_routine(d)
    assert cfg is not None and not problems
    assert cfg.cron == "" and cfg.budgets["max_turns"] == 10
    assert cfg.fs_write_roots and cfg.fs_read_roots
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    assert raw["kind"] == "conversation"
    main = (d / "main.md").read_text()
    assert "materialized_from" in main and "converse" in main
    assert "## Standing practices" in main and "traits/git-checkpoint.md" in main
    assert (d / "traits" / "git-checkpoint.md").exists()
    assert not (d / "traits" / "improve-bugfix.md").exists()   # improve-* are routine-improver lenses, not materialized traits
    assert (d / "instruction.md").read_text().startswith("Fix the flaky test")
    assert (d / "artifacts").is_dir() and (d / "attachments").is_dir()
    assert cfg.name == conv_mod.fallback_title("Fix the flaky test")


def test_attachment_note_and_fallback_title():
    assert conv_mod.attachment_note([]) == ""
    note = conv_mod.attachment_note(["attachments/a.png", "attachments/b.csv"])
    assert "attachments/a.png" in note and "vision" in note
    assert conv_mod.fallback_title("  \n\nHello   world\nmore") == "Hello world"
    assert len(conv_mod.fallback_title("x" * 200)) <= 61


# ---- API ----------------------------------------------------------------------------------------

def test_create_list_detail_message_delete(client):
    c, server = client
    r = c.post("/api/conversations", data={"text": "Summarize the repo"},
               files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    assert ("fire", slug) in c.app.state.runner.calls
    conv_dir = server.conversations_home / slug
    saved = list((conv_dir / "attachments").iterdir())
    assert len(saved) == 1 and saved[0].name.endswith("-notes.txt")
    instruction = (conv_dir / "instruction.md").read_text()
    assert "Summarize the repo" in instruction and "attachments/" in instruction

    items = c.get("/api/conversations").json()
    assert [i["slug"] for i in items] == [slug]
    assert items[0]["state"] == "running"

    detail = c.get(f"/api/conversations/{slug}").json()
    assert detail["title"] and detail["budgets"]["max_turns"] == 10
    perm = {p["slug"]: p for p in detail["permissions"]}
    assert perm["shell"]["active"] is False                  # off by default, one-click grant
    assert perm["run-history"]["routine_only"] is True       # greyed in the panel
    assert "git-checkpoint" in detail["traits"]

    # message to the LIVE run → inbox only (mid-run injection)
    r = c.post(f"/api/conversations/{slug}/message", data={"text": "also check the README"})
    assert r.json()["delivery"] == "mid-run"
    msgs = list((conv_dir / "inbox").glob("msg-*.json"))
    assert len(msgs) == 1

    # message to a FINISHED run → resume in place
    ts = detail["run_id"].split(":")[1]
    atomic_write_json(conv_dir / "runs" / ts / "status.json",
                      {"run_id": detail["run_id"], "state": "finished", "turn": 3})
    r = c.post(f"/api/conversations/{slug}/message", data={"text": "continue"},
               files=[("files", ("data.csv", b"a,b\n1,2", "text/csv"))])
    assert r.json()["delivery"] == "resumed"
    assert ("resume", slug) in c.app.state.runner.calls
    newest = max((conv_dir / "inbox").glob("msg-*.json"))
    assert "data.csv" in newest.read_text()

    # home-aware run resolution: the conversation's run answers on /api/runs
    (conv_dir / "runs" / ts / "transcript.jsonl").write_text("")
    assert c.get(f"/api/runs/{slug}:{ts}").status_code == 200
    assert c.get(f"/api/runs/{slug}:{ts}/transcript").status_code == 200

    r = c.delete(f"/api/conversations/{slug}")
    assert r.status_code == 200 and not conv_dir.exists()


def test_artifacts_list_and_serving(client):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "make a report"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    (conv_dir / "artifacts" / "report.md").write_text("# hi")
    (conv_dir / "artifacts" / "sub").mkdir()
    (conv_dir / "artifacts" / "sub" / "chart.html").write_text("<b>x</b>")
    arts = c.get(f"/api/conversations/{slug}/artifacts").json()
    assert {a["path"] for a in arts} == {"artifacts/report.md", "artifacts/sub/chart.html"}
    r = c.get(f"/api/conversations/{slug}/file", params={"path": "artifacts/report.md"})
    assert r.status_code == 200 and r.text == "# hi"
    assert "markdown" in r.headers["content-type"]
    # only artifacts/ and attachments/ are servable — never the recipe or state
    assert c.get(f"/api/conversations/{slug}/file",
                 params={"path": "routine.yaml"}).status_code == 400
    assert c.get(f"/api/conversations/{slug}/file",
                 params={"path": "artifacts/../routine.yaml"}).status_code in (400, 404)


def test_create_conversation_accepts_prestart_budgets(client):
    c, server = client
    r = c.post("/api/conversations",
               data={"text": "bounded task", "max_turns": "5", "max_total_turns": "40"})
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    raw = yaml.safe_load((server.conversations_home / slug / "routine.yaml").read_text())
    assert raw["budgets"]["max_turns"] == 5            # per-reply cap
    assert raw["budgets"]["max_total_turns"] == 40     # whole-conversation cap
    # blank fields keep the conversation defaults; a non-numeric budget is a 400
    slug2 = c.post("/api/conversations", data={"text": "plain"}).json()["slug"]
    raw2 = yaml.safe_load((server.conversations_home / slug2 / "routine.yaml").read_text())
    assert raw2["budgets"]["max_turns"] == 10 and raw2["budgets"]["max_total_turns"] == -1
    assert c.post("/api/conversations", data={"text": "x", "max_turns": "lots"}).status_code == 400


def test_conversation_phase_mapping():
    for s in ("running", "queued", "starting"):
        assert conv_mod.conversation_phase(s) == "working"
    for s in ("finished", "failed", "aborted", "waiting_user", "new", None):
        assert conv_mod.conversation_phase(s) == "waiting for you"


def test_conversation_stategraph_reflects_run_state(client):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "do a thing"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    ts = "20260712-120000"
    # fake_fire wrote status.json state=running → the diagram lights "working"
    g = c.get(f"/api/conversations/{slug}/stategraph").json()
    assert [s["name"] for s in g["states"]] == ["working", "waiting for you"]
    assert g["current"] == "working"
    # a finished reply → it is the user's turn again ("waiting for you")
    atomic_write_json(conv_dir / "runs" / ts / "status.json",
                      {"run_id": f"{slug}:{ts}", "state": "finished", "turn": 2})
    assert c.get(f"/api/conversations/{slug}/stategraph").json()["current"] == "waiting for you"


def test_patch_and_permissions(client):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    ts = "20260712-120000"
    atomic_write_json(conv_dir / "runs" / ts / "status.json",
                      {"run_id": f"{slug}:{ts}", "state": "finished", "turn": 1})
    r = c.patch(f"/api/conversations/{slug}",
                json={"title": "My repo work", "tags": ["repo", "ci"],
                      "workdir": "~/projects/x", "budgets": {"max_turns": 20}})
    assert r.status_code == 200, r.text
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    assert raw["name"] == "My repo work" and raw["tags"] == ["repo", "ci"]
    assert raw["fs_write_roots"] == ["~/projects/x"] and raw["budgets"]["max_turns"] == 20
    r = c.put(f"/api/conversations/{slug}/permissions",
              json={"active": ["memory", "shell", "not-a-permission"]})
    assert r.json()["active"] == ["memory", "shell"]


def test_delete_guarded_while_active(client):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    # the fake fire leaves the run 'running' → the conversation counts as active
    assert c.delete(f"/api/conversations/{slug}").status_code == 409
    assert (server.conversations_home / slug).exists()


def test_settings_editable_while_active(client):
    """Budgets AND permissions retune at any time on a live conversation — each reply reads
    routine.yaml at its own boot, so the edit simply lands on the NEXT reply (no 409)."""
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    # fake fire leaves the run 'running' → the conversation counts as active
    assert c.patch(f"/api/conversations/{slug}",
                   json={"budgets": {"max_turns": -1}}).status_code == 200
    r = c.put(f"/api/conversations/{slug}/permissions", json={"active": ["memory"]})
    assert r.status_code == 200 and r.json()["active"] == ["memory"]


def test_capabilities_floored_to_held_permissions(client):
    """D8: a gated capability is only the MEANS of a held permission. Asking for write_util
    with no permission held floors it away; holding util-authoring grants it (and the
    confirm level — user policy — is preserved)."""
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    r = c.put(f"/api/conversations/{slug}/permissions",
              json={"active": [], "capabilities": {"actions": ["write_util"], "confirm": "never"}})
    assert r.status_code == 200, r.text
    assert r.json()["capabilities"]["actions"] == []          # orphan capability floored away
    r = c.put(f"/api/conversations/{slug}/permissions",
              json={"active": ["util-authoring"],
                    "capabilities": {"actions": [], "confirm": "creations"}})
    assert r.status_code == 200, r.text
    caps = r.json()["capabilities"]
    assert "write_util" in caps["actions"] and caps["confirm"] == "creations"


def test_answering_a_finished_conversation_resumes_it(client):
    """F39: a conversation is a one-shot run with no scheduled next run — answering a
    deferred decision on a FINISHED conversation must resume it in place so the queued
    answer is actually consumed (else it sits 'answered · queued' forever)."""
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    ts = "20260712-120000"
    # the single run has finished (terminal) and left a deferred pending decision
    atomic_write_json(conv_dir / "runs" / ts / "status.json",
                      {"run_id": f"{slug}:{ts}", "state": "finished", "turn": 3})
    qid = "q-decide"
    atomic_write_json(conv_dir / "questions" / "pending" / f"{qid}.json",
                      {"qid": qid, "question": "Which way?", "mode": "deferred", "asked": ts})
    qs = c.get("/api/questions").json()
    assert any(q["qid"] == qid and q.get("conversation") for q in qs)
    r = c.post(f"/api/questions/{qid}/answer", json={"text": "left"})
    assert r.status_code == 200, r.text
    assert r.json().get("resumed") is True
    assert ("resume", slug) in c.app.state.runner.calls        # the finished run was woken
    assert (conv_dir / "inbox" / f"answer-{qid}.json").exists()


def test_conversation_questions_reach_decisions(client):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    atomic_write_json(conv_dir / "questions" / "pending" / "q-1.json",
                      {"qid": "q-1", "question": "Which branch?", "options": [],
                       "asked": "20260712-120000", "mode": "deferred", "type": "question"})
    qs = c.get("/api/questions").json()
    match = next(q for q in qs if q["qid"] == "q-1")
    assert match["conversation"] is True and match["routine"] == slug
    r = c.post("/api/questions/q-1/answer", json={"text": "main"})
    assert r.status_code == 200
    assert (conv_dir / "inbox" / "answer-q-1.json").exists()


# ---- detached background tasks ------------------------------------------------------------------


def _bg_task(server, taskid, owner_slug, *, state="running", pid=999999):
    """A detached task dir under background_home owned by owner_slug (a dead pid by default)."""
    d = server.background_home / taskid
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "slug": taskid, "name": "scrape", "enabled": True,
        "schedule": {"cron": "", "tz": "Europe/Berlin", "catchup": "skip"},
        "workflow": {"library_slug": "general-task", "library_commit": ""},
        "owner": {"slug": owner_slug, "dir": str(server.conversations_home / owner_slug)},
    }))
    ts = "20260712-130000"
    rd = d / "runs" / ts
    rd.mkdir(parents=True)
    atomic_write_json(rd / "status.json", {"run_id": f"{taskid}:{ts}", "state": state, "pid": pid})
    (rd / "result.md").write_text("scrape done")
    return d


def test_launch_background_writes_request(client):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    r = c.post(f"/api/conversations/{slug}/background",
               data={"prompt": "scrape 200 pages", "workflow": "general-task", "label": "scrape"})
    assert r.status_code == 200, r.text
    taskid = r.json()["taskid"]
    req = server.background_home / ".requests" / f"{taskid}.json"
    body = yaml.safe_load(req.read_text())   # json is valid yaml
    assert body["owner"] == {"slug": slug, "dir": str(server.conversations_home / slug)}
    assert body["prompt"] == "scrape 200 pages" and body["workflow"] == "general-task"
    assert c.post(f"/api/conversations/{slug}/background", data={"prompt": "  "}).status_code == 400


def test_list_background(client):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    _bg_task(server, f"bg-{slug}-aaaa", slug)
    _bg_task(server, "bg-other-bbbb", "someone-else")   # not owned by this conversation
    items = c.get(f"/api/conversations/{slug}/background").json()
    assert [i["taskid"] for i in items] == [f"bg-{slug}-aaaa"]
    assert items[0]["state"] == "running" and items[0]["label"] == "scrape"


def test_cancel_background(client, monkeypatch):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    _bg_task(server, f"bg-{slug}-cccc", slug)
    aborted = []

    async def fake_abort(taskid):
        aborted.append(taskid)
        return True

    c.app.state.runner.abort = fake_abort
    r = c.post(f"/api/conversations/{slug}/background/bg-{slug}-cccc/cancel")
    assert r.status_code == 200 and r.json()["cancelled"] is True
    assert aborted == [f"bg-{slug}-cccc"]
    # a task the conversation does not own → 404
    _bg_task(server, "bg-foreign-dddd", "other-conv")
    assert c.post(f"/api/conversations/{slug}/background/bg-foreign-dddd/cancel").status_code == 404
    assert c.post(f"/api/conversations/{slug}/background/nope/cancel").status_code == 404


def test_background_run_resolves_on_runs_endpoint(client):
    """The _run_dir search tuple includes background_home, so a detached run's transcript/tree
    resolve on the generic /api/runs endpoints (what the rail's task tree fetches)."""
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    task = _bg_task(server, f"bg-{slug}-ffff", slug)
    (task / "runs" / "20260712-130000" / "transcript.jsonl").write_text("")
    rid = f"bg-{slug}-ffff:20260712-130000"
    assert c.get(f"/api/runs/{rid}").status_code == 200
    assert c.get(f"/api/runs/{rid}/tree").status_code == 200
    # the conversation detail carries its background list for the rail's first paint
    detail = c.get(f"/api/conversations/{slug}").json()
    assert [t["taskid"] for t in detail["background"]] == [f"bg-{slug}-ffff"]


def test_delete_conversation_tears_down_background(client):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    ts = "20260712-120000"
    atomic_write_json(conv_dir / "runs" / ts / "status.json",   # finish the reply so delete isn't 409
                      {"run_id": f"{slug}:{ts}", "state": "finished", "turn": 1})
    task = _bg_task(server, f"bg-{slug}-eeee", slug)   # dead pid → abort falls through, then rmtree
    assert c.delete(f"/api/conversations/{slug}").status_code == 200
    assert not task.exists() and not conv_dir.exists()


# ---- runner + registry + bootstrap ---------------------------------------------------------------

def test_runner_reserved_interactive_slots(server):
    from rsched.daemon.events import EventBus
    from rsched.daemon.runner import INTERACTIVE_SLOTS, Runner

    async def check():
        runner = Runner(server, EventBus())
        conv = conv_mod.create_conversation(server, slug="c-slot", first_message="hi")
        ccfg, _ = load_routine(conv)
        routine_dir = server.routines_home / "r-slot"
        (routine_dir / "state").mkdir(parents=True)
        (routine_dir / "routine.yaml").write_text(yaml.safe_dump(
            {"slug": "r-slot", "description": "d"}))
        (routine_dir / "instruction.md").write_text("x")
        (routine_dir / "main.md").write_text("x")
        rcfg, _ = load_routine(routine_dir)
        assert runner._sem_for(ccfg) is runner.interactive_semaphore
        assert runner._sem_for(rcfg) is runner.semaphore
        assert runner.interactive_semaphore._value == INTERACTIVE_SLOTS

    asyncio.run(check())


def test_registry_scan_conversations_home(server):
    conv_mod.create_conversation(server, slug="c-scan", first_message="hello there")
    from rsched.daemon import registry

    assert "c-scan" not in registry.scan(server)                     # not a routine
    catalog = registry.scan(server, server.conversations_home)
    assert set(catalog) == {"c-scan"}
    assert catalog["c-scan"].cfg.cron == ""


def test_sync_seed_library_docs(tmp_path):
    from rsched.bootstrap import sync_seed_library_docs

    lib = tmp_path / "lib"
    (lib / "workflows").mkdir(parents=True)
    (lib / "traits").mkdir()
    (lib / "traits" / "ask-policy.md").write_text("local edit — must survive")
    n = sync_seed_library_docs(lib)
    assert n > 0
    assert (lib / "workflows" / "converse.py").exists()
    assert (lib / "traits" / "git-checkpoint.md").exists()
    assert (lib / "traits" / "ask-policy.md").read_text() == "local edit — must survive"
    assert sync_seed_library_docs(lib) == 0                          # idempotent


def test_conversation_runs_end_to_end(server, scripted):
    """The materialized converse recipe drives a real engine run: work lands in
    artifacts/, the reply is the finish summary, and the run feeds workflow-usage."""
    from rsched.engine.runtime import run_routine

    d = conv_mod.create_conversation(server, slug="c-run", first_message="write me a haiku file")
    scripted([
        {"say": "Writing the artifact.", "kind": "write_file",
         "path": "artifacts/haiku.md", "content": "silent scheduler"},
        {"say": "Replying.", "kind": "finish", "status": "ok",
         "summary": "Wrote artifacts/haiku.md — a haiku about the scheduler."},
    ])
    status, run_dir = run_routine(d, server)
    assert status == "ok"
    assert (d / "artifacts" / "haiku.md").read_text() == "silent scheduler"
    assert not (d / ".git").exists()          # the finish autocommit no-ops: unversioned
    usage = (server.routines_home / ".control" / "workflow-usage.jsonl").read_text()
    assert '"converse"' in usage and "c-run" in usage   # conversations feed the evidence stream


def test_conversation_detach_writes_intent(server, scripted):
    """End-to-end gating: a conversation holds background-tasks by default, so a `detach` action
    passes the grant layer and drops an intent file for the DetachedManager."""
    from rsched.engine.runtime import run_routine

    d = conv_mod.create_conversation(server, slug="c-bg",
                                     first_message="scrape the whole site in the background")
    scripted([
        {"say": "Kicking off the scrape.", "kind": "detach", "workflow": "general-task",
         "label": "scrape", "prompt": "Scrape all 200 pages of example.com and summarize them."},
        {"say": "Replying.", "kind": "finish", "status": "ok",
         "summary": "Started the scrape in the background — I'll report back when it lands."},
    ])
    status, _ = run_routine(d, server)
    assert status == "ok"
    reqs = list((server.background_home / ".requests").glob("*.json"))
    assert len(reqs) == 1
    import json
    body = json.loads(reqs[0].read_text())
    assert body["owner"]["slug"] == "c-bg" and body["prompt"].startswith("Scrape all 200")
    assert body["workflow"] == "general-task" and body["label"] == "scrape"


def test_autolabel_fallback_never_raises(server):
    d = conv_mod.create_conversation(server, slug="c-label", first_message="hello world")
    conv_mod.autolabel(server, d, "hello world")   # dummy endpoint is unreachable → no-op
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    assert raw["name"] == "hello world"
