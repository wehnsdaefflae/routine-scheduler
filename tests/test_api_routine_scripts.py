"""The run rail's SCRIPTS section, server half (web/api_routine_files.py).

Operator order 2026-11-01: "a routine's scripts should have a section in the right routine
run sidebar". The catalog the rail shows is `rsched.scripts.list_scripts` — the SAME reader
the engine uses to tell a run what its `scripts/` hold (engine/capabilities.py) — so the
sidebar cannot drift from what the run was told it could call. That sharing is the contract
these tests pin: a new description written for the console would be a second source of truth
for the same bytes.

Why the route is its own lazy read rather than a field on `/api/runs/{run_id}`: that payload
is fetched by every run page at mount, and most readers never open the section. The run
detail stays the size it was.
"""

from __future__ import annotations

from helpers import bare_routine

HEADER = '''"""tidy-up — delete the temp files a run leaves behind.

usage:
  tidy-up [--dry-run]
"""
'''


def _with_scripts(home):
    bare_routine(home, "worker")
    d = home / "worker" / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tidy_up.py").write_text(HEADER, encoding="utf-8")
    # A script whose header cannot be parsed must still LIST: a reader looking for the file
    # that broke needs to see it named, and hiding it is how a broken script stays invisible.
    (d / "zz_broken.py").write_text("x = (\n", encoding="utf-8")
    return d


def test_the_rail_reads_the_same_catalog_the_run_is_given(api_client):
    c, tmp = api_client
    home = tmp / "routines"
    _with_scripts(home)
    body = c.get("/api/routines/worker/scripts")
    assert body.status_code == 200, body.text
    rows = body.json()["scripts"]
    names = [r["name"] for r in rows]
    assert names == ["tidy_up", "zz_broken"], names
    tidy = rows[0]
    # `utils_header.parse_header` keeps the docstring's WHOLE first line as the summary —
    # name included, exactly as a run is shown it ("tidy-up — delete …"). The rail strips
    # that leading `<name> —` itself (components/runscripts.js) rather than the route
    # editing the header: one reader, one text, and the console does its own layout.
    assert tidy["summary"] == "tidy-up — delete the temp files a run leaves behind."
    assert "tidy-up [--dry-run]" in tidy["usage"]
    # every row carries the three keys the rail renders, broken script included
    for r in rows:
        assert set(r) == {"name", "summary", "usage"}, r


def test_a_routine_with_no_scripts_answers_with_an_empty_list(api_client):
    c, tmp = api_client
    bare_routine(tmp / "routines", "plain")
    body = c.get("/api/routines/plain/scripts")
    assert body.status_code == 200, body.text
    # NOT a 404: the rail adds its section only when the list is non-empty, and an error
    # would make every scriptless routine's run page log a failed read.
    assert body.json() == {"scripts": []}


def test_an_unknown_routine_is_a_404(api_client):
    c, _ = api_client
    assert c.get("/api/routines/no-such-routine/scripts").status_code == 404
