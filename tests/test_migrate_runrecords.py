"""The one-shot run-record backfill (migrate_runrecords.py, runevidence.py) — every past run's
usage record gains the fingerprint and quality a live run records, rebuilt from git and the
run's own files, with what could not be recovered named UNKNOWN rather than guessed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from conftest import git_in
from helpers import server_for
from rsched import migrate_runrecords as m
from rsched import runevidence as ev
from rsched.config import load_routine
from rsched.engine import runrecord
from rsched.readmodels.incarnations import incarnation_of, incarnations

T0 = "2026-08-01T00:00:00+00:00"
RUN = "20260805-070000"
ROUTINE = {"name": "Digest", "slug": "digest", "description": "d",
           "schedule": {"cron": "0 7 * * *", "tz": "Etc/UTC"},
           "budgets": {"max_turns": 40}, "rules": ["web-research"], "permissions": []}


def _commit_all(d: Path, date: str = T0) -> None:
    git_in(d, "add", "-A", date=date)
    git_in(d, "commit", "-qm", "c", date=date)


def _instance(tmp_path: Path, monkeypatch, routine: dict | None = None) -> Path:
    """A routines home with one versioned routine, a library holding its rule, and an engine
    checkout whose `main` has held 0.300.0 since T0."""
    home = tmp_path / "routines"
    d = home / "digest"
    (d / "runs").mkdir(parents=True)
    git_in(d, "init", "-q")
    (d / "routine.yaml").write_text(yaml.safe_dump(routine or ROUTINE), encoding="utf-8")
    (d / "main.md").write_text("# recipe\n", encoding="utf-8")
    _commit_all(d)
    lib = tmp_path / "test-library"
    (lib / "rules").mkdir(parents=True)
    git_in(lib, "init", "-q")
    (lib / "rules" / "web-research.md").write_text("# rule: web-research\n", encoding="utf-8")
    _commit_all(lib)
    src = tmp_path / "src"
    (src / "src" / "rsched").mkdir(parents=True)
    git_in(src, "init", "-q", "-b", "main")
    (src / "src" / "rsched" / "__init__.py").write_text('__version__ = "0.300.0"\n',
                                                        encoding="utf-8")
    _commit_all(src)
    monkeypatch.setattr(m, "repo_root", lambda: src)
    return home


def _write_run(run_dir: Path, *, status: dict, events: list[dict]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def _stream(home: Path) -> Path:
    return home / ".control" / "workflow-usage.jsonl"


def _rows(home: Path) -> list[dict]:
    rows = []
    for line in _stream(home).read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _leg(run_id: str, **extra: object) -> dict:
    return {"ts": "2026-08-05T07:30:00+00:00", "routine": run_id.split(":", maxsplit=1)[0],
            "run_id": run_id, "depth": 0, "status": "ok", "turns": 9, "tokens": 30_000, **extra}


EVENTS = [
    {"type": "header", "orchestrator": {"endpoint": "claude", "model": "opus"}},
    {"type": "user_injection", "payload": {"text": "go", "boot": True}},
    {"type": "assistant_action", "payload": {"kind": "util"}},
    {"type": "user_injection", "payload": {"text": "use the short form"}},
    {"type": "user_injection", "payload": {"text": "ANSWER to your deferred question “x”:\ny"}},
    {"type": "user_injection", "payload": {"text": "[RULE x] read it", "source": "assist"}},
    {"type": "user_injection", "payload": {"text": "a report", "via": "report"}},
    {"type": "observation", "payload": {"kind": "finish", "stopping_unsupported": ["s1"]}},
    {"type": "stopping_update", "payload": {"disputed": ["s1"]}},
    {"type": "observation", "payload": {"kind": "reminder_hold"}},
    {"type": "finish", "payload": {}},
]
STATUS = {"deliberation": "standard", "schema_retries": 2, "elapsed_s": 300,
          "accounting": ["d1 met: x", "d2 unmet: y"], "stages": {"skipped": ["scan"]},
          "usage": {"in": 1, "out": 1, "cached_in": 90, "cache_write": 10}}


def test_a_past_run_is_rebuilt_as_of_the_moment_it_started(tmp_path, monkeypatch):
    home = _instance(tmp_path, monkeypatch)
    _write_run(home / "digest" / "runs" / RUN, status=STATUS, events=EVENTS)
    conversation = json.dumps(_leg("c-20260805-070000:20260805-070000"))
    _stream(home).parent.mkdir(parents=True)
    _stream(home).write_text("\n".join([
        json.dumps(_leg(f"digest:{RUN}", turns=4, recipe_commit="r1")), conversation, "{torn",
        json.dumps(_leg(f"digest:{RUN}", recipe_commit="r1"))]) + "\n", encoding="utf-8")

    record = m.run_migration(server_for(home / "digest"))

    assert (record["runs"], record["with_quality"]) == (1, 1)
    lines = _stream(home).read_text(encoding="utf-8").splitlines()
    assert lines[1] == conversation and lines[2] == "{torn"           # untouched, byte for byte
    first, last = (r for r in _rows(home) if r["routine"] == "digest")
    assert first["fingerprint"] == last["fingerprint"]                  # every leg alike
    fp, q = last["fingerprint"], last["quality"]
    cfg, _ = load_routine(home / "digest")
    assert fp["source"] == "reconstructed" and fp["engine"] == "0.300.0"
    assert fp["config"] == runrecord.config_hash(cfg)                   # the live hasher
    assert fp["rules"] == {"web-research": runrecord.short_hash("# rule: web-research\n")}
    assert fp["model_id"] == "claude/opus" and fp["deliberation"] == "standard"
    assert fp["unknown"] == ["effort", "model"]                         # the system model's
    assert "recipe" not in fp                                           # the record names it
    assert q == {"owed": 2, "met": 1, "unmet": 1, "not_due": 0, "stages_skipped": 1,
                 "schema_retries": 2, "elapsed_s": 300, "cache_read": 90, "cache_write": 10,
                 "challenged": 1, "disputed": 1, "interventions": 1}    # holds: not yet recorded
    assert m.run_migration(server_for(home / "digest")) == {}           # one-shot


def test_a_named_model_is_known_and_a_pruned_run_keeps_no_quality(tmp_path, monkeypatch):
    home = _instance(tmp_path, monkeypatch, {**ROUTINE, "models": {"main": "Opus high"}})
    (home / ".control").mkdir()
    _stream(home).write_text(json.dumps(_leg(f"digest:{RUN}")) + "\n", encoding="utf-8")
    m.run_migration(server_for(home / "digest"))
    (row,) = _rows(home)
    assert row["fingerprint"]["model"] == "Opus high" and "quality" not in row
    assert row["fingerprint"]["unknown"] == ["effort", "model_id"]      # never from the catalog
    assert row["fingerprint"]["recipe"]                                 # dated from git instead


def test_staged_evidence_stands_in_for_a_pruned_run_dir_and_is_removed(tmp_path, monkeypatch):
    home = _instance(tmp_path, monkeypatch)
    _write_run(home / m.EVIDENCE / "digest" / RUN, status=STATUS, events=EVENTS)
    _stream(home).write_text(json.dumps(_leg(f"digest:{RUN}")) + "\n", encoding="utf-8")
    m.run_migration(server_for(home / "digest"))
    (row,) = _rows(home)
    assert row["quality"]["interventions"] == 1 and row["fingerprint"]["model_id"]
    assert not (home / m.EVIDENCE).exists()


def test_a_live_run_keeps_its_fingerprint_but_takes_the_corrections(tmp_path, monkeypatch):
    """0.398.0 hashed the wrong fields and recorded no model id — both corrected in place."""
    home = _instance(tmp_path, monkeypatch)
    _write_run(home / "digest" / "runs" / RUN,
               status={"model": "claude-proxy/claude-opus-5"}, events=[])
    live = {"engine": "0.398.0", "model": "Opus medium", "effort": "medium",
            "deliberation": "standard", "config": "stale", "rules": {}}
    (home / ".control").mkdir()
    _stream(home).write_text(json.dumps(_leg(f"digest:{RUN}", fingerprint=live)) + "\n",
                             encoding="utf-8")
    record = m.run_migration(server_for(home / "digest"))
    (row,) = _rows(home)
    cfg, _ = load_routine(home / "digest")
    assert record["amended"] == 1 and "source" not in row["fingerprint"]
    assert row["fingerprint"]["config"] == runrecord.config_hash(cfg)
    assert row["fingerprint"]["model_id"] == "claude-proxy/claude-opus-5"
    assert row["fingerprint"]["effort"] == "medium"                     # the rest untouched


def test_a_stream_that_grew_meanwhile_is_left_alone_and_retried(tmp_path, monkeypatch):
    home = _instance(tmp_path, monkeypatch)
    (home / ".control").mkdir()
    _stream(home).write_text(json.dumps(_leg(f"digest:{RUN}")) + "\n", encoding="utf-8")
    real = m.rebuild

    def appending(builder, records):
        with _stream(home).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_leg("digest:20260806-070000")) + "\n")
        return real(builder, records)

    monkeypatch.setattr(m, "rebuild", appending)
    record = m.run_migration(server_for(home / "digest"))
    assert "skipped" in record and not (home / m.RECORD).exists()
    assert [r.get("fingerprint") for r in _rows(home)] == [None, None]  # nothing lost or written


def test_a_slug_archived_and_created_again_names_two_routines(tmp_path, monkeypatch):
    home = _instance(tmp_path, monkeypatch)
    old = home / ".archive" / "digest-20260803-000000"
    old.mkdir(parents=True)
    git_in(old, "init", "-q")
    (old / "routine.yaml").write_text(yaml.safe_dump({**ROUTINE, "budgets": {"max_turns": 5}}),
                                      encoding="utf-8")
    _commit_all(old)
    incs = incarnations(home, "digest")
    assert [i.name for i in incs] == ["digest-20260803-000000", "digest"]
    assert incarnation_of(incs, "20260802-120000").name == "digest-20260803-000000"
    assert incarnation_of(incs, RUN).live
    (home / ".control").mkdir()
    _stream(home).write_text("".join(json.dumps(_leg(f"digest:{ts}")) + "\n"
                                     for ts in ("20260802-120000", RUN)), encoding="utf-8")
    m.run_migration(server_for(home / "digest"))
    before, after = (r["fingerprint"]["config"] for r in _rows(home))
    assert before == runrecord.config_hash(load_routine(old)[0]) != after


@pytest.mark.parametrize(("engine", "counted"), [
    ("0.200.0", set()), ("0.250.0", {"challenged", "disputed"}),
    ("0.305.0", {"challenged", "disputed", "holds"}), ("", set())])
def test_a_count_its_release_could_not_record_is_unknown_not_zero(engine, counted):
    q = ev.quality_of({}, EVENTS, engine) or {}
    assert {"challenged", "disputed", "holds"} & set(q) == counted
    assert q["interventions"] == 1


def test_only_the_persons_own_words_mid_run_are_interventions():
    """Not the leg's opening message, not the answer the run asked for, not an engine note or a
    rule assist (each carries a `source`), not a report or a machine channel."""
    assert ev.interventions(EVENTS) == 1
    machine = {"type": "user_injection", "payload": {"text": "done", "via": "background"}}
    web = {"type": "user_injection", "payload": {"text": "stop", "via": "web"}}
    acting = [{"type": "assistant_action", "payload": {}}]
    assert ev.interventions([*acting, machine, web]) == 1
    assert ev.interventions([web]) == 0                                 # before it acted: boot


def test_the_model_a_run_ran_on_comes_from_its_own_files():
    header = [{"type": "header", "orchestrator": {"endpoint": "e", "model": "m"}}]
    assert ev.model_id_of({"model": "x/y"}, header) == "e/m"
    assert ev.model_id_of({"model": "x/y"}, []) == "x/y"
    assert ev.model_id_of({"model": "Opus"}, []) == ""                  # a name, not an id
    assert ev.started("20260805-070000").isoformat() == "2026-08-05T07:00:00+00:00"
