"""The durable run record's two halves (engine/runrecord.py): the FINGERPRINT — what made the
run — and QUALITY — how it went. A routine's input changes from run to run, so conclusions about
a change are drawn from these, compared across the runs on either side of it."""

from __future__ import annotations

import json
from types import SimpleNamespace

from conftest import finish, write_file
from helpers import server_for
from rsched import __version__
from rsched.config import load_routine
from rsched.engine import runrecord
from rsched.engine.history import prior_counters
from rsched.engine.runtime import run_routine

TS = "20261008-140000"
DONE_WHEN_MD = """# Run flow

Do the job.

## Done when

- d1 — the digest is written
- d2 — the link resolves
"""


def _records(d) -> list[dict]:
    stream = d.parent / ".control" / "workflow-usage.jsonl"
    return [json.loads(x) for x in stream.read_text(encoding="utf-8").splitlines()]


def test_a_run_records_what_made_it_and_how_it_went(make_routine, scripted, monkeypatch):
    from rsched.engine import verifier
    from rsched.engine.inbox import file_message

    monkeypatch.setattr(verifier, "refuted", lambda loop, claims, summary: [])
    d = make_routine(slug="recorded", workflow_md=DONE_WHEN_MD)

    def interrupted():
        file_message(d, "use the short form", via="web")      # reaches the run mid-work
        return write_file("state/a.txt")

    scripted([interrupted, write_file("state/b.txt"),
              {**finish(), "accounting": ["d1 met: state/a.txt written",
                                          "d2 unmet: the host was down"]}])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    (rec,) = [r for r in _records(d) if r["depth"] == 0]
    fp, q = rec["fingerprint"], rec["quality"]
    assert fp["engine"] == __version__ and fp["model"] and isinstance(fp["rules"], dict)
    assert len(fp["config"]) == 10 and "trial" not in fp
    assert (q["owed"], q["met"], q["unmet"], q["not_due"]) == (2, 1, 1, 0)
    assert q["interventions"] == 1 and q["challenged"] == 0 and q["holds"] == 0
    assert q["elapsed_s"] >= 0 and "cache_read" in q


def test_the_config_hash_moves_with_behaviour_and_not_with_identity(make_routine):
    import yaml

    d = make_routine(slug="hashed")
    cfg, _ = load_routine(d)
    before = runrecord.config_hash(cfg)
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    (d / "routine.yaml").write_text(yaml.safe_dump({**raw, "description": "renamed",
                                                    "tags": ["x"]}))
    assert runrecord.config_hash(load_routine(d)[0]) == before        # identity: no change
    (d / "routine.yaml").write_text(yaml.safe_dump(
        {**raw, "budgets": {**raw["budgets"], "max_turns": 99}}))
    assert runrecord.config_hash(load_routine(d)[0]) != before        # behaviour: a change


def test_when_a_routine_runs_is_not_how_it_behaves(make_routine):
    """0.398.0 excluded `schedule` and `retention` — YAML keys, not the field names the dump
    carries (`cron`, `tz`, `keep_runs`) — so a cron edit read as a behaviour change."""
    import yaml

    d = make_routine(slug="clocked")
    before = runrecord.config_hash(load_routine(d)[0])
    raw = yaml.safe_load((d / "routine.yaml").read_text())
    (d / "routine.yaml").write_text(yaml.safe_dump({
        **raw, "schedule": {"cron": "17 3 * * 2", "tz": "Etc/UTC"},
        "retention": {"keep_runs": 7}, "hub_tab": "Elsewhere", "improve": False}))
    assert runrecord.config_hash(load_routine(d)[0]) == before


def test_every_config_field_is_decided():
    """A field in neither set would be silently left out of the hash; in both, contradictory."""
    from rsched.config.routine import RoutineConfig

    fields = set(RoutineConfig.model_fields)
    assert fields == runrecord.BEHAVIOUR | runrecord.NOT_BEHAVIOUR, (
        f"undecided: {sorted(fields - runrecord.BEHAVIOUR - runrecord.NOT_BEHAVIOUR)}")
    assert not runrecord.BEHAVIOUR & runrecord.NOT_BEHAVIOUR


def test_a_rule_revision_moves_only_that_rules_hash(tmp_path):
    (tmp_path / "a.md").write_text("# rule: a — one\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# rule: b — two\n", encoding="utf-8")
    before = runrecord.rule_hashes(tmp_path, ["a", "b", "gone"])
    (tmp_path / "b.md").write_text("# rule: b — revised\n", encoding="utf-8")
    after = runrecord.rule_hashes(tmp_path, ["a", "b", "gone"])
    assert before["a"] == after["a"] and before["b"] != after["b"] and after["gone"] == ""


def test_a_trial_run_names_its_trial(tmp_path):
    (tmp_path / runrecord.TRIAL_FILE).write_text(json.dumps({"id": "t-1"}), encoding="utf-8")
    assert runrecord.trial_id(tmp_path) == "t-1"
    assert runrecord.trial_id(tmp_path / "nowhere") == ""


def test_the_quality_counters_survive_a_resume():
    """Run-cumulative like `asks_deferred`: a resumed leg reseeds them from the prior leg's
    status.json, or the newest leg's record — the one the stream's fold keeps — would undercount
    the run."""
    status = {"claims_challenged": 2, "claims_disputed": 1, "holds": 3, "interventions": 4}
    assert prior_counters(status) == status
    assert prior_counters({"holds": True}) == {}                       # a bool is not a count


def test_quality_reads_only_done_when_and_goal_lines():
    ctx = SimpleNamespace(
        accounting=["d1 met: x", "b1 unmet: y", "g1 distance: far", "d2 not due: none"],
        usage_total=lambda: {"in": 1, "out": 1, "cached_in": 9, "cache_write": 3},
        stage_coverage=lambda: {"skipped": ["gather"]}, claims_challenged=1,
        claims_disputed=0, holds=2, schema_retries=1, interventions=0,
        elapsed_total_s=lambda: 12.7)
    assert runrecord.quality(ctx) == {
        "owed": 3, "met": 1, "unmet": 1, "not_due": 1, "stages_skipped": 1, "challenged": 1,
        "disputed": 0, "holds": 2, "schema_retries": 1, "interventions": 0, "elapsed_s": 12,
        "cache_read": 9, "cache_write": 3}
