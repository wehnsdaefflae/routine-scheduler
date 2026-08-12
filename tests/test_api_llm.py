"""POST /api/llm — the procedure-side model call (operator symmetry rule 2026-08-12):
the daemon resolves the CALLING routine's own configured model (default: its `main`
role, the recipe's model), runs the completion server-side, and records the spend in
the durable usage stream under the routine.
"""

from types import SimpleNamespace

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rsched.config import ServerConfig
from rsched.endpoints.base import Completion
from rsched.readmodels import usage_stream
from rsched.web import api_llm


class _Ep:
    def __init__(self):
        self.calls = []

    def complete(self, messages, **kw):
        self.calls.append((messages, kw))
        return Completion(text="the reply", parsed=None,
                          usage={"in": 10, "out": 5, "cost": 0.01})


class _Registry:
    def __init__(self, server):
        self.server = server

    def for_model(self, kind, models):
        _Registry.last_role = kind
        return _Registry.ep, SimpleNamespace(endpoint="main-ep", model="main-model",
                                             effort="", temperature=None, max_tokens=None)

    def for_uncensored(self, models):
        return None


def _app(tmp_path, monkeypatch):
    server = ServerConfig()
    server.routines_home = tmp_path / "routines"
    (server.routines_home / ".control").mkdir(parents=True)
    d = server.routines_home / "procr"
    d.mkdir()
    (d / "routine.yaml").write_text(yaml.safe_dump(
        {"name": "P", "slug": "procr", "enabled": True}), encoding="utf-8")
    _Registry.ep = _Ep()
    monkeypatch.setattr(api_llm, "EndpointRegistry", _Registry)
    app = FastAPI()
    app.state.server = server
    app.include_router(api_llm.router, prefix="/api")
    return TestClient(app), server


def test_llm_resolves_the_routines_default_model_and_records_spend(tmp_path, monkeypatch):
    c, server = _app(tmp_path, monkeypatch)
    r = c.post("/api/llm", json={"routine": "procr", "prompt": "judge this",
                                 "system": "be brief"})
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "the reply" and data["model"] == "main-model"
    assert _Registry.last_role == "main"                 # the recipe's default model
    messages, kw = _Registry.ep.calls[0]
    assert messages[0] == {"role": "system", "content": "be brief"}
    assert messages[1]["content"] == "judge this"
    assert kw["kind"] == "procedure_llm" and "procr" in kw["purpose"]
    # the spend landed in the durable stream under the calling routine
    rows = usage_stream.usage_records(server.routines_home)
    assert len(rows) == 1
    row = rows[0]
    assert row["routine"] == "procr" and row["workflow"] == "(procedure-llm)"
    assert row["tokens"] == 15 and row["cost"] == 0.01


def test_llm_validates_routine_and_role(tmp_path, monkeypatch):
    c, _server = _app(tmp_path, monkeypatch)
    assert c.post("/api/llm", json={"routine": "ghost", "prompt": "p"}).status_code == 404
    assert c.post("/api/llm", json={"routine": "procr", "prompt": "p",
                                    "role": "gpt-x"}).status_code == 400
    # unconfigured uncensored role is a teaching 400, not a silent fallback
    r = c.post("/api/llm", json={"routine": "procr", "prompt": "p", "role": "uncensored"})
    assert r.status_code == 400 and "uncensored" in r.json()["detail"]
