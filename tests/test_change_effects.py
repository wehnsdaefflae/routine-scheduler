"""Did a change help? (readmodels/change_effects.py, change_signals.py, model_fit.py) — changes
found from the runs' own fingerprints and judged on correctness, completeness and effectiveness,
with nothing declared and nothing reverted."""

from __future__ import annotations

from conftest import git_in
from helpers import measured_run, write_usage_stream
from rsched.readmodels import change_effects, change_signals, memo
from rsched.readmodels.model_fit import model_fit

_run = measured_run


def _home(tmp_path, records, *slugs):
    home = tmp_path / "routines"
    for slug in slugs or ("digest",):
        (home / slug).mkdir(parents=True, exist_ok=True)
        (home / slug / "routine.yaml").write_text("slug: x\n", encoding="utf-8")
    write_usage_stream(home, records)
    memo.reset()
    return home


def test_a_recipe_change_that_costs_more_and_delivers_less_is_a_regression(tmp_path):
    runs = ([_run(i) for i in range(1, 6)]
            + [_run(i, recipe="c2", tokens=90_000, met=1) for i in range(6, 11)])
    (change,) = change_effects.changes(_home(tmp_path, runs))["routines"]["digest"]
    assert change["what"] == [{"kind": "recipe", "name": "", "from": "c1", "to": "c2"}]
    assert (change["runs_before"], change["runs_after"]) == (5, 5)
    assert change["dimensions"] == {"correctness": "same", "completeness": "worse",
                                    "effectiveness": "worse"}
    assert change["verdict"] == "regressed"


def test_a_change_still_collecting_its_runs_is_measuring_and_churn_is_too_few(tmp_path):
    runs = ([_run(i) for i in range(1, 6)] + [_run(6, recipe="c2"), _run(7, recipe="c3"),
                                             _run(8, recipe="c3")])
    newest, middle = change_effects.changes(_home(tmp_path, runs))["routines"]["digest"]
    assert newest["verdict"] == "too few runs"           # c2 got one run before c3 came
    assert middle["verdict"] == "measuring" or middle["runs_after"] == 1
    assert {c["verdict"] for c in (newest, middle)} <= {"too few runs", "measuring"}


def test_nothing_moving_past_its_noise_threshold_is_no_effect(tmp_path):
    runs = ([_run(i, tokens=40_000) for i in range(1, 6)]
            + [_run(i, recipe="c2", tokens=44_000) for i in range(6, 11)])
    (change,) = change_effects.changes(_home(tmp_path, runs))["routines"]["digest"]
    assert change["verdict"] == "no effect"


def test_a_release_is_judged_across_routines_and_never_cuts_a_routines_history(tmp_path):
    runs = []
    for slug in ("digest", "triage"):
        runs += [_run(i, slug=slug) for i in range(1, 6)]
        runs += [_run(i, slug=slug, engine="0.397.0", tokens=20_000) for i in range(6, 11)]
    data = change_effects.changes(_home(tmp_path, runs, "digest", "triage"))
    assert data["routines"] == {"digest": [], "triage": []}     # a release is not a routine change
    (release,) = data["releases"]
    assert release["release"] == "0.397.0" and sorted(release["routines"]) == ["digest", "triage"]
    assert release["verdicts"] == {"improved": 2} and release["tokens_ratio"] == 0.5


def test_model_and_rule_changes_roll_up_across_routines(tmp_path):
    runs = []
    for slug in ("digest", "triage"):
        runs += [_run(i, slug=slug) for i in range(1, 4)]
        runs += [_run(i, slug=slug, model="Sonnet high", rules={"web-research": "r2"})
                 for i in range(4, 7)]
    fleet = change_effects.changes(_home(tmp_path, runs, "digest", "triage"))["fleet"]
    kinds = {(row["kind"], row["name"], row["from"], row["to"]) for row in fleet}
    assert kinds == {("model", "", "Opus high", "Sonnet high"),
                     ("rule", "web-research", "r1", "r2")}
    assert all(set(row["routines"]) == {"digest", "triage"} for row in fleet)


