"""Playbooks: storage roundtrip, MAIN.md lint, the seed playbook, distilling a conversation into a
playbook (+ revise) via the system model, boot-time seed sync, and the library + conversation API
(picker seeding, Save/Update endpoints)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from rsched import playbooks
from rsched.config import load_server_config
from rsched.endpoints import EndpointRegistry as _RealRegistry   # captured before any monkeypatch
from rsched.web.app import create_app

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "library-seed"
TOKEN = "test-token"


@pytest.fixture
def server(tmp_path):
    """A ServerConfig with tmp homes and the REAL library-seed copied in (playbooks included)."""
    lib = tmp_path / "library"
    for kind in ("workflows", "traits", "permissions", "playbooks"):
        shutil.copytree(SEED / kind, lib / kind)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "conversations_home": str(tmp_path / "conversations"),
        "libraries_home": str(lib),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "models": {"m": {"endpoint": "dummy", "model": "m"}},
        "system_model": "m",
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

        async def fake_fire(cfg, *, reason="x"):
            ts = "20260714-120000"
            rd = cfg.dir / "runs" / ts
            rd.mkdir(parents=True, exist_ok=True)
            from rsched.paths import atomic_write_json
            atomic_write_json(rd / "status.json",
                              {"run_id": f"{cfg.slug}:{ts}", "state": "running", "turn": 0})
            return f"{cfg.slug}:{ts}"

        async def fake_resume(cfg, ts, *, reason="x"):
            return f"{cfg.slug}:{ts}"

        c.app.state.runner.fire = fake_fire
        c.app.state.runner.resume = fake_resume
        yield c, server


# ---- a scripted system model for the distill/revise inferences ---------------------------------

class _ScriptedEndpoint:
    context_chars = 200_000
    name = "scripted"
    multimodal = False

    def __init__(self, reply):
        self.reply = reply

    def complete(self, messages, *, model, schema=None, effort=None, **_):
        from rsched.endpoints.base import Completion
        return Completion(text=json.dumps(self.reply),
                          parsed=self.reply if schema else None, usage={"in": 1, "out": 1})


def _patch_system_model(monkeypatch, reply):
    from rsched.config import ModelRef, ServerConfig

    class _R(_RealRegistry):
        def __init__(self):
            super().__init__(ServerConfig())

        def get(self, name):
            return _ScriptedEndpoint(reply)

        def resolve(self, name):   # every name resolves to the scripted endpoint (no catalog)
            return self.get(name), ModelRef(endpoint="scripted", model="m", name=name or "system")

        def for_system(self):
            return self.resolve("system")

    monkeypatch.setattr("rsched.endpoints.EndpointRegistry", lambda server: _R())


_REPLY = {"slug": "clean-and-chart-a-dataset", "title": "Clean and chart a dataset",
          "when": "when you have a messy dataset to tidy and visualize",
          "tags": ["data", "charts"], "axis": "the dataset and chart type",
          "main": "## Parameters\n- `{{dataset}}` — the file to clean.\n\n## Instructions\n"
                  "1. Load and clean `{{dataset}}`.\n2. Chart it and save to artifacts/."}


# ---- storage ------------------------------------------------------------------------------------

def test_storage_roundtrip(server):
    home = server.library_home
    main = ("---\nslug: demo\ntitle: Demo\nwhen: when to use\ntags:\n- x\naxis: the thing\n"
            "updated: 2026-07-14\n---\n\n## Instructions\n1. do it\n")
    playbooks.write_playbook(home, "demo", main=main, details={"extra": "# extra\nlong stuff"})
    cat = {p["slug"]: p for p in playbooks.list_playbooks(home)}
    assert cat["demo"]["title"] == "Demo" and cat["demo"]["when"] == "when to use"
    assert cat["demo"]["details"] == ["extra.md"]
    pb = playbooks.read_playbook(home, "demo")
    assert "## Instructions" in pb["body"] and pb["details"]["extra.md"].startswith("# extra")
    # a MAIN-only rewrite (details=None) leaves detail files untouched
    playbooks.write_playbook(home, "demo", main=pb["content"].replace("do it", "do it well"))
    assert playbooks.read_playbook(home, "demo")["details"]["extra.md"].startswith("# extra")
    # unique_slug never clobbers
    assert playbooks.unique_slug(home, "demo") == "demo-2"
    assert playbooks.delete_playbook(home, "demo")
    assert playbooks.read_playbook(home, "demo") is None


def test_read_detail_rejects_traversal(server):
    home = server.library_home
    playbooks.write_playbook(home, "d", main="---\nslug: d\n---\n\n## Instructions\n1. x",
                             details={"note": "hi"})
    assert playbooks.read_detail(home, "d", "note.md") == "hi\n"
    assert playbooks.read_detail(home, "d", "../../routine.yaml") is None


# ---- lint + the seed playbook -------------------------------------------------------------------

def test_seed_playbook_lints_clean():
    from rsched.workflows.lint import lint_playbook_text
    main = (SEED / "playbooks" / "research-and-report" / "MAIN.md").read_text()
    assert lint_playbook_text(main, filename="research-and-report/MAIN.md") == []


def test_lint_catches_problems():
    from rsched.workflows.lint import lint_playbook_text
    probs = lint_playbook_text("---\ntitle: X\ntags: []\n---\nno instructions here",
                               filename="x/MAIN.md")
    assert any("slug" in p for p in probs)
    assert any("axis" in p for p in probs)
    assert any("tag" in p for p in probs)
    assert any("Instructions" in p for p in probs)


# ---- seed sync (reaches existing instances at boot) --------------------------------------------

def test_sync_installs_playbook_subfolders(tmp_path):
    from rsched.bootstrap import sync_seed_library_docs
    lib = tmp_path / "lib"
    (lib / "workflows").mkdir(parents=True)
    (lib / "traits").mkdir()
    sync_seed_library_docs(lib)
    assert (lib / "playbooks" / "research-and-report" / "MAIN.md").exists()
    assert sync_seed_library_docs(lib) == 0                       # idempotent — never re-copies


# ---- distill + revise ---------------------------------------------------------------------------

def test_distill_and_revise(server, monkeypatch):
    from rsched import conversations as conv_mod, playbook_distill
    from rsched.workflows.lint import lint_playbook_text

    d = conv_mod.create_conversation(server, slug="c-x",
                                     first_message="Clean up the CSV in data/ and chart it")
    run = d / "runs" / "20260714-100000"
    run.mkdir(parents=True)
    (run / "transcript.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "assistant_action", "payload": {"kind": "util", "name": "xlsx-pdf", "say": "charting"}},
        {"type": "finish", "payload": {"status": "ok", "summary": "Cleaned and charted."}},
    ]) + "\n")

    _patch_system_model(monkeypatch, _REPLY)
    pb = playbook_distill.distill_playbook(server, d)
    assert pb["slug"] == "clean-and-chart-a-dataset" and pb["details"] == {}
    main_text, _ = playbook_distill.materialize(pb)
    assert lint_playbook_text(main_text, filename="x/MAIN.md") == []   # distilled playbook lints clean

    _patch_system_model(monkeypatch, {**_REPLY, "main": _REPLY["main"] + "\n3. Save chart to artifacts/."})
    pb2 = playbook_distill.revise_playbook(server, d, main_text, pb["slug"])
    assert pb2["slug"] == pb["slug"] and "artifacts/" in pb2["main_body"]


def test_distill_refuses_empty(server, monkeypatch):
    from rsched import conversations as conv_mod, playbook_distill
    conv = conv_mod.create_conversation(server, slug="c-empty", first_message="do a thing")
    _patch_system_model(monkeypatch, {**_REPLY, "main": ""})
    with pytest.raises(ValueError):
        playbook_distill.distill_playbook(server, conv)


# ---- API: library listing + conversation seeding + Save/Update ----------------------------------

def test_library_and_playbook_api(client):
    c, _ = client
    lib = c.get("/api/library").json()
    assert any(p["slug"] == "research-and-report" for p in lib["playbooks"])
    pbs = c.get("/api/playbooks").json()["playbooks"]
    assert any(p["slug"] == "research-and-report" for p in pbs)
    d = c.get("/api/playbooks/research-and-report").json()
    assert "## Instructions" in d["content"]
    assert c.get("/api/playbooks/nope").status_code == 404


def test_create_conversation_from_playbook(client):
    c, server = client
    r = c.post("/api/conversations",
               data={"text": "topic: local LLM inference", "playbook": "research-and-report"})
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    conv = server.conversations_home / slug
    instr = (conv / "instruction.md").read_text()
    assert "Research a topic" in instr and "local LLM inference" in instr   # brief + specialization
    raw = yaml.safe_load((conv / "routine.yaml").read_text())
    assert raw["playbook"]["slug"] == "research-and-report"
    assert c.get(f"/api/conversations/{slug}").json()["playbook"] == "research-and-report"


def test_create_from_playbook_without_a_message(client):
    c, server = client
    r = c.post("/api/conversations", data={"playbook": "research-and-report"})   # playbook is the brief
    assert r.status_code == 200, r.text
    instr = (server.conversations_home / r.json()["slug"] / "instruction.md").read_text()
    assert "Research a topic" in instr and "none given" in instr


def test_create_rejects_fully_empty(client):
    c, _ = client
    assert c.post("/api/conversations", data={"text": "  "}).status_code == 400


def test_save_playbook_endpoint(client, monkeypatch):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "clean a csv"}).json()["slug"]
    conv = server.conversations_home / slug
    (conv / "runs" / "20260714-120000").mkdir(parents=True, exist_ok=True)
    (conv / "runs" / "20260714-120000" / "transcript.jsonl").write_text(
        json.dumps({"type": "finish", "payload": {"status": "ok", "summary": "done"}}) + "\n")
    _patch_system_model(monkeypatch, _REPLY)
    r = c.post(f"/api/conversations/{slug}/playbook")
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "clean-and-chart-a-dataset"
    assert playbooks.read_playbook(server.library_home, "clean-and-chart-a-dataset") is not None


def test_update_playbook_endpoint(client, monkeypatch):
    c, server = client
    slug = c.post("/api/conversations",
                  data={"text": "about X", "playbook": "research-and-report"}).json()["slug"]
    conv = server.conversations_home / slug
    (conv / "runs" / "20260714-120000").mkdir(parents=True, exist_ok=True)
    (conv / "runs" / "20260714-120000" / "transcript.jsonl").write_text(
        json.dumps({"type": "finish", "payload": {"summary": "done"}}) + "\n")
    reply = {"slug": "research-and-report", "title": "Research a topic and deliver a cited report",
             "when": "when to research", "tags": ["research"], "axis": "the topic",
             "main": "## Instructions\n1. search\n2. corroborate\n3. NEW verification step"}
    _patch_system_model(monkeypatch, reply)
    r = c.put(f"/api/conversations/{slug}/playbook")
    assert r.status_code == 200, r.text
    updated = playbooks.read_playbook(server.library_home, "research-and-report")["content"]
    assert "NEW verification step" in updated


def test_update_playbook_requires_binding(client):
    c, server = client
    slug = c.post("/api/conversations", data={"text": "no playbook here"}).json()["slug"]
    assert c.put(f"/api/conversations/{slug}/playbook").status_code == 400
