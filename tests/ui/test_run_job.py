"""The run rail's JOBS section (components/runjob.js, docs/jobs.md).

OPERATOR ORDER (F646): *"routines with tasks (like the 'fau' routine) need to display the task
they're currently working on in the right sidebar"*. The run page is where a run is actually
watched; before this the open job was visible only on the ROUTINE page, which shows the store
rather than this run.

Four claims, all about what a PERSON sees:

- a live run with a job open says WHICH job, in words, with its brief;
- a job this run already checkpointed shows the outcome it recorded, not the open marker;
- a run that is not the ledger's own (an older run of the same routine) reports what IT did,
  read from the job's history — never the job another run has open now;
- a routine with no job layer gains NO section: `tests/ui/test_finish_line.py` pins the rail's
  caption list exactly, and an always-present, always-empty card teaches a reader to skip it.
"""

from __future__ import annotations

import re

import yaml
from playwright.sync_api import expect

from rsched import jobs

from .helpers import watch_requests

TS = "20261012-090000"
OTHER_TS = "20261011-090000"


def _keep_jobs(ui, *, ledger: dict, rows: list[dict], slug: str = "uir"):
    """Turn the job layer on for `slug` and write the store the way the engine leaves it."""
    d = ui.routine_dir(slug)
    cfg = yaml.safe_load((d / "routine.yaml").read_text())
    cfg["capabilities"] = {**(cfg.get("capabilities") or {}), "jobs": "on"}
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg))
    jobs.save(d, {"version": 1, "deleted": [], "run": ledger, "jobs": rows})
    return d


def _open_now(run_id: str) -> dict:
    return {"run_id": run_id, "started": "2026-10-12T09:00:00+02:00", "open": "ards",
            "due": [{"id": "ards", "why": ["its quiet days are up"]}], "done": {}}


ARDS = {"id": "ards", "title": "ARDS consulting", "state": "active",
        "brief": "the consulting engagement with the ARDS working group",
        "workspace": "jobs/ards"}


def test_a_live_run_shows_the_job_it_is_working_on(ui, ui_page):
    _keep_jobs(ui, ledger=_open_now(f"uir:{TS}"), rows=[ARDS])
    ui.seed_run("uir", TS, "running", summary="")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    row = ui_page.locator("[data-run-job='ards']")
    expect(row).to_be_visible()
    expect(row).to_contain_text("ARDS consulting")
    expect(row).to_contain_text("working on it now")
    expect(row).to_contain_text("the consulting engagement with the ARDS working group")
    # and it is a foldable rail section like every other
    expect(ui_page.locator('.run-rail .rail-cap[data-rail="jobs"]')).to_be_visible()


def test_a_job_this_run_checkpointed_shows_its_outcome(ui, ui_page):
    ledger = {"run_id": f"uir:{TS}", "open": "", "due": [{"id": "ards", "why": ["due"]}],
              "done": {"ards": "advanced"}}
    _keep_jobs(ui, ledger=ledger, rows=[{
        **ARDS,
        "last": {"run": f"uir:{TS}", "at": "2026-10-12T09:30:00+02:00", "outcome": "advanced",
                 "summary": "AP 3.4 revised and sent"}}])
    ui.seed_run("uir", TS, "finished", summary="done")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    row = ui_page.locator("[data-run-job='ards']")
    expect(row).to_contain_text("advanced")
    expect(row).to_contain_text("AP 3.4 revised and sent")
    expect(row).not_to_contain_text("working on it now")


def test_an_older_run_reports_what_it_did_not_what_is_open_now(ui, ui_page):
    """The store is the ROUTINE's; the rail is a RUN's. A page opened on yesterday's run must
    not claim the job today's run has open — it reports the entry its own run id left."""
    _keep_jobs(ui, ledger=_open_now(f"uir:{TS}"), rows=[{
        **ARDS,
        "history": [{"run": f"uir:{OTHER_TS}", "at": "2026-10-11T09:30:00+02:00",
                     "outcome": "deferred", "summary": "waits on the funder's reply"}]}])
    ui.seed_run("uir", TS, "running", summary="")
    ui.seed_run("uir", OTHER_TS, "finished", summary="done")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{OTHER_TS}")
    row = ui_page.locator("[data-run-job='ards']")
    expect(row).to_contain_text("deferred")
    expect(row).to_contain_text("waits on the funder's reply")
    expect(row).not_to_contain_text("working on it now")


