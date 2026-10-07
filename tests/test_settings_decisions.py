"""Settings → Decision endpoints (web/settings/decisions.py): CRUD over the decision catalog,
the two defaults, the guards that keep the catalog loadable, and the live probe."""

from __future__ import annotations

import yaml

from rsched.endpoints import decisions_openai
from rsched.endpoints.decisions import Decision, DecisionAnswer


def _cfg(tmp_path) -> dict:
    return yaml.safe_load((tmp_path / "config.yaml").read_text())


def _seed(c) -> None:
    assert c.post("/api/settings/decision-endpoints", json={
        "name": "predator", "protocol": "openai", "base_url": "http://box:8790/v1",
        "api_key": "tok"}).json()["problems"] == []
    assert c.post("/api/settings/decision-endpoints", json={
        "name": "or-jev", "protocol": "systemone", "base_url": "https://openrouter.ai/api/v1",
        "key_var": "OPENROUTER_API_KEY"}).json()["ok"]
    assert c.post("/api/settings/decision-models", json={
        "name": "vl8b", "endpoint": "predator", "model": "qwen3-vl-8b-instruct"}).json()["ok"]
    assert c.post("/api/settings/decision-models", json={
        "name": "jev", "endpoint": "or-jev", "model": "typesafe/jev-1.13"}).json()["ok"]


def test_the_catalog_round_trips(api_client):
    c, tmp_path = api_client
    _seed(c)
    assert c.put("/api/settings/decision-defaults", json={
        "decision_model": "jev", "decision_media_model": "vl8b"}).json()["problems"] == []
    cfg = _cfg(tmp_path)
    assert cfg["decision_endpoints"]["predator"] == {
        "protocol": "openai", "base_url": "http://box:8790/v1", "api_key": "tok",
        "timeout_s": 300}
    assert cfg["decision_models"]["jev"] == {"endpoint": "or-jev", "model": "typesafe/jev-1.13"}
    view = c.get("/api/settings/decisions").json()
    assert view["protocols"] == ["openai", "systemone"]
    assert (view["decision_model"], view["decision_media_model"]) == ("jev", "vl8b")
    eps = {e["name"]: e for e in view["endpoints"]}
    assert eps["predator"]["key_source"]["source"] == "inline"
    assert "api_key" not in eps["predator"]                      # never the value
    assert eps["or-jev"]["images"] is False
    assert eps["or-jev"]["default_base_url"] == "https://api.typesafe.ai/v1"
    models = {m["name"]: m for m in view["models"]}
    assert models["vl8b"]["images"] is True
    assert models["jev"]["images"] is False


def test_a_blank_key_keeps_the_saved_one(api_client):
    c, tmp_path = api_client
    _seed(c)
    c.put("/api/settings/decision-endpoints/predator", json={
        "name": "predator", "protocol": "openai", "base_url": "http://box:9000/v1"})
    ep = _cfg(tmp_path)["decision_endpoints"]["predator"]
    assert (ep["api_key"], ep["base_url"]) == ("tok", "http://box:9000/v1")


def test_guards_keep_the_catalog_loadable(api_client):
    c, _ = api_client
    _seed(c)
    c.put("/api/settings/decision-defaults", json={"decision_model": "jev",
                                                   "decision_media_model": "vl8b"})
    assert c.post("/api/settings/decision-endpoints", json={
        "name": "x", "protocol": "chat"}).status_code == 400
    r = c.post("/api/settings/decision-models", json={"name": "y", "endpoint": "nope",
                                                       "model": "m"})
    assert r.status_code == 400
    r = c.post("/api/settings/decision-models", json={"name": "y", "endpoint": "or-jev",
                                                       "model": "m", "multimodal": True})
    assert "text only" in r.json()["detail"]
    r = c.put("/api/settings/decision-defaults", json={"decision_media_model": "jev"})
    assert "does not take images" in r.json()["detail"]
    assert c.delete("/api/settings/decision-endpoints/or-jev").status_code == 400
    assert "default" in c.delete("/api/settings/decision-models/jev").json()["detail"]
    c.put("/api/settings/decision-defaults", json={"decision_model": "",
                                                   "decision_media_model": "vl8b"})
    assert c.delete("/api/settings/decision-models/jev").json()["ok"]
    assert c.delete("/api/settings/decision-endpoints/or-jev").json()["ok"]
    assert [e["name"] for e in c.get("/api/settings/decisions").json()["endpoints"]] == [
        "predator"]


def test_the_probe_asks_one_obvious_question(api_client, monkeypatch):
    c, _ = api_client
    _seed(c)
    asked: list = []

    def fake(self, evidence, images, questions, *, model, timeout):
        asked.append((evidence, [q.question for q in questions], model))
        return Decision([DecisionAnswer("probe", "yes_no", {"yes": 0.99, "no": 0.01},
                                        probability=0.99)], usage={"in": 42, "out": 0},
                        model="qwen3-vl-8b-instruct")

    monkeypatch.setattr(decisions_openai.OpenAIDecisions, "decide", fake)
    r = c.post("/api/settings/decision-models/vl8b/test").json()
    assert r["ok"]
    assert r["probability"] == 0.99
    assert r["usage"] == {"in": 42, "out": 0}
    assert asked[0][2] == "qwen3-vl-8b-instruct"
    assert c.post("/api/settings/decision-models/ghost/test").status_code == 404


def test_a_failed_probe_says_why(api_client, monkeypatch):
    c, _ = api_client
    _seed(c)
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)
    r = c.post("/api/settings/decision-models/jev/test").json()
    assert r["ok"] is False
    assert r["auth"] is True
    assert "no API key" in r["error"]


def test_the_routine_token_cannot_read_the_catalog(tmp_path):
    from conftest import authed_client, make_test_server
    server = make_test_server(tmp_path, routine_token="rt-token")
    with authed_client(server, token="rt-token") as c:
        assert c.get("/api/settings/decisions").status_code == 403
