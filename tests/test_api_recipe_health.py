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

    # D78-A: an active run no longer bounces the revert with a 409 — it is QUEUED and
    # applied at run end (the recipe is untouched now; the spool holds one pending edit).
    run_dir = tmp / "routines" / "revr" / "runs" / "20260717-070000"
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json",
                      {"run_id": "revr:20260717-070000", "state": "running", "turn": 1})
    rq = c.post("/api/routines/revr/recipe/revert", json={"commit": v2})
    assert rq.status_code == 200 and rq.json().get("queued") is True
    assert "# v2" in (d / "main.md").read_text(encoding="utf-8")   # not reverted yet
    from rsched import pending_edits
    assert pending_edits.pending_count(tmp / "routines", "revr") == 1
    # drop the queued edit so the direct-apply case below starts from a clean spool
    for p in pending_edits.pending(tmp / "routines", "revr"):
        p.unlink()
    atomic_write_json(run_dir / "status.json",
                      {"run_id": "revr:20260717-070000", "state": "finished", "turn": 1})

    # the real thing: recipe restored, response names the new version commit
    r = c.post("/api/routines/revr/recipe/revert", json={"commit": v2})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["reverted"] == v2 and body["new_commit"]
    assert "# v2" not in (d / "main.md").read_text(encoding="utf-8")


def test_health_carries_the_cautions_the_run_was_held_by(api_client, make_routine):
    """`state/reminders.json` and `state/assists.json` are engine-written and were read by
    nothing a person opens: reviewing whether a caution earns its interruptions meant opening a
    file on the server. They ride the health payload because they answer the question the
    version table asks — is this routine's behaviour getting better, and what changed.
    """
    from rsched import reminders as store

    c, _tmp = api_client
    d = make_routine(slug="cautious")
    (d / "state").mkdir(exist_ok=True)
    store.save_local(d, [store.Reminder(
        id="rem-mv", regex="^util:fs-ops mv ", description="it overwrites the destination",
        scope="local", created_run="r:1",
        stats={**store.blank_stats(), "fires": 3, "could_not": 2, "did": 1})], {})
    atomic_write_json(d / "state" / "assists.json", {"git-checkpoint:pre-action": 4})

    cautions = c.get("/api/routines/cautious/health").json()["cautions"]
    row = next(r for r in cautions["reminders"] if r["id"] == "rem-mv")
    assert row["scope"] == "local" and row["stats"]["fires"] == 3
    assert row["stats"]["could_not"] == 2          # the label that says the pattern is too broad
    assert cautions["labels"] == list(store.LABELS)
    assist = next(a for a in cautions["assists"] if a["key"] == "git-checkpoint:pre-action")
    assert assist["fires"] == 4 and assist["rule"] == "git-checkpoint"


def test_a_local_reminder_can_be_deleted_and_a_curated_one_cannot(api_client, make_routine):
    """A local reminder is written by the run with NO approval — that is the whole point of the
    local rung — so this route is the user's only lever over one. Without it a bad pattern kept
    costing a turn on every match, and the only remedy was hand-editing the store.
    """
    from rsched import reminders as store

    c, _tmp = api_client
    d = make_routine(slug="forgetful")
    (d / "state").mkdir(exist_ok=True)
    store.save_local(d, [
        store.Reminder(id="rem-a", regex="^util:a", description="d", scope="local",
                       created_run="r:1", stats=store.blank_stats()),
        store.Reminder(id="rem-b", regex="^util:b", description="d", scope="local",
                       created_run="r:1", stats=store.blank_stats())], {})

    assert c.delete("/api/routines/forgetful/reminders/rem-a").json() == {"ok": True,
                                                                         "remaining": 1}
    left, _ = store.load_local(d)
    assert [r.id for r in left] == ["rem-b"]
    assert c.delete("/api/routines/forgetful/reminders/rem-a").status_code == 404
    assert c.delete("/api/routines/forgetful/reminders/not-an-id").status_code == 400
    # a curated reminder is the LIBRARY's copy — removed on the Library tab, never here
    assert c.delete("/api/routines/forgetful/reminders/rem-curated").status_code == 404
