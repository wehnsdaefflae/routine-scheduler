"""Routine groups (D53 Phase A): the store (rsched.groups) + its CRUD API (web.api_groups).

Phase A is the store + CRUD only — nothing fires. These tests pin the store's shape
guarantees (ordered/deduped members, on_failure vocabulary, the update tri-state) and the
API's member-existence validation against the live registry.
"""

from __future__ import annotations

from pathlib import Path

from rsched import groups

# -- store ---------------------------------------------------------------------------------

def test_store_empty_reads_as_default(tmp_path):
    home = tmp_path
    data = groups.load(home)
    assert data == {"default_on_failure": "stop", "groups": []}
    assert not groups.groups_file(home).exists()   # a pure read never writes


def test_create_preserves_order_and_dedups_members(tmp_path):
    home = tmp_path
    rec = groups.create(home, name="Morning", members=["b", "a", "b", "", "c"])
    assert rec["members"] == ["b", "a", "c"]       # order kept, dupes + blanks dropped
    assert rec["id"].startswith("grp-")
    assert rec["on_failure"] is None               # inherit by default
    assert groups.get(home, rec["id"]) == rec


def test_create_rejects_blank_name_and_bad_on_failure(tmp_path):
    home = tmp_path
    for bad in ("", "   "):
        try:
            groups.create(home, name=bad)
            raise AssertionError("expected ValueError for blank name")
        except ValueError:
            pass
    try:
        groups.create(home, name="ok", on_failure="explode")
        raise AssertionError("expected ValueError for bad on_failure")
    except ValueError:
        pass


def test_update_on_failure_tristate(tmp_path):
    home = tmp_path
    rec = groups.create(home, name="G", on_failure="continue")
    assert rec["on_failure"] == "continue"
    # omit on_failure → unchanged; rename only
    groups.update(home, rec["id"], name="G2")
    assert groups.get(home, rec["id"])["on_failure"] == "continue"
    assert groups.get(home, rec["id"])["name"] == "G2"
    # explicit None → inherit
    groups.update(home, rec["id"], on_failure=None)
    assert groups.get(home, rec["id"])["on_failure"] is None
    # explicit value → override
    groups.update(home, rec["id"], on_failure="stop")
    assert groups.get(home, rec["id"])["on_failure"] == "stop"


def test_set_default_and_delete(tmp_path):
    home = tmp_path
    assert groups.default_on_failure(home) == "stop"
    groups.set_default_on_failure(home, "continue")
    assert groups.default_on_failure(home) == "continue"
    rec = groups.create(home, name="G")
    assert groups.delete(home, rec["id"]) is True
    assert groups.delete(home, rec["id"]) is False   # idempotent
    assert groups.get(home, rec["id"]) is None
    # deleting a group leaves the default intact
    assert groups.default_on_failure(home) == "continue"


def test_load_normalizes_corrupt_shape(tmp_path):
    home = tmp_path
    from rsched.paths import atomic_write_json
    atomic_write_json(groups.groups_file(home),
                      {"default_on_failure": "nonsense",
                       "groups": [{"id": "grp-x", "name": "G", "members": ["a", "a"],
                                   "on_failure": "bad"}, "junk", 42]})
    data = groups.load(home)
    assert data["default_on_failure"] == "stop"            # bad default → built-in
    assert len(data["groups"]) == 1                          # non-dicts dropped
    assert data["groups"][0]["members"] == ["a"]            # deduped
    assert data["groups"][0]["on_failure"] is None          # bad value → inherit


# -- API -----------------------------------------------------------------------------------

def _mk(tmp_path: Path, slug: str) -> None:
    d = tmp_path / "routines" / slug
    (d / "state").mkdir(parents=True)
    (d / "inbox").mkdir()
    import yaml
    cfg = {"name": f"Test {slug}", "slug": slug, "enabled": True,
           "description": "t", "schedule": {"cron": "0 7 * * 1", "tz": "UTC", "catchup": "skip"},
           "workflow": {"library_slug": "test-flow", "library_commit": "abc123"},
           "budgets": {"max_turns": 5, "max_wall_clock_min": 5, "max_total_tokens": 1000,
                       "max_subruns": 1, "max_subrun_depth": 1, "ask_timeout_min": 1}}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    (d / "main.md").write_text("## Run flow\n1. do it\n", encoding="utf-8")
    (d / "LEDGER.md").write_text("# LEDGER\n", encoding="utf-8")


def test_api_group_lifecycle(api_client):
    client, tmp_path = api_client
    _mk(tmp_path, "alpha")
    _mk(tmp_path, "beta")

    # empty list carries the default + vocab + the routine picker
    r = client.get("/api/groups")
    assert r.status_code == 200
    body = r.json()
    assert body["default_on_failure"] == "stop"
    assert body["on_failure_vocab"] == ["stop", "continue"]
    assert body["groups"] == []
    assert {k["slug"] for k in body["known_routines"]} == {"alpha", "beta"}

    # create with real members
    r = client.post("/api/groups", json={"name": "Morning", "members": ["beta", "alpha"]})
    assert r.status_code == 200, r.text
    gid = r.json()["group"]["id"]
    assert r.json()["group"]["members"] == ["beta", "alpha"]

    # unknown member is rejected, naming the slug
    r = client.post("/api/groups", json={"name": "Bad", "members": ["ghost"]})
    assert r.status_code == 400
    assert "ghost" in r.json()["detail"]

    # patch: reorder + set on_failure override
    r = client.patch(f"/api/groups/{gid}",
                     json={"members": ["alpha", "beta"], "on_failure": "continue",
                           "set_on_failure": True})
    assert r.status_code == 200, r.text
    assert r.json()["group"]["members"] == ["alpha", "beta"]
    assert r.json()["group"]["on_failure"] == "continue"

    # set the instance default
    r = client.put("/api/groups/default", json={"default_on_failure": "continue"})
    assert r.status_code == 200
    assert client.get("/api/groups").json()["default_on_failure"] == "continue"

    # a bad default value is a 400
    assert client.put("/api/groups/default",
                      json={"default_on_failure": "explode"}).status_code == 400

    # delete, then a second delete 404s
    assert client.delete(f"/api/groups/{gid}").status_code == 200
    assert client.delete(f"/api/groups/{gid}").status_code == 404
    assert client.get("/api/groups").json()["groups"] == []
