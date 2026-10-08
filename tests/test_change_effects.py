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
    assert release["verdict"] == "improved" and release["tokens_ratio"] == 0.5
    assert (release["confounded"], release["smeared"]) == (0, 0)


def test_model_and_rule_changes_roll_up_across_routines(tmp_path):
    runs = []
    for slug in ("digest", "triage"):
        runs += [_run(i, slug=slug) for i in range(1, 4)]
        runs += [_run(i, slug=slug, model="Sonnet high", rules={"web-research": "r2"})
                 for i in range(4, 7)]
    fleet = change_effects.changes(_home(tmp_path, runs, "digest", "triage"))["fleet"]
    kinds = {(row["kind"], row["name"], row["from"], row["to"]) for row in fleet}
    assert kinds == {("model", "", "Opus high", "Sonnet high"),
                     ("model_id", "", "proxy/opus", "proxy/sonnet"),
                     ("rule", "web-research", "r1", "r2")}
    assert all(set(row["routines"]) == {"digest", "triage"} for row in fleet)
    # they changed TOGETHER at every run: no row may claim the batch's effect as its own
    assert all(row["alone"] == 0 and row["verdict"] == "too few runs" for row in fleet)
    assert all(row["together"]["runs_before"] == 6 for row in fleet)


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


def test_a_rule_revised_alone_is_judged_on_every_holder_pooled(tmp_path):
    """Each holder ran once on either side — `too few runs` for each alone — and the pool of
    the three is judged; routines a hundred times apart in size weigh alike."""
    runs = []
    for slug, size in (("digest", 1_000_000), ("triage", 10_000), ("scan", 50_000)):
        runs += [_run(1, slug=slug, tokens=size),
                 _run(2, slug=slug, tokens=size * 3, rules={"web-research": "r2"})]
    data = change_effects.changes(_home(tmp_path, runs, "digest", "triage", "scan"))
    assert all(chs[0]["verdict"] == "too few runs" for chs in data["routines"].values())
    (row,) = data["fleet"]
    assert (row["alone"], row["runs_before"], row["runs_after"]) == (3, 3, 3)
    assert row["verdict"] == "regressed" and row["tokens_ratio"] == 3.0
    assert row["together"] is None


def test_an_unknown_component_is_never_a_change(tmp_path):
    """A rebuilt run knows neither its catalog model nor its effort; the live runs after it do
    — a gap in the evidence, not a change in the routine."""
    rebuilt = []
    for i in range(1, 4):
        rec = _run(i, rebuilt=True)
        rec["fingerprint"] = {k: v for k, v in rec["fingerprint"].items() if k != "model"}
        rec["fingerprint"]["unknown"] = ["effort", "model"]
        rebuilt.append(rec)
    runs = [*rebuilt, *(_run(i) for i in range(4, 7))]
    data = change_effects.changes(_home(tmp_path, runs))
    assert data["routines"]["digest"] == []
    fit = model_fit(_home(tmp_path, runs), "digest")
    assert {(g["model"], g["model_id"], g["effort"], g["rebuilt"]) for g in fit} == {
        (None, "proxy/opus", None, 3), ("Opus high", "proxy/opus", "high", 0)}


def test_a_rebuilt_change_says_so(tmp_path):
    runs = ([_run(i, rebuilt=True) for i in range(1, 4)]
            + [_run(i, recipe="c2", rebuilt=True) for i in range(4, 7)])
    (change,) = change_effects.changes(_home(tmp_path, runs))["routines"]["digest"]
    assert change["reconstructed"] is True


