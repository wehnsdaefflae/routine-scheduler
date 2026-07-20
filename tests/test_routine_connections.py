"""PATCH /routines/{slug} connection bindings: a known provider + account label persists to
routine.yaml (wholesale replace); an unknown provider is rejected."""

from __future__ import annotations

import yaml


def test_patch_connections_persists(api_client, make_routine):
    client, tmp_path = api_client
    make_routine(slug="cxr")
    r = client.patch("/api/routines/cxr", json={"connections": {"notion": "acme"}})
    assert r.status_code == 200, r.text
    raw = yaml.safe_load((tmp_path / "routines" / "cxr" / "routine.yaml").read_text(encoding="utf-8"))
    assert raw["connections"] == {"notion": "acme"}


def test_patch_connections_rejects_unknown_provider(api_client, make_routine):
    client, _ = api_client
    make_routine(slug="cxr2")
    bad = client.patch("/api/routines/cxr2", json={"connections": {"bogus": "x"}})
    assert bad.status_code == 400
    assert "bogus" in bad.text
