"""The routine page's job routes (web/api_jobs.py): the list as the store holds it, and the
operator's one write — here, recording the routine a job WAS before a merge (`origin`), which
is what points a report for that switched-off routine at this job (rsched/recipients.py).
"""

from __future__ import annotations

from helpers import bare_routine
from rsched import jobs


def _umbrella(home):
    bare_routine(home, "umbrella")
    bare_routine(home, "old")
    doc = jobs.empty()
    doc["jobs"].append(jobs.new_job("old-work", "Old work", "", by="operator"))
    jobs.save(home / "umbrella", doc)


def test_the_operator_records_the_routine_a_job_was(api_client):
    c, tmp = api_client
    home = tmp / "routines"
    _umbrella(home)
    r = c.patch("/api/routines/umbrella/jobs/old-work", json={"origin": "old"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == ["origin"]
    assert jobs.find(jobs.load(home / "umbrella"), "old-work")["origin"] == "old"
    listed = c.get("/api/routines/umbrella/jobs").json()["jobs"]
    assert listed[0]["origin"] == "old"
    # "" clears it
    assert c.patch("/api/routines/umbrella/jobs/old-work", json={"origin": ""}).status_code == 200
    assert "origin" not in jobs.find(jobs.load(home / "umbrella"), "old-work")


def test_an_origin_must_be_another_routine_here(api_client):
    c, tmp = api_client
    _umbrella(tmp / "routines")
    for bad in ("umbrella", "no-such-routine"):
        r = c.patch("/api/routines/umbrella/jobs/old-work", json={"origin": bad})
        assert r.status_code == 422, bad
        assert "is not another routine here" in r.text