def test_an_archived_routines_runs_count_for_the_fleet_and_not_for_its_successor(tmp_path):
    """`digest` was archived on 10-04 and created again: its runs before that are another
    routine's — never a change in the new one, still evidence about the release they ran."""
    runs = ([_run(i, tokens=90_000) for i in range(1, 3)]
            + [_run(3, tokens=30_000, engine="0.397.0")]
            + [_run(i, recipe="new") for i in range(5, 8)])
    home = _home(tmp_path, runs)
    old = home / ".archive" / "digest-20261004-000000"
    old.mkdir(parents=True)
    (old / "routine.yaml").write_text("slug: digest\n", encoding="utf-8")
    memo.reset()
    data = change_effects.changes(home)
    assert data["routines"] == {"digest": []}                  # no change across the two
    assert data["archived"] == ["digest-20261004-000000"]
    assert data["releases"][0]["routines"] == ["digest-20261004-000000"]
    assert data["measured_runs"] == 6


def test_a_count_is_compared_only_where_it_was_counted_the_same_way():
    """0.369.0-0.371.1 changed what the verifier and the holds count — the ruler, not the
    routine: a run before 0.371.1 has no reading for them."""
    old, new = _run(1, engine="0.370.2"), _run(2, engine="0.371.1")
    old["quality"]["challenged"] = new["quality"]["challenged"] = 2
    assert change_signals.run_signals(old)["challenged"] is None
    assert change_signals.run_signals(new)["challenged"] == 2
    assert change_signals.run_signals(old)["interventions"] == 0     # unaffected


def test_the_fleet_week_by_week(tmp_path):
    """Each routine counts once a week; a cost reads against the routine's own median; a week
    without a run is kept, empty."""
    runs = [_run(1, tokens=10_000), _run(2, tokens=30_000, engine="0.397.0"),
            _run(1, slug="triage", tokens=500_000), _run(15, slug="triage", tokens=500_000)]
    weeks = change_effects.changes(_home(tmp_path, runs, "digest", "triage"))["timeline"]
    assert [w["week"] for w in weeks] == ["2026-W40", "2026-W41", "2026-W42"]
    first, empty, last = weeks
    assert (first["runs"], first["routines"]) == (3, 2)
    assert first["releases"] == ["0.396.0", "0.397.0"]
    assert first["signals"]["tokens"] == 1.0          # digest: (0.5+1.5)/2 → median 1; triage 1
    assert empty["runs"] == 0 and empty["signals"]["tokens"] is None
    assert last["models"] == {"proxy/opus": 1}


def test_the_api_serves_the_timelines(api_client):
    c, tmp = api_client
    _home(tmp, [_run(i) for i in range(1, 4)])
    fleet = c.get("/api/changes").json()
    assert fleet["timeline"][0]["runs"] == 3 and fleet["archived"] == []
    assert c.get("/api/changes/digest").json()["timeline"][0]["routines"] == 1


def test_a_release_a_routine_skipped_smears_the_one_it_moved_to(tmp_path):
    """`digest` ran 0.396 then 0.398: its boundary carries 0.397's code too, which only
    `triage` ever ran."""
    runs = [_run(i, engine=e) for i, e in ((1, "0.396.0"), (2, "0.396.0"), (3, "0.398.0"))]
    runs += [_run(i, slug="triage", engine=e)
             for i, e in ((1, "0.396.0"), (2, "0.397.0"), (3, "0.398.0"))]
    rel = {r["release"]: r for r in change_effects.changes(
        _home(tmp_path, runs, "digest", "triage"))["releases"]}
    assert sorted(rel) == ["0.397.0", "0.398.0"]
    assert rel["0.397.0"]["smeared"] == 1                      # its after-window ran 0.398
    assert rel["0.398.0"]["routines"] == ["digest", "triage"]
    assert rel["0.398.0"]["smeared"] == 1                      # digest: 0.397 between, median 1


def test_a_catalog_model_switch_is_one_change_not_three(tmp_path):
    """Name, provider id and effort move together when a routine switches catalog model."""
    runs = []
    for slug in ("digest", "triage", "scan"):
        runs += [_run(1, slug=slug), _run(2, slug=slug, model="Sonnet low", effort="low")]
    fleet = change_effects.changes(_home(tmp_path, runs, "digest", "triage", "scan"))["fleet"]
    assert {r["kind"] for r in fleet} == {"model", "model_id", "effort"}
    assert all(r["alone"] == 3 and r["together"] is None for r in fleet)
