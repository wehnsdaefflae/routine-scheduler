"""Settings → Machines CRUD (web/settings/machines.py): a save writes what the form sent and
keeps what it did not, the catalog name is validated, a delete names what it cannot find."""

from __future__ import annotations

import yaml

GPU = {"name": "gpu", "host": "gpu.lan", "user": "agent", "key_var": "GPU_KEY",
       "share": "/srv/shared", "description": "the GPU box", "tags": ["gpu"]}


def _saved(c) -> dict:
    return yaml.safe_load(c.app.state.server.source.read_text(encoding="utf-8"))["machines"]


def test_an_edit_keeps_the_fields_the_form_does_not_send(api_client):
    """`exclusive` (one job at a time, a QUEUE on the box) is set in config.yaml — the form
    has no control for it, so an ordinary edit never sends it. Every save used to write the
    model's default for it, so renaming a GPU box's description silently stopped its queue."""
    c, _ = api_client
    assert c.put("/api/settings/machines/gpu", json={**GPU, "exclusive": True}).status_code == 200
    r = c.put("/api/settings/machines/gpu",
              json={**GPU, "description": "the big GPU box", "share": ""})
    assert r.status_code == 200, r.text
    live = c.app.state.server.machines["gpu"]
    assert live.exclusive is True
    assert live.description == "the big GPU box"
    assert live.share == ""                     # a field the form DID send, emptied, is cleared
    raw = _saved(c)["gpu"]
    assert raw["exclusive"] is True and "share" not in raw


def test_a_new_machine_needs_a_catalog_name_host_and_user(api_client):
    c, _ = api_client
    assert c.put("/api/settings/machines/GPU", json=GPU).status_code == 400
    assert c.put("/api/settings/machines/gpu", json={**GPU, "host": " "}).status_code == 400
    assert c.put("/api/settings/machines/gpu", json=GPU).status_code == 200
    assert _saved(c)["gpu"]["host"] == "gpu.lan"


def test_delete_removes_one_and_names_a_missing_one(api_client):
    c, _ = api_client
    c.put("/api/settings/machines/gpu", json=GPU)
    assert c.delete("/api/settings/machines/gpu").status_code == 200
    assert "gpu" not in c.app.state.server.machines
    assert c.delete("/api/settings/machines/gpu").status_code == 404