def test_unfingerprinted_runs_conversations_and_children_are_not_measured(tmp_path):
    runs = [{**_run(1), "fingerprint": None}, {**_run(2), "depth": 1},
            _run(3, slug="c-20261008-120000")]
    data = change_effects.changes(_home(tmp_path, runs))
    assert data["routines"] == {} and data["measured_runs"] == 0


def test_model_fit_groups_runs_by_what_served_them(tmp_path):
    runs = ([_run(i, tokens=60_000) for i in range(1, 5)]
            + [_run(i, model="Sonnet high", trial="t-1", tokens=20_000, met=1)
               for i in range(5, 7)])
    fit = model_fit(_home(tmp_path, runs), "digest")
    assert [(g["model"], g["trial"], g["runs"]) for g in fit] == [
        ("Opus high", "", 4), ("Sonnet high", "t-1", 2)]
    assert fit[0]["met_rate"] == 1.0 and fit[1]["met_rate"] == 0.5
    assert fit[1]["tokens"] == 20_000


def test_a_signal_needs_both_its_threshold_and_its_floor_to_move():
    tokens = next(s for s in change_signals.SIGNALS if s.name == "tokens")
    assert change_signals.compare(1_000, 3_000, tokens) == 0       # 3x, but under the floor
    assert change_signals.compare(40_000, 90_000, tokens) == -1
    assert change_signals.compare(90_000, 40_000, tokens) == 1
    met = next(s for s in change_signals.SIGNALS if s.name == "met_rate")
    assert change_signals.compare(0.9, 0.6, met) == -1 and change_signals.compare(None, 1, met) == 0


def test_the_api_serves_the_fleet_and_one_routine(api_client):
    c, tmp = api_client
    _home(tmp, [_run(i) for i in range(1, 4)] + [_run(i, recipe="c2") for i in range(4, 7)])
    fleet = c.get("/api/changes").json()
    assert fleet["routines"][0]["routine"] == "digest" and fleet["measured_runs"] == 6
    one = c.get("/api/changes/digest").json()
    assert one["changes"][0]["verdict"] == "no effect" and one["model_fit"][0]["runs"] == 6
    assert c.get("/api/changes/nobody").status_code == 404


def _commit(d, subject: str) -> str:
    git_in(d, "add", "-A")
    git_in(d, "commit", "-qm", subject)
    return git_in(d, "rev-parse", "HEAD").stdout.strip()


def test_the_production_pages_one_line_names_the_newest_verdict_and_the_recipe_flag(
        api_client, make_routine):
    """The routine page carries ONE line about development (operator, 2026-10-08): the count,
    the newest verdict, and — since the recipe-health banner moved to the development view —
    the regression flag on the newest recipe change, so a flagged recipe still reaches it."""
    c, tmp = api_client
    d = make_routine(slug="digest")
    git_in(d, "init", "-q")
    v1 = _commit(d, "scaffold")
    (d / "main.md").write_text("# v2\n", encoding="utf-8")
    v2 = _commit(d, "recipe: sharpen the scan")
    write_usage_stream(tmp / "routines", [_run(i, recipe=v1) for i in range(1, 6)]
                       + [_run(i, recipe=v2, status="failed") for i in range(6, 11)])
    memo.reset()

    line = c.get("/api/changes/digest/summary").json()
    assert (line["changes"], line["latest"]) == (1, "regressed")
    assert line["at"].startswith("2026-10-06")
    assert line["recipe_regression"]["subject"] == "recipe: sharpen the scan"


def test_a_routine_with_nothing_measured_has_an_empty_line(api_client, make_routine):
    c, _tmp = api_client
    make_routine(slug="quiet")
    memo.reset()
    assert c.get("/api/changes/quiet/summary").json() == {
        "routine": "quiet", "changes": 0, "latest": None, "at": None, "recipe_regression": None}
    assert c.get("/api/changes/nobody/summary").status_code == 404
