"""The never-grantable credential stores, enforced where grants are actually MADE.

`entities.NEVER_GRANTABLE` has always promised that the instance's config dir (the console
token plus the central secrets store), `~/.credentials` and `~/.ssh` can be granted to no
routine "by design". The promise had exactly ONE enforcer — the runtime access-REQUEST
validator — so it was true of what a run asks for and false of what an operator types into
the routine page's Filesystem-roots panel. That is how the config dir became a live read AND
write root on a routine that audits configuration: one prompt injection away from the
operator's token, every routine's scoped secrets and every OAuth refresh token.

Two enforcers now, on purpose asymmetric:

- the PATCH edge REFUSES a new one with a 400 naming the path and the reason;
- the LOADER REPORTS one already in a file and keeps it. Dropping would break the two live
  routines whose actual job is auditing and exporting the server's configuration — a root
  that vanishes from under their next run fails them with nothing naming the cause. Loud
  beats silent in both directions; which one is louder is what differs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, make_test_server
from rsched import entities
from rsched.config import load_routine
from rsched.web.app import create_app

GUARDED = ["~/.config/routine-scheduler", "~/.credentials", "~/.ssh"]


def test_the_guard_names_the_three_stores_and_anything_over_them():
    assert entities.guarded_roots(GUARDED) == GUARDED
    assert entities.guarded_roots(["~/.ssh/config"]) == ["~/.ssh/config"]
    assert entities.guarded_roots(["~"]) == ["~"]          # a grant that CONTAINS one
    assert entities.guarded_roots(["~/routines", "~/git-repos/routine-scheduler"]) == []
    assert entities.guarded_roots(None) == [] and entities.guarded_roots("~/.ssh") == []


def test_the_patch_edge_refuses_a_guarded_root_and_says_which(tmp_path, make_routine):
    make_routine(slug="auditor")
    server = make_test_server(tmp_path)
    with TestClient(create_app(server, with_scheduler=False)) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        for key in ("fs_read_roots", "fs_write_roots"):
            r = c.patch("/api/routines/auditor",
                        json={key: ["~/routines", "~/.config/routine-scheduler"]})
            assert r.status_code == 400, r.text
            assert "~/.config/routine-scheduler" in r.json()["detail"]
            assert "credential store" in r.json()["detail"]
        # …and the clean list still saves
        ok = c.patch("/api/routines/auditor", json={"fs_read_roots": ["~/routines"]})
        assert ok.status_code == 200, ok.text
        saved = yaml.safe_load((tmp_path / "routines" / "auditor" / "routine.yaml").read_text())
        assert saved["fs_read_roots"] == ["~/routines"]


def test_every_grant_edge_holds_the_same_checks(tmp_path, make_routine):
    """ONE enforcer (`config_fields.validate_roots`) for the routine PATCH, the conversation
    PATCH and the conversation create form. The routine PATCH used to save a RELATIVE root —
    resolved later against the daemon's working directory — because the absolute-path check
    lived in the create form alone."""
    make_routine(slug="auditor")
    server = make_test_server(tmp_path)
    with TestClient(create_app(server, with_scheduler=False)) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        r = c.patch("/api/routines/auditor", json={"fs_write_roots": ["data/out"]})
        assert r.status_code == 400 and "not an absolute path" in r.json()["detail"], r.text
        ok = c.patch("/api/routines/auditor",
                     json={"fs_read_roots": ["~/routines/", " ~/routines", "/srv/x"]})
        assert ok.status_code == 200, ok.text
        saved = yaml.safe_load((tmp_path / "routines" / "auditor" / "routine.yaml").read_text())
        assert saved["fs_read_roots"] == ["~/routines", "/srv/x"]


def _write_roots(routine: Path, key: str, values: list[str]) -> None:
    """Put a grant in the FILE, the way the loader's "already there" case arises."""
    raw = yaml.safe_load((routine / "routine.yaml").read_text(encoding="utf-8"))
    raw[key] = values
    (routine / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")


def test_narrowing_a_guarded_root_the_routine_already_holds_is_accepted(tmp_path, make_routine):
    """F582: the guard's own advisory asks the operator to narrow a store a routine already
    holds — and the PATCH edge refused the narrower path, so the fix could not be applied at
    all. A value strictly INSIDE a grant the file already carries reduces what the routine
    reaches, so it is accepted; the direction that does not reduce it stays refused below.
    """
    routine = make_routine(slug="configaudit")
    _write_roots(routine, "fs_read_roots", ["~/routines", "~/.config/routine-scheduler"])
    server = make_test_server(tmp_path)
    with TestClient(create_app(server, with_scheduler=False)) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        r = c.patch("/api/routines/configaudit",
                    json={"fs_read_roots": ["~/routines",
                                            "~/.config/routine-scheduler/config.yaml"]})
        assert r.status_code == 200, r.text
        saved = yaml.safe_load((routine / "routine.yaml").read_text())
        assert saved["fs_read_roots"] == ["~/routines",
                                          "~/.config/routine-scheduler/config.yaml"]


def test_re_sending_a_guarded_root_the_file_already_holds_is_not_refused(tmp_path, make_routine):
    """A grant already in the file is the loader's to REPORT and keep, so an edit that merely
    re-sends it changes nothing — and refusing it blocks every unrelated edit to the same list
    for exactly the routines the guard complains about.
    """
    routine = make_routine(slug="configaudit")
    _write_roots(routine, "fs_read_roots", ["~/.config/routine-scheduler/config.yaml"])
    server = make_test_server(tmp_path)
    with TestClient(create_app(server, with_scheduler=False)) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        r = c.patch("/api/routines/configaudit",
                    json={"fs_read_roots": ["~/.config/routine-scheduler/config.yaml",
                                            "~/routines"]})
        assert r.status_code == 200, r.text
        saved = yaml.safe_load((routine / "routine.yaml").read_text())
        assert saved["fs_read_roots"] == ["~/.config/routine-scheduler/config.yaml", "~/routines"]


def test_a_newly_granted_guarded_root_is_still_refused_on_a_routine_holding_none(
        tmp_path, make_routine):
    """The security property, unchanged: an allowance exists only RELATIVE to a grant the
    routine already has. A routine holding no store cannot acquire one through this door.
    """
    routine = make_routine(slug="clean")
    _write_roots(routine, "fs_read_roots", ["~/routines"])
    server = make_test_server(tmp_path)
    with TestClient(create_app(server, with_scheduler=False)) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        for value in ("~/.config/routine-scheduler", "~/.config/routine-scheduler/config.yaml",
                      "~/.ssh/id_ed25519"):
            r = c.patch("/api/routines/clean", json={"fs_read_roots": ["~/routines", value]})
            assert r.status_code == 400, f"{value}: {r.text}"
            assert "credential store" in r.json()["detail"]
        # the other LIST is its own grant: holding the store for reads does not open writes
        _write_roots(routine, "fs_read_roots", ["~/.config/routine-scheduler"])
        r = c.patch("/api/routines/clean",
                    json={"fs_write_roots": ["~/.config/routine-scheduler/config.yaml"]})
        assert r.status_code == 400, r.text


def test_widening_a_narrowed_guarded_root_back_to_the_store_is_refused(tmp_path, make_routine):
    """Narrowing is a one-way door: once the grant is the file inside the store, this edge will
    not take the whole store back, nor a parent of what is held.
    """
    routine = make_routine(slug="configaudit")
    _write_roots(routine, "fs_read_roots", ["~/.config/routine-scheduler/config.yaml"])
    server = make_test_server(tmp_path)
    with TestClient(create_app(server, with_scheduler=False)) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        for value in ("~/.config/routine-scheduler", "~/.config"):
            r = c.patch("/api/routines/configaudit", json={"fs_read_roots": [value]})
            assert r.status_code == 400, f"{value}: {r.text}"
            assert "credential store" in r.json()["detail"]


def test_an_edge_making_the_grant_passes_no_current_so_nothing_is_grantable(tmp_path):
    """The create edges (the conversation composer, a settings pattern) pass no `current`, so
    every guarded value is new there — the allowance cannot be reached by a form making the
    grant for the first time.
    """
    from rsched.web import config_fields

    assert config_fields.validate_roots(
        "fs_read_roots", ["~/.config/routine-scheduler/config.yaml"],
        current=["~/.config/routine-scheduler"]) == ["~/.config/routine-scheduler/config.yaml"]
    for current in (None, [], ["~/routines"]):
        with pytest.raises(HTTPException) as caught:
            config_fields.validate_roots("fs_read_roots",
                                         ["~/.config/routine-scheduler/config.yaml"],
                                         current=current)
        assert "credential store" in str(caught.value.detail), current


def test_a_guarded_root_already_in_a_file_is_reported_and_kept(make_routine):
    """The store is named ABSOLUTELY here because conftest's `_hermetic_home` redirects `~`
    for the config package only — a routine.yaml may carry either spelling, and the guard
    compares resolved paths."""
    routine = make_routine(slug="configaudit")
    raw = yaml.safe_load((routine / "routine.yaml").read_text(encoding="utf-8"))
    raw["fs_read_roots"] = ["~/routines", str(Path.home() / ".config" / "routine-scheduler")]
    (routine / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg, problems = load_routine(routine)
    assert cfg is not None
    named = [p for p in problems if "routine-scheduler" in p and "credential store" in p]
    assert named, problems
    assert "fs_read_roots" in named[0]
    # KEPT: the run that has been reading it for months keeps reading it until the operator acts
    assert len(cfg.fs_read_roots) == 2
    assert str(cfg.fs_read_roots[1]).endswith(".config/routine-scheduler")


def test_a_clean_routine_reports_nothing(make_routine):
    routine = make_routine(slug="clean")
    raw = yaml.safe_load((routine / "routine.yaml").read_text(encoding="utf-8"))
    raw["fs_write_roots"] = ["~/routines"]
    (routine / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    _cfg, problems = load_routine(routine)
    assert not [p for p in problems if "credential store" in p]