def test_each_job_is_its_own_block_from_the_stylesheet(ui, ui_page):
    """The rendered-look defect, pinned where a class can be checked: three jobs with nothing
    between them read as ONE run-on paragraph, the second job's title continuing the first's
    note. 0.417.0 fixed it with an INLINE style on the reasoning that both stylesheets were at
    their size ceilings — wrong, since the house bar is per FILE: the rules now live in
    `static/rail.css` and this asserts the COMPUTED border, so a stylesheet that loses the rule
    fails here instead of passing on a class that does nothing."""
    ledger = {"run_id": f"uir:{TS}", "open": "ards",
              "due": [{"id": "ards", "why": ["up"]}, {"id": "doc", "why": ["up"]}], "done": {}}
    _keep_jobs(ui, ledger=ledger, rows=[
        ARDS,
        {"id": "doc", "title": "Documentation handover", "state": "active",
         "brief": "the handover doc", "workspace": "jobs/doc"}])
    ui.seed_run("uir", TS, "running", summary="")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    first = ui_page.locator("[data-run-job='ards']")
    second = ui_page.locator("[data-run-job='doc']")
    expect(first).to_be_visible()
    expect(second).to_be_visible()

    # the FIRST block has nothing above it to separate from
    assert ui_page.evaluate(
        "() => getComputedStyle(document.querySelector(\"[data-run-job='ards']\"))"
        ".borderTopWidth") == "0px"
    # ... the second carries the hairline, and it comes from the stylesheet, not an inline rule
    assert ui_page.evaluate(
        "() => getComputedStyle(document.querySelector(\"[data-run-job='doc']\"))"
        ".borderTopWidth") == "1px"
    assert ui_page.evaluate(
        "() => document.querySelector(\"[data-run-job='doc']\").getAttribute('style')") is None


def test_the_job_poll_dies_with_the_run_view(ui, ui_page):
    """`tests/ui/test_view_teardown.py` catches a leaked poller by the RUN ID in its URL, and
    this one's path is the ROUTINE's (`/api/routines/{slug}/jobs`) — so the general check
    cannot see it and the claim is pinned here. A poller that outlives its view sends a GET for
    the rest of the browser session, one more per run page visited (the F324-era sub-run tree
    defect, documented in that file)."""
    _keep_jobs(ui, ledger=_open_now(f"uir:{TS}"), rows=[ARDS])
    ui.seed_run("uir", TS, "running", summary="")

    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    expect(ui_page.locator("[data-run-job='ards']")).to_be_visible()
    ui_page.evaluate("location.hash = '#/help'")
    ui_page.wait_for_selector("#view h1")

    after = watch_requests(ui_page, re.compile("/api/"))
    ui_page.wait_for_timeout(6000)       # > the section's 5 s live poll
    leaked = [u for u in after if "/jobs" in u]
    assert not leaked, f"the job poll outlived the run view: {leaked}"


def test_a_routine_without_jobs_gains_no_section(ui, ui_page):
    ui.seed_run("uir", TS, "finished", summary="done")
    ui_page.set_viewport_size({"width": 1425, "height": 900})
    ui_page.goto(f"{ui.url}/#/run/uir:{TS}")
    expect(ui_page.locator(".run-rail .rail-cap").first).to_be_visible()
    caps = [c.strip().lower() for c in
            ui_page.locator(".run-rail .rail-cap").all_inner_texts()]
    assert "jobs" not in caps
    assert ui_page.locator("[data-run-job]").count() == 0
