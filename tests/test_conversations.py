"""Conversations: disk scaffolding, the converse seed pattern, the API surface
(create/message/artifacts/delete + home-aware run resolution), the runner's reserved
interactive slots, and the boot-time library-doc seed sync."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import make_test_server
from rsched import conversations as conv_mod
from rsched.config import load_routine
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
    shutil.copytree(SEED / "rules", lib / "rules")
    shutil.copytree(SEED / "permissions", lib / "permissions")
    return make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"),
                            libraries_home=str(lib))


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
    from rsched.workflows.lint import lint_rule_text, lint_workflow_py

    rules = library_docs.slugs(SEED / "rules")
    assert "git-checkpoint" in rules
    src = (SEED / "workflows" / "converse.py").read_text()
    assert lint_workflow_py(src, filename="converse.py", rule_slugs=rules) == []
    raw = (SEED / "rules" / "git-checkpoint.md").read_text()
    assert lint_rule_text(raw, filename="git-checkpoint.md") == []


# ---- disk scaffolding ---------------------------------------------------------------------------

def test_create_conversation_disk_shape(server):
    d = conv_mod.create_conversation(server, slug="c-test", first_message="Fix the flaky test\nin repo X",
                                     workdir=str(server.routines_home))
    assert not (d / ".git").exists()            # unversioned by design — delete means gone
    cfg, problems = load_routine(d)
    assert cfg is not None and not problems
    assert cfg.cron == "" and cfg.budgets["max_turns"] == 40
    assert cfg.fs_write_roots and cfg.fs_read_roots
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    assert raw["kind"] == "conversation"
    main = (d / "main.md").read_text()
    assert "materialized_from" in main and "converse" in main
    assert "## Standing practices" in main and "`git-checkpoint`" in main
    assert "git-checkpoint" in raw["rules"]
    assert not (d / "rules").exists()          # the prose lives in the library, nowhere else
    assert (d / "instruction.md").read_text().startswith("Fix the flaky test")
    assert (d / "artifacts").is_dir() and (d / "attachments").is_dir()
    assert cfg.name == conv_mod.fallback_title("Fix the flaky test")


def test_attachment_note_and_fallback_title():
    assert conv_mod.attachment_note([]) == ""
    note = conv_mod.attachment_note(["attachments/a.png", "attachments/b.csv"])
    assert "attachments/a.png" in note and "view_image" in note
    # the block is prose the model reads: it names the capability, never the util the engine
    # falls back to for a non-multimodal model
    assert "vision" not in note
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
    assert detail["title"] and detail["budgets"]["max_turns"] == 40
    perm = {p["slug"]: p for p in detail["permissions"]}
    assert perm["shell"]["active"] is False                  # off by default, one-click grant
    assert perm["run-history"]["routine_only"] is True       # greyed in the panel
    assert "git-checkpoint" in detail["rules"]

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
    newest = max((conv_dir / "inbox").glob("msg-*.json"),
                 key=lambda f: f.stat().st_mtime_ns)   # names carry a uuid — mtime orders
    assert "data.csv" in newest.read_text()

    # home-aware run resolution: the conversation's run answers on /api/runs
    (conv_dir / "runs" / ts / "transcript.jsonl").write_text("")
    assert c.get(f"/api/runs/{slug}:{ts}").status_code == 200
    assert c.get(f"/api/runs/{slug}:{ts}/transcript").status_code == 200

    r = c.delete(f"/api/conversations/{slug}")
    assert r.status_code == 200 and not conv_dir.exists()


def test_conversation_patch_forbids_unknown_keys(client):
    c, _srv = client
    slug = c.post("/api/conversations", data={"text": "hello"}).json()["slug"]
    r = c.patch(f"/api/conversations/{slug}", json={"titel": "typo"})
    assert r.status_code == 422 and "titel" in str(r.json()["detail"])
    assert c.patch(f"/api/conversations/{slug}",
                   json={"title": "Real title"}).status_code == 200


def test_message_racing_a_finish_still_wakes_the_run(client, monkeypatch):
    """R108 residual (F268): the handler snapshots liveness BEFORE writing the message —
    a run that finishes inside that window used to get a 'mid-run' delivery nobody would
    ever drain. The post-write re-check must fall through to the terminal resume."""
    import rsched.web.api_conversations as api_conv

    c, server = client
    slug = c.post("/api/conversations", data={"text": "long job"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    run_id = c.get(f"/api/conversations/{slug}").json()["run_id"]
    ts = run_id.split(":")[1]        # fake_fire left it "running"

    real_save = api_conv._save_attachments

    async def finish_mid_window(conv_dir_, files):
        # the run finishes AFTER the handler's liveness snapshot, BEFORE the re-check
        atomic_write_json(conv_dir / "runs" / ts / "status.json",
                          {"run_id": run_id, "state": "finished", "turn": 5})
        return await real_save(conv_dir_, files)

    monkeypatch.setattr(api_conv, "_save_attachments", finish_mid_window)
    c.app.state.runner.calls.clear()
    r = c.post(f"/api/conversations/{slug}/message", data={"text": "one more thing"})
    assert r.status_code == 200, r.text
    assert r.json()["delivery"] == "resumed"                  # not a stranded "mid-run"
    assert ("resume", slug) in c.app.state.runner.calls
    assert list((conv_dir / "inbox").glob("msg-*.json"))      # durable for the woken run


def test_message_to_terminal_conversation_refused_while_draining(client):
    """R81: a message to a TERMINAL conversation while the daemon is DRAINING for a restart
    must be refused up front with a clear 503 — NOT filed-then-stranded (resume() refuses
    during drain and nothing re-drives the inbox after relaunch, which drove the observed 6×
    resend spam). Both the conversation /message and the run /converse endpoints must refuse,
    file no inbox message, and take no resume/fire."""
    c, server = client
    slug = c.post("/api/conversations", data={"text": "Plan the week"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    ts = c.get(f"/api/conversations/{slug}").json()["run_id"].split(":")[1]
    # the run is finished (terminal), and the daemon has entered drain for a self-update restart
    atomic_write_json(conv_dir / "runs" / ts / "status.json",
                      {"run_id": f"{slug}:{ts}", "state": "finished", "turn": 3})
    c.app.state.runner.draining = True
    c.app.state.runner.calls.clear()
    before = len(list((conv_dir / "inbox").glob("msg-*.json")))

    r = c.post(f"/api/conversations/{slug}/message", data={"text": "also add the gym"})
    assert r.status_code == 503, r.text
    assert "NOT saved" in r.json()["detail"]

    r = c.post(f"/api/runs/{slug}:{ts}/converse", data={"text": "also add the gym"})
    assert r.status_code == 503, r.text
    assert "NOT saved" in r.json()["detail"]

    # nothing filed, nothing woken — the message was refused, not stranded
    assert len(list((conv_dir / "inbox").glob("msg-*.json"))) == before
    assert c.app.state.runner.calls == []

    # once the restart is done and drain clears, the SAME message lands and resumes normally
    c.app.state.runner.draining = False
    r = c.post(f"/api/conversations/{slug}/message", data={"text": "also add the gym"})
    assert r.status_code == 200 and r.json()["delivery"] == "resumed"
    assert len(list((conv_dir / "inbox").glob("msg-*.json"))) == before + 1


def test_message_admin_token_drops_marker_only_when_valid(client, monkeypatch):
    """D63-1A: the Conversations composer's Admin toggle sends x-admin-token with the message;
    a resume of a terminal conversation carrying a VALID token drops the one-shot admin marker
    on the resumed run's dir (which the loop reads to lift capability gating), and a wrong /
    absent token leaves no marker. Mirrors the /runs/{id}/converse admin path (D62) at the
    conversation-composer endpoint. Also the first endpoint-level coverage of the admin flow."""
    from rsched.engine.admin import ADMIN_MARKER, ADMIN_TOKEN_ENV
    monkeypatch.setenv(ADMIN_TOKEN_ENV, "s3cret-admin-token")
    c, server = client
    slug = c.post("/api/conversations", data={"text": "Plan the week"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    ts = c.get(f"/api/conversations/{slug}").json()["run_id"].split(":")[1]
    run_dir = conv_dir / "runs" / ts
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"{slug}:{ts}", "state": "finished", "turn": 3})

    # no admin header → resume, but NO admin marker
    r = c.post(f"/api/conversations/{slug}/message", data={"text": "continue"})
    assert r.status_code == 200 and r.json()["delivery"] == "resumed"
    assert not (run_dir / ADMIN_MARKER).exists()

    # a WRONG token → resume, still NO marker (fail-closed)
    r = c.post(f"/api/conversations/{slug}/message", data={"text": "again"},
               headers={"x-admin-token": "wrong"})
    assert r.status_code == 200
    assert not (run_dir / ADMIN_MARKER).exists()

    # a VALID token → the one-shot admin marker is dropped on the resumed run dir
    r = c.post(f"/api/conversations/{slug}/message", data={"text": "now with admin"},
               headers={"x-admin-token": "s3cret-admin-token"})
    assert r.status_code == 200 and r.json()["delivery"] == "resumed"
    assert (run_dir / ADMIN_MARKER).exists()


def test_create_conversation_admin_token_drops_marker_only_when_valid(client, monkeypatch):
    """D66: the NEW-conversation composer's Admin toggle sends x-admin-token ON CREATE.
    Reply #1 fires on create, so a VALID token must drop the one-shot admin marker on the
    freshly-created run dir (before its engine boots); a wrong/absent token leaves none.
    Same web-layer-only check as the /message resume path (D63)."""
    from rsched.engine.admin import ADMIN_MARKER, ADMIN_TOKEN_ENV
    monkeypatch.setenv(ADMIN_TOKEN_ENV, "s3cret-admin-token")
    c, server = client

    def _run_dir(resp):
        slug = resp["slug"]
        ts = resp["run_id"].split(":", 1)[1]
        return server.conversations_home / slug / "runs" / ts

    # no admin header → created + fired, but NO admin marker
    resp = c.post("/api/conversations", data={"text": "plan a trip"}).json()
    assert not (_run_dir(resp) / ADMIN_MARKER).exists()

    # a WRONG token → created, still NO marker (fail-closed)
    resp = c.post("/api/conversations", data={"text": "again"},
                  headers={"x-admin-token": "wrong"}).json()
    assert not (_run_dir(resp) / ADMIN_MARKER).exists()

    # a VALID token → the one-shot admin marker is planted on the first run dir
    resp = c.post("/api/conversations", data={"text": "now with admin"},
                  headers={"x-admin-token": "s3cret-admin-token"}).json()
    assert (_run_dir(resp) / ADMIN_MARKER).exists()


def test_conversation_connections_binding(client):
    """D55 (closes R70): a conversation can bind an OAuth connection just like a routine —
    PATCH /conversations/{slug} accepts `connections`, the binding lands in routine.yaml (so the
    engine injects the token), the detail response echoes it, and an unknown provider is
    rejected. Before this a Google connection could be bound only on routine pages, so a
    conversation could not call connector utils (google-api)."""
    c, server = client
    slug = c.post("/api/conversations", data={"text": "read my google contacts"}).json()["slug"]
    # detail exposes the (empty) connections map so the card can render current bindings
    assert c.get(f"/api/conversations/{slug}").json()["connections"] == {}
    # bind a Google connection (existence of the account is NOT required — bind ahead of connecting)
    r = c.patch(f"/api/conversations/{slug}", json={"connections": {"google": "me@example.com"}})
    assert r.status_code == 200 and "connections" in r.json()["updated"]
    # it landed in routine.yaml (where the engine's _connection_env reads it)
    import yaml
    raw = yaml.safe_load((server.conversations_home / slug / "routine.yaml").read_text())
    assert raw["connections"] == {"google": "me@example.com"}
    # and the detail response echoes it back for the card
    assert c.get(f"/api/conversations/{slug}").json()["connections"] == {"google": "me@example.com"}
    # unknown provider is rejected, same as a routine
    assert c.patch(f"/api/conversations/{slug}",
                   json={"connections": {"nope": "x"}}).status_code == 400
    # an empty account label is rejected
    assert c.patch(f"/api/conversations/{slug}",
                   json={"connections": {"google": ""}}).status_code == 400


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


def test_artifact_delete_scoped_to_artifacts_dir(client):
    # the sidebar's delete (user order 2026-08-14): artifacts/ only — an attachment is the
    # USER'S upload and must survive every artifact delete attempt against it
    c, server = client
    slug = c.post("/api/conversations", data={"text": "make a report"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    (conv_dir / "artifacts" / "report.md").write_text("# hi")
    (conv_dir / "attachments" / "input.pdf").write_bytes(b"%PDF")
    r = c.delete(f"/api/conversations/{slug}/artifacts",
                 params={"path": "artifacts/report.md"})
    assert r.status_code == 200 and r.json()["deleted"] == "artifacts/report.md"
    assert not (conv_dir / "artifacts" / "report.md").exists()
    assert c.delete(f"/api/conversations/{slug}/artifacts",
                    params={"path": "attachments/input.pdf"}).status_code == 400
    assert (conv_dir / "attachments" / "input.pdf").exists()


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
    assert raw2["budgets"]["max_turns"] == 40 and raw2["budgets"]["max_total_turns"] == -1
    assert c.post("/api/conversations", data={"text": "x", "max_turns": "lots"}).status_code == 400


def test_conversation_defaults_endpoint(client):
    """The composer's ⚙ capabilities & budgets panel is fed by /conversations/defaults —
    the layers a NEW conversation gets, offered BEFORE create (the first reply fires on
    create, so a post-hoc toggle would miss reply #1)."""
    c, _server = client
    d = c.get("/api/conversations/defaults").json()
    perm = {p["slug"]: p for p in d["permissions"]}
    assert perm["background-tasks"]["active"] is True        # a conversation default
    assert perm["shell"]["active"] is False                  # off by default, one-click grant
    assert perm["run-history"]["routine_only"] is True       # greyed in the composer too
    assert d["budgets"]["max_turns"] == 40
    assert d["deliberation"] == "deliberate"
    assert "actions" in d["capabilities"]["active"]
    # F339: the RULES surface too — the library's rules (slug + summary, for the picker)
    # and the set a new conversation holds by default.
    assert d["library_rules"] and all(set(r) == {"slug", "summary"} for r in d["library_rules"])
    assert set(d["rules"]) <= {r["slug"] for r in d["library_rules"]}


def test_create_conversation_accepts_prestart_rules(client):
    """F339: rules are a PRE-START choice. A rule reaches the prompt through main.md's
    Standing-practices tail, which is materialized at create time — one bound afterwards
    never governs reply #1, which fires the moment the conversation is created."""
    import json

    import yaml

    c, server = client
    defaults = c.get("/api/conversations/defaults").json()
    pick = [defaults["library_rules"][0]["slug"]]
    slug = c.post("/api/conversations", data={
        "text": "ruled task", "rules": json.dumps(pick)}).json()["slug"]
    raw = yaml.safe_load(
        (server.conversations_home / slug / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["rules"] == pick
    # …and the chosen rule is actually woven into the recipe the first reply reads
    main = (server.conversations_home / slug / "main.md").read_text(encoding="utf-8")
    assert pick[0] in main


def test_create_conversation_rejects_an_unknown_rule(client):
    """A typo must not produce a conversation holding a rule with no prose — the tail would
    name a practice nobody wrote."""
    import json

    c, _server = client
    r = c.post("/api/conversations", data={
        "text": "x", "rules": json.dumps(["no-such-rule"])})
    assert r.status_code == 400 and "no such rule" in r.json()["detail"]


def test_create_conversation_rejects_an_unconnected_account(client):
    """F339: a connection bound at create must name a REALLY connected account — otherwise
    the binding fails at first use, which is the failure the picker exists to prevent."""
    import json

    c, _server = client
    r = c.post("/api/conversations", data={
        "text": "x", "connections": json.dumps({"google": "nobody"})})
    assert r.status_code == 400 and "no connected account" in r.json()["detail"]
    r2 = c.post("/api/conversations", data={
        "text": "x", "connections": json.dumps({"frobnitz": "a"})})
    assert r2.status_code == 400 and "unknown provider" in r2.json()["detail"]


def test_create_conversation_accepts_prestart_layers(client):
    """Pre-start permission layers ride the create request through the SAME resolve +
    cascade + floor as the header panel's save; deliberation lands in tuning.yaml and the
    per-reply minute/token ceilings in budgets — all governing reply #1 already."""
    import json

    c, server = client
    defaults = c.get("/api/conversations/defaults").json()
    active = [p["slug"] for p in defaults["permissions"]
              if p["active"] and not p.get("routine_only")] + ["shell"]
    r = c.post("/api/conversations", data={
        "text": "shelly task", "deliberation": "terse",
        "max_wall_clock_min": "45", "max_total_tokens": "123000",
        "permissions": json.dumps({"active": active}),
    })
    assert r.status_code == 200, r.text
    conv_dir = server.conversations_home / r.json()["slug"]
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    assert "shell" in raw["permissions"]
    assert "shell" in raw["capabilities"]["utils"]           # the requires cascade raised it
    assert raw["budgets"]["max_wall_clock_min"] == 45
    assert raw["budgets"]["max_total_tokens"] == 123000
    tuning = yaml.safe_load((conv_dir / "tuning.yaml").read_text())
    assert tuning["deliberation"] == "terse"
    # junk is rejected up front, before anything lands on disk
    assert c.post("/api/conversations",
                  data={"text": "x", "deliberation": "extreme"}).status_code == 400
    assert c.post("/api/conversations",
                  data={"text": "x", "permissions": "{not json"}).status_code == 400


def test_create_conversation_with_folder_access(client):
    """D70: the composer's folder-access roots ride the create request and land on the
    config BEFORE the engine boots — workdir first (the project dir), write roots also
    listed as read roots (kept for visible config even though the engine's read gate now
    folds write roots in itself — F294: a write grant implies read),
    everything deduped, in the same native keys an allow-forever fs grant is written to."""
    import json

    c, server = client
    r = c.post("/api/conversations", data={
        "text": "work on my data", "workdir": "~/projects/x",
        "fs_write_roots": json.dumps(["/srv/videos", "~/projects/x", "/srv/videos/"]),
        "fs_read_roots": json.dumps(["~/datasets", "/srv/videos"]),
    })
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    raw = yaml.safe_load((server.conversations_home / slug / "routine.yaml").read_text())
    assert raw["fs_write_roots"] == ["~/projects/x", "/srv/videos"]
    assert raw["fs_read_roots"] == ["~/projects/x", "/srv/videos", "~/datasets"]
    # the detail payload exposes the full lists (workdir stays write_roots[0])
    d = c.get(f"/api/conversations/{slug}").json()
    assert d["workdir"].endswith("projects/x")
    assert d["fs_write_roots"] == ["~/projects/x", "/srv/videos"]
    assert d["fs_read_roots"] == ["~/projects/x", "/srv/videos", "~/datasets"]
    # relative paths and non-JSON payloads are refused before anything lands on disk
    assert c.post("/api/conversations", data={
        "text": "x", "fs_write_roots": json.dumps(["srv/videos"])}).status_code == 400
    assert c.post("/api/conversations", data={
        "text": "x", "fs_read_roots": "not json"}).status_code == 400


def test_patch_workdir_preserves_granted_roots(client):
    """The workdir is by convention the FIRST write root; changing the project directory
    replaces that slot only — folder grants beyond it (D70 create-time roots, allow-forever
    fs decisions) survive the change instead of being wiped."""
    import json

    c, server = client
    slug = c.post("/api/conversations", data={
        "text": "t", "workdir": "~/projects/x",
        "fs_write_roots": json.dumps(["/srv/videos"])}).json()["slug"]
    conv_dir = server.conversations_home / slug
    r = c.patch(f"/api/conversations/{slug}", json={"workdir": "~/projects/y"})
    assert r.status_code == 200, r.text
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    assert raw["fs_write_roots"] == ["~/projects/y", "/srv/videos"]
    assert raw["fs_read_roots"] == ["~/projects/y", "/srv/videos"]
    # clearing the workdir keeps the granted root
    c.patch(f"/api/conversations/{slug}", json={"workdir": ""})
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    assert raw["fs_write_roots"] == ["/srv/videos"]


def test_patch_folder_access_lists(client):
    """D82: the header panel edits the FULL folder-access lists mid-conversation — PATCH
    fs_read_roots/fs_write_roots replaces wholesale (an empty list clears the grants),
    lands in routine.yaml for the NEXT reply's boot, and refuses blank entries."""
    c, server = client
    slug = c.post("/api/conversations", data={
        "text": "t", "workdir": "~/projects/x"}).json()["slug"]
    conv_dir = server.conversations_home / slug
    r = c.patch(f"/api/conversations/{slug}", json={
        "fs_read_roots": ["~/datasets", " /srv/media "],
        "fs_write_roots": ["~/projects/x", "/srv/out"]})
    assert r.status_code == 200, r.text
    assert set(r.json()["updated"]) == {"fs_read_roots", "fs_write_roots"}
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    assert raw["fs_read_roots"] == ["~/datasets", "/srv/media"]     # stripped
    assert raw["fs_write_roots"] == ["~/projects/x", "/srv/out"]
    # the detail payload reflects the saved lists (what the editors re-open on)
    d = c.get(f"/api/conversations/{slug}").json()
    assert d["fs_read_roots"] == ["~/datasets", "/srv/media"]
    assert d["fs_write_roots"] == ["~/projects/x", "/srv/out"]
    # a blank entry is refused before anything lands on disk
    assert c.patch(f"/api/conversations/{slug}",
                   json={"fs_write_roots": ["  "]}).status_code == 400
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    assert raw["fs_write_roots"] == ["~/projects/x", "/srv/out"]
    # an empty list clears the grants entirely
    assert c.patch(f"/api/conversations/{slug}",
                   json={"fs_write_roots": []}).status_code == 200
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    assert raw["fs_write_roots"] == []


def _tiny_window_model(server, name="tiny"):
    """A catalog model whose max output tokens alone fill its window (65_536 chars ≈
    16_384 tokens = the default output reservation) — the class the harness cannot run."""
    from rsched.config import ModelConfig

    server.models[name] = ModelConfig(name=name, endpoint="dummy", model="t",
                                      context_chars=65_536, max_tokens=16_384)


def test_create_refuses_model_the_harness_cannot_run(client):
    """R112/R128: name-in-catalog is not enough — a model whose window minus its output
    reservation leaves no input budget dies on its first completion, so create refuses it
    up front with a message naming the numbers and the fix."""
    c, server = client
    _tiny_window_model(server)
    r = c.post("/api/conversations", data={"text": "t", "model": "tiny"})
    assert r.status_code == 400
    assert "cannot run a single turn" in r.json()["detail"]
    assert "16,384" in r.json()["detail"]
    # a workable model still passes (the fixture's default: 100k chars, default output cap)
    assert c.post("/api/conversations", data={"text": "t", "model": "m"}).status_code == 200


def test_model_change_refuses_impossible_window(client):
    c, server = client
    _tiny_window_model(server)
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    r = c.patch(f"/api/conversations/{slug}",
                json={"models": {"main": "tiny", "tool_call": "tiny"}})
    assert r.status_code == 400
    assert "cannot run a single turn" in r.json()["detail"]
    raw = yaml.safe_load((server.conversations_home / slug / "routine.yaml").read_text())
    assert "models" not in raw                     # nothing landed on the config
    ok = c.patch(f"/api/conversations/{slug}",
                 json={"models": {"main": "m", "tool_call": "m"}})
    assert ok.status_code == 200


def test_create_conversation_accepts_per_role_models_including_honeypot(client):
    """A conversation can START with a honeypot (uncensored) role configured — the role the
    refusal machinery hands a refused request's essence to, otherwise unreachable before the
    first reply. The per-role `models` map seeds all three roles at create time; the single
    `model` shorthand only ever seeded main + tool_call."""
    c, server = client
    from rsched.config import ModelConfig
    server.models["hp"] = ModelConfig(name="hp", endpoint="dummy", model="h",
                                      context_chars=200_000, max_tokens=4_096)
    r = c.post("/api/conversations", data={
        "text": "t",
        "models": json.dumps({"main": "m", "tool_call": "m", "uncensored": "hp"}),
    })
    assert r.status_code == 200
    slug = r.json()["slug"]
    raw = yaml.safe_load((server.conversations_home / slug / "routine.yaml").read_text())
    assert raw["models"] == {"main": "m", "tool_call": "m", "uncensored": "hp"}
    # the detail endpoint round-trips all three roles
    got = c.get(f"/api/conversations/{slug}").json()["models"]
    assert got["uncensored"] == "hp" and got["main"] == "m"


def test_create_conversation_per_role_models_validated(client):
    """Per-role create models are validated exactly like the PATCH path: unknown role,
    non-catalog name, and an unrunnable window are each refused up front."""
    c, server = client
    _tiny_window_model(server)
    assert c.post("/api/conversations", data={
        "text": "t", "models": json.dumps({"bogus_role": "m"})}).status_code == 400
    assert c.post("/api/conversations", data={
        "text": "t", "models": json.dumps({"uncensored": "nope"})}).status_code == 400
    assert c.post("/api/conversations", data={
        "text": "t", "models": json.dumps({"uncensored": "tiny"})}).status_code == 400
    assert c.post("/api/conversations", data={
        "text": "t", "models": "not-json"}).status_code == 400


def test_detail_and_settings_pickers_carry_window_meta(client):
    """R128: the picker payloads expose per-model window metadata so the UI can label and
    disable — the conversation detail's catalog_meta and /api/settings/models' window
    field come from the same derivation."""
    c, server = client
    _tiny_window_model(server)
    slug = c.post("/api/conversations", data={"text": "t"}).json()["slug"]
    meta = c.get(f"/api/conversations/{slug}").json()["catalog_meta"]
    assert meta["m"]["fit"] in ("ok", "tight") and meta["m"]["input_ceiling_chars"] > 0
    assert meta["tiny"]["fit"] == "impossible"
    assert meta["tiny"]["context_tokens"] == 16_384
    by_name = {m["name"]: m for m in c.get("/api/settings/models").json()["models"]}
    assert by_name["tiny"]["window"]["fit"] == "impossible"
    assert by_name["m"]["window"] == meta["m"]


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
    c, _server = client
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
    c, _server = client
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
    from rsched import registry

    assert "c-scan" not in registry.scan(server)                     # not a routine
    catalog = registry.scan(server, server.conversations_home)
    assert set(catalog) == {"c-scan"}
    assert catalog["c-scan"].cfg.cron == ""


def test_sync_seed_library_docs(tmp_path):
    from rsched.bootstrap import sync_seed_library_docs

    lib = tmp_path / "lib"
    (lib / "workflows").mkdir(parents=True)
    (lib / "rules").mkdir()
    (lib / "rules" / "ask-policy.md").write_text("local edit — must survive")
    n = sync_seed_library_docs(lib)
    assert n > 0
    assert (lib / "workflows" / "converse.py").exists()
    assert (lib / "rules" / "git-checkpoint.md").exists()
    assert (lib / "rules" / "ask-policy.md").read_text() == "local edit — must survive"
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
    status, _run_dir = run_routine(d, server)
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


def test_autolabel_rewrites_config_atomically(server, monkeypatch):
    """autolabel rewrites routine.yaml OFF the reply path while the daemon may scan it —
    the rewrite must land whole (title + tags applied, every other key intact) and leave
    no atomic_write tmp debris behind in the conversation dir."""
    from types import SimpleNamespace

    d = conv_mod.create_conversation(server, slug="c-atomic", first_message="track my garden beds")
    before = yaml.safe_load((d / "routine.yaml").read_text())

    class FakeEndpoint:
        def complete(self, *a, **k):
            return SimpleNamespace(parsed={"title": "Garden bed tracker",
                                           "tags": ["Garden", "WEEKLY!"]}, text="")

    class FakeRegistry:
        def __init__(self, _server): ...
        def for_model(self, kind, models):
            return FakeEndpoint(), SimpleNamespace(model="m", effort=None, temperature=None, max_tokens=None)

    monkeypatch.setattr("rsched.endpoints.EndpointRegistry", FakeRegistry)
    conv_mod.autolabel(server, d, "track my garden beds")
    raw = yaml.safe_load((d / "routine.yaml").read_text())   # parses whole → no torn write
    assert raw["name"] == raw["description"] == "Garden bed tracker"
    assert raw["tags"] == ["garden", "weekly"]               # normalized lowercase slugs
    untouched = {k: v for k, v in before.items() if k not in ("name", "description", "tags")}
    assert {k: raw[k] for k in untouched} == untouched       # the rest of the config survives
    assert not list(d.glob("*.tmp"))                         # tmp file was renamed, not left


def test_autolabel_uses_the_conversations_own_model(server, monkeypatch):
    """Title + tags come from the conversation's OWN model (for_model('main', its models)),
    never the system model — so a conversation pinned to an uncensored model titles with it
    instead of a default model that might refuse the request."""
    from types import SimpleNamespace

    d = conv_mod.create_conversation(server, slug="c-mdl", first_message="hello",
                                     models={"main": "uncensored-x"})
    seen: dict = {}

    class FakeEndpoint:
        def complete(self, *a, **k):
            return SimpleNamespace(parsed={"title": "Titled thing", "tags": []}, text="")

    class FakeRegistry:
        def __init__(self, _server): ...
        def for_model(self, kind, models):
            seen["kind"], seen["models"] = kind, dict(models)
            return FakeEndpoint(), SimpleNamespace(model="m", effort=None, temperature=None, max_tokens=None)
        def for_system(self):
            raise AssertionError("autolabel must resolve the conversation's model, not the system one")

    monkeypatch.setattr("rsched.endpoints.EndpointRegistry", FakeRegistry)
    conv_mod.autolabel(server, d, "hello")
    assert seen["kind"] == "main"
    assert seen["models"] == {"main": "uncensored-x"}
    assert yaml.safe_load((d / "routine.yaml").read_text())["name"] == "Titled thing"


def test_commands_catalog_and_command_flagged_message(client):
    """The chat composer's slash-command surface: /commands serves the capability-filtered
    kinds + util catalog, and a command-flagged message lands in the inbox with the flag
    the engine executes on."""
    c, server = client
    slug = c.post("/api/conversations", data={"text": "hi there"}).json()["slug"]

    catalog = c.get(f"/api/conversations/{slug}/commands").json()
    kinds = {k["kind"] for k in catalog["kinds"]}
    assert {"util", "read_file", "write_file", "llm"} <= kinds
    assert all(k["usage"].startswith("/") for k in catalog["kinds"])
    # conversations hold the memory permission by default → memory commands offered
    assert "memory_read" in kinds

    r = c.post(f"/api/conversations/{slug}/message",
               data={"text": "/read_file instruction.md", "command": "1"})
    assert r.status_code == 200
    from rsched.paths import read_json as _rj
    msgs = sorted((server.conversations_home / slug / "inbox").glob("msg-*.json"))
    flagged = [m for m in msgs if (_rj(m) or {}).get("command")]
    assert len(flagged) == 1
    assert _rj(flagged[0])["text"] == "/read_file instruction.md"


def test_creation_floors_capabilities_like_save(server):
    """The default creation path applies the SAME raise->floor as every save: a held doc
    whose requires names a non-gated action cannot smuggle it into the persisted mapping
    (raise adds it, the floor strips it), while its reserved util survives."""
    (server.permissions_home / "leaky.md").write_text(
        "---\nrequires:\n  actions: [finish]\n  utils: [remote]\n---\n"
        "# permission: leaky\n\nA doc whose requires lists the ungated `finish` kind.\n",
        encoding="utf-8")
    d = conv_mod.create_conversation(server, slug="c-floor", first_message="hi",
                                     permissions=["leaky"])
    cfg, problems = load_routine(d)
    assert not problems and cfg is not None
    assert cfg.permissions == ["leaky"]
    assert cfg.capabilities["utils"] == ["remote"]      # required by the held doc - kept
    assert cfg.capabilities["actions"] == []            # finish is not a gated kind - floored


def test_conversation_machines_bind_and_validate(client):
    """D102 (R475/R496): a conversation binds catalog machines exactly like a routine — the
    detail payload carries the catalog for the picker, the PATCH persists into routine.yaml
    (the next reply's boot injects RSCHED_MACHINES), and an off-catalog name is a 400: that
    binding would resolve to nothing at run time, so it must fail at the click, not silently.
    """
    from rsched.config import MachineConfig

    c, server = client
    mac = MachineConfig(host="10.0.0.9", user="rs", description="RTX 4090")
    mac.name = "gpu-box"
    server.machines["gpu-box"] = mac
    slug = c.post("/api/conversations", data={"text": "train the model"}).json()["slug"]
    detail = c.get(f"/api/conversations/{slug}").json()
    assert detail["machines"] == []
    assert [m["name"] for m in detail["machine_catalog"]] == ["gpu-box"]
    r = c.patch(f"/api/conversations/{slug}", json={"machines": ["gpu-box"]})
    assert r.status_code == 200, r.text
    raw = yaml.safe_load(
        (server.conversations_home / slug / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["machines"] == ["gpu-box"]
    assert c.get(f"/api/conversations/{slug}").json()["machines"] == ["gpu-box"]
    r = c.patch(f"/api/conversations/{slug}", json={"machines": ["ghost"]})
    assert r.status_code == 400 and "Settings" in r.json()["detail"]
