"""The recipe-health routes over the wire: GET /routines/{slug}/health (version buckets +
regression payload) and POST /routines/{slug}/recipe/revert (recipe-only rollback behind
the no-active-run 409 guard).
"""

import subprocess

from conftest import git_in as _git
from rsched.paths import atomic_write_json


def _versioned(d):
    _git(d, "init", "-q")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "scaffold")
    (d / "main.md").write_text("# v2\n", encoding="utf-8")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "recipe: v2")
    r = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def test_health_route(api_client, make_routine):
    c, _tmp = api_client
    d = make_routine(slug="healthy")
    v2 = _versioned(d)
    r = c.get("/api/routines/healthy/health")
    assert r.status_code == 200
    h = r.json()
    assert h["tracked"] is True
    assert h["versions"][0]["commit"] == v2 and h["versions"][0]["current"] is True
    assert h["regression"]["evaluated"] is False        # no runs yet
    assert c.get("/api/routines/nope/health").status_code == 404


def test_health_route_unversioned(api_client, make_routine):
    c, _tmp = api_client
    make_routine(slug="plain")
    h = c.get("/api/routines/plain/health").json()
    assert h["tracked"] is False and h["versions"] == []


def test_revert_route_and_guards(api_client, make_routine):
    c, tmp = api_client
    d = make_routine(slug="revr")
    v2 = _versioned(d)

    # bad commit → 400 with the RecipeError text
    r = c.post("/api/routines/revr/recipe/revert", json={"commit": "0" * 40})
    assert r.status_code == 400 and "unknown commit" in r.json()["detail"]

    # active run → 409, nothing touched
    run_dir = tmp / "routines" / "revr" / "runs" / "20260717-070000"
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": "revr:20260717-070000", "state": "running", "turn": 1})
    assert c.post("/api/routines/revr/recipe/revert",
                  json={"commit": v2}).status_code == 409
    atomic_write_json(run_dir / "status.json",
                      {"run_id": "revr:20260717-070000", "state": "finished", "turn": 1})

    # the real thing: recipe restored, response names the new version commit
    r = c.post("/api/routines/revr/recipe/revert", json={"commit": v2})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["reverted"] == v2 and body["new_commit"]
    assert "# v2" not in (d / "main.md").read_text(encoding="utf-8")
