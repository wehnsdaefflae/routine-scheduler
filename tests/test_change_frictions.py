"""The MOMENT TEST (readmodels/change_frictions.py): a change is judged by whether the failures
that REPEATED before it came back after it — two runs a side, the neighbouring changes a short
window had to reach across named, and what a recipe commit says it `Addresses:` judged on its
own."""

from __future__ import annotations

from conftest import git_in
from helpers import measured_run, write_usage_stream
from rsched.readmodels import change_effects, memo


def _home(tmp_path, records):
    home = tmp_path / "routines"
    (home / "digest").mkdir(parents=True, exist_ok=True)
    (home / "digest" / "routine.yaml").write_text("slug: x\n", encoding="utf-8")
    write_usage_stream(home, records)
    memo.reset()
    return home


def _changes(tmp_path, runs):
    return change_effects.changes(_home(tmp_path, runs))["routines"]["digest"]


def test_a_repeated_failure_gone_after_the_change_is_fixed_on_two_runs_a_side(tmp_path):
    runs = [measured_run(1, frictions={"edit_miss": 3, "schema": 1}),
            measured_run(2, frictions={"edit_miss": 1}),
            measured_run(3, recipe="c2", frictions={"script:sync": 2}),
            measured_run(4, recipe="c2", frictions={"script:sync": 1})]
    (change,) = _changes(tmp_path, runs)
    assert change["verdict"] == "too few runs"            # the rate test cannot say a thing
    moment = change["frictions"]
    assert moment["status"] == "judged" and moment["confounded"] == []
    assert moment["fixed"] == [{"signature": "edit_miss", "before": 2, "after": 0}]
    assert moment["persists"] == []
    assert moment["introduced"] == [{"signature": "script:sync", "before": 0, "after": 2}]
    # one run's schema slip was never a target
    assert [c["signature"] for c in moment["chronic"]] == ["edit_miss"]


def test_a_short_segment_borrows_its_neighbours_runs_and_names_them_confounded(tmp_path):
    runs = [measured_run(1, frictions={"edit_miss": 1}),
            measured_run(2, frictions={"edit_miss": 1}),
            measured_run(3, recipe="c2", frictions={}),
            measured_run(4, recipe="c3", frictions={}),
            measured_run(5, recipe="c3", frictions={})]
    newest, first = _changes(tmp_path, runs)
    assert first["frictions"]["status"] == "judged"
    assert first["frictions"]["confounded"] == ["digest:20261004-050000"]   # c3 came next
    assert first["frictions"]["fixed"][0]["signature"] == "edit_miss"
    # c3's own before-window is c2's single run, reaching back across c2 itself
    assert newest["frictions"]["confounded"] == ["digest:20261003-050000"]


def test_a_run_without_recorded_frictions_is_unknown_not_clean(tmp_path):
    runs = [measured_run(1, frictions={"edit_miss": 1}), measured_run(2, frictions={"edit_miss": 1}),
            measured_run(3, recipe="c2"), measured_run(4, recipe="c2")]   # no record after
    (change,) = _changes(tmp_path, runs)
    assert change["frictions"]["status"] == "measuring"
    assert change["frictions"]["runs_after"] == 0 and "fixed" not in change["frictions"]


def test_an_addresses_line_is_judged_as_the_changes_aim(tmp_path):
    home = tmp_path / "routines"
    d = home / "digest"
    d.mkdir(parents=True)
    (d / "routine.yaml").write_text("slug: x\n", encoding="utf-8")
    git_in(d, "init", "-q")
    (d / "main.md").write_text("v1\n", encoding="utf-8")
    git_in(d, "add", "-A")
    git_in(d, "commit", "-qm", "v1")
    c1 = git_in(d, "rev-parse", "HEAD").stdout.strip()
    (d / "main.md").write_text("v2\n", encoding="utf-8")
    git_in(d, "commit", "-qam", "anchor the edit on a unique line\n\n"
           "Addresses: edit_miss, unmet:d4 not-a-signature")
    c2 = git_in(d, "rev-parse", "HEAD").stdout.strip()
    runs = [measured_run(1, recipe=c1, frictions={"edit_miss": 1}),
            measured_run(2, recipe=c1, frictions={"edit_miss": 2}),
            measured_run(3, recipe=c2, frictions={"edit_miss": 1}),
            measured_run(4, recipe=c2, frictions={})]
    write_usage_stream(home, runs)
    memo.reset()
    (change,) = change_effects.changes(home)["routines"]["digest"]
    assert change["frictions"]["aims"] == [
        {"signature": "edit_miss", "before": 2, "after": 1, "verdict": "persists"},
        {"signature": "unmet:d4", "before": 0, "after": 0, "verdict": "unfounded"}]


def test_a_commit_that_cannot_be_read_leaves_the_aims_unknown(tmp_path):
    runs = [measured_run(1, frictions={}), measured_run(2, frictions={}),
            measured_run(3, recipe="c2", frictions={}), measured_run(4, recipe="c2", frictions={})]
    (change,) = _changes(tmp_path, runs)                   # the routine dir is no git repo
    assert change["frictions"]["aims"] is None
