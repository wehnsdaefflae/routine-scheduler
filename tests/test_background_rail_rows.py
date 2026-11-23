"""D308 pt 2/3 — what a background ROW carries, so the conversation rail can say it: the row
used to hold taskid/label/state/run_id/summary/delivered and the rail rendered the first three,
while elapsed time needs the run's start, the landed files need their paths, and a task started
from the composer needs to exist before the DetachedManager's next tick — otherwise the
control's promise ("it appears in the rail at once") is true only on average. Own file:
tests/test_conversations.py is at its size-ratchet ceiling.
"""

from __future__ import annotations

import pytest
import yaml

from conftest import authed_client, seeded_server
from rsched.paths import atomic_write_json

TS = "20260712-130000"


@pytest.fixture
def server(tmp_path):
    return seeded_server(tmp_path)


@pytest.fixture
def client(server):
    with authed_client(server) as c:
        yield c, server


def _bg_task(server, taskid, owner_slug, *, state="running", delivered=False):
    """A detached task dir under background_home owned by owner_slug, with one run."""
    d = server.background_home / taskid
    (d / "state").mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(yaml.safe_dump({
        "slug": taskid, "name": "scrape", "enabled": True,
        "schedule": {"cron": "", "tz": "Europe/Berlin"},
        "workflow": {"library_slug": "general-task", "library_commit": ""},
        "owner": {"slug": owner_slug, "dir": str(server.conversations_home / owner_slug)},
    }))
    rd = d / "runs" / TS
    rd.mkdir(parents=True)
    atomic_write_json(rd / "status.json",
                      {"run_id": f"{taskid}:{TS}", "state": state, "pid": 999999})
    if delivered:
        atomic_write_json(d / "delivered.json", {"ts": "t", "state": state, "owner": owner_slug})
    return d


def _conversation(c):
    return c.post("/api/conversations", data={"text": "t"}).json()["slug"]


def test_a_live_row_carries_the_run_ts_the_rail_counts_elapsed_time_from(client):
    """A row with no start cannot say how long the task has been going, and the rail had no
    other source for it: the task's run dir is not the conversation's."""
    c, server = client
    slug = _conversation(c)
    taskid = f"bg-{slug}-aaaa"
    _bg_task(server, taskid, slug)
    row = c.get(f"/api/conversations/{slug}/background").json()[0]
    assert row["started"] == TS
    assert row["run_id"] == f"{taskid}:{TS}"


def test_a_delivered_row_names_the_files_that_landed_in_the_conversation(client):
    """The paths are the ones the delivery copied under `artifacts/from-bg-<taskid>/`, relative
    to the CONVERSATION dir — which is how the artifacts panel addresses a file, so the rail
    can link each one into it instead of naming it as text."""
    c, server = client
    slug = _conversation(c)
    taskid = f"bg-{slug}-bbbb"
    _bg_task(server, taskid, slug, state="finished", delivered=True)
    landed = server.conversations_home / slug / "artifacts" / f"from-bg-{taskid}"
    landed.mkdir(parents=True)
    (landed / "report.md").write_text("done", encoding="utf-8")
    (landed / "notes.txt").write_text("more", encoding="utf-8")

    row = c.get(f"/api/conversations/{slug}/background").json()[0]
    assert row["delivered"] is True
    assert row["files"] == [f"artifacts/from-bg-{taskid}/notes.txt",
                            f"artifacts/from-bg-{taskid}/report.md"]


def test_an_undelivered_task_lists_no_files_even_when_some_are_there(client):
    """`delivered` is about the HAND-BACK, not about the run: files sitting in the directory
    before the marker exists have not reached the conversation, and a rail that linked them
    would say the delivery happened."""
    c, server = client
    slug = _conversation(c)
    taskid = f"bg-{slug}-cccc"
    _bg_task(server, taskid, slug, state="finished")
    landed = server.conversations_home / slug / "artifacts" / f"from-bg-{taskid}"
    landed.mkdir(parents=True)
    (landed / "half.md").write_text("partial", encoding="utf-8")

    row = c.get(f"/api/conversations/{slug}/background").json()[0]
    assert row["delivered"] is False and row["files"] == []


def test_a_just_launched_task_is_a_row_before_the_manager_has_made_its_dir(client):
    """The composer's control posts the intent and expects the rail to show it. Intake is a
    daemon tick away, so until then the task exists only as `.requests/<taskid>.json` — and a
    control whose result appears up to five seconds later reads as one that did nothing."""
    c, server = client
    slug = _conversation(c)
    r = c.post(f"/api/conversations/{slug}/background",
               data={"prompt": "scrape 200 pages", "label": "scrape the archive"})
    taskid = r.json()["taskid"]
    assert not (server.background_home / taskid).exists()

    rows = c.get(f"/api/conversations/{slug}/background").json()
    assert [row["taskid"] for row in rows] == [taskid]
    assert rows[0]["state"] == "queued" and rows[0]["label"] == "scrape the archive"
    assert rows[0]["run_id"] == "" and rows[0]["files"] == []


def test_a_pending_task_of_another_conversation_is_not_this_ones_row(client):
    """The requests directory is shared by every conversation, so the owner on each request is
    what partitions it — exactly as the task dirs' `owner.slug` does."""
    c, _server = client
    mine, theirs = _conversation(c), _conversation(c)
    c.post(f"/api/conversations/{theirs}/background", data={"prompt": "not mine"})
    assert c.get(f"/api/conversations/{mine}/background").json() == []
    assert len(c.get(f"/api/conversations/{theirs}/background").json()) == 1
