"""Instance-wide full-text search: index build over a fixture tree (both homes, gz
transcripts, subruns), incremental refresh, retention pruning, cache rebuild, query
escaping/injection safety, and the /api/search endpoint.
"""

import gzip
import json
import shutil

import pytest
import yaml
from fastapi.testclient import TestClient

from rsched.config import ServerConfig, load_server_config
from rsched.paths import atomic_write_json
from rsched.search import SearchIndex
from rsched.search.index import MARK_END, MARK_START, _escape_query
from rsched.search.sources import extract, iter_sources
from rsched.web.app import create_app

TOKEN = "search-test-token"


def _write_events(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _build_tree(tmp_path, make_routine):
    """A fixture instance: one routine with two runs (one gz, one with a subrun),
    ledger/memory/decision/recipe files, plus one conversation."""
    d = make_routine(slug="alpha")
    (d / "LEDGER.md").write_text("# LEDGER\n\n### run 1 — zeppelin telemetry feed migrated\n",
                                 encoding="utf-8")
    (d / ".memory").mkdir()
    (d / ".memory" / "note-a.md").write_text("mnemonic pattern for retries", encoding="utf-8")
    (d / ".memory" / "INDEX.md").write_text("- [note-a](note-a.md) — xylograph", encoding="utf-8")
    (d / "stages").mkdir()
    (d / "stages" / "check.md").write_text("# Step: check\nverify the flux capacitor",
                                           encoding="utf-8")
    (d / "questions" / "pending").mkdir(parents=True)
    atomic_write_json(d / "questions" / "pending" / "q1.json",
                      {"qid": "q1", "question": "Deploy the kraken tonight?", "mode": "deferred",
                       "options": ["yes", "no"], "default": "no", "asked": "20260714-070000"})
    run = d / "runs" / "20260701-120000"
    _write_events(run / "transcript.jsonl", [
        {"type": "header", "run_id": "alpha:20260701-120000"},
        {"ts": "2026-07-01T12:00:05+00:00", "type": "assistant_action", "turn": 1,
         "phase": "gather", "payload": {"say": "quantum telemetry captured from the probe",
                                        "kind": "util", "name": "x",
                                        "note": "probe emits chartreuse pulses"}},
        {"ts": "2026-07-01T12:00:06+00:00", "type": "question", "turn": 2,
         "payload": {"qid": "q9", "mode": "blocking", "question": "proceed with the zebra?"}},
        {"ts": "2026-07-01T12:00:07+00:00", "type": "answer",
         "payload": {"qid": "q9", "text": "yes, zebra approved", "source": "web"}},
        {"ts": "2026-07-01T12:00:08+00:00", "type": "user_injection",
         "payload": {"text": "please also check the walrus feed"}},
        {"ts": "2026-07-01T12:00:09+00:00", "type": "user_injection",
         "payload": {"text": "[engine] xylophone maintenance note", "source": "engine"}},
        {"ts": "2026-07-01T12:00:20+00:00", "type": "finish", "turn": 3,
         "payload": {"status": "ok", "summary": "telemetry archived, zebra checked",
                     "authored": True}},
    ])
    (run / "result.md").write_text("Final: zebra checked.", encoding="utf-8")
    (run / "history").mkdir()
    (run / "history" / "001-early.md").write_text("archived unicorn negotiation detail",
                                                  encoding="utf-8")
    _write_events(run / "sub" / "1" / "transcript.jsonl", [
        {"ts": "2026-07-01T12:00:10+00:00", "type": "assistant_action", "turn": 1,
         "payload": {"say": "narwhal analysis in the child task", "kind": "util", "name": "x"}},
    ])
    old = d / "runs" / "20260601-110000"
    old.mkdir(parents=True)
    with gzip.open(old / "transcript.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "t", "type": "assistant_action", "turn": 1,
                             "payload": {"say": "gzipped okapi survey", "kind": "util"}}) + "\n")

    conv = tmp_path / "conversations" / "c-plan"
    (conv / "runs").mkdir(parents=True)
    (conv / "routine.yaml").write_text(yaml.safe_dump(
        {"slug": "c-plan", "description": "a chat", "kind": "conversation"}), encoding="utf-8")
    (conv / "instruction.md").write_text("Help me plan the vaporwave playlist", encoding="utf-8")
    _write_events(conv / "runs" / "20260702-100000" / "transcript.jsonl", [
        {"ts": "2026-07-02T10:00:20+00:00", "type": "finish", "turn": 2,
         "payload": {"status": "ok", "summary": "playlist drafted, seventeen tracks",
                     "authored": True}},
    ])
    return d


@pytest.fixture
def server(tmp_path, make_routine) -> ServerConfig:
    _build_tree(tmp_path, make_routine)
    return ServerConfig(routines_home=tmp_path / "routines",
                        conversations_home=tmp_path / "conversations",
                        background_home=tmp_path / "background",
                        libraries_home=tmp_path / "library")


@pytest.fixture
def index(server) -> SearchIndex:
    idx = SearchIndex(server)
    idx.refresh()
    return idx


def _one(index, q, **expect):
    hits = index.search(q)
    assert hits, f"no hits for {q!r}"
    hit = hits[0]
    for key, want in expect.items():
        assert hit[key] == want, f"{q!r}: {key}={hit[key]!r}, expected {want!r}"
    return hit


def test_kinds_and_metadata(index):
    _one(index, "zeppelin", kind="ledger", home="routine", slug="alpha", run_ts="")
    _one(index, "mnemonic", kind="memory")
    _one(index, "kraken", kind="decision", ts="20260714-070000")
    _one(index, "capacitor", kind="recipe")
    hit = _one(index, "quantum telemetry", kind="say", run_ts="20260701-120000",
               turn=1, phase="gather")
    assert MARK_START in hit["snippet"] and MARK_END in hit["snippet"]
    _one(index, "chartreuse", kind="note")
    _one(index, "walrus", kind="user_message")
    _one(index, "zebra approved", kind="answer")
    _one(index, "proceed", kind="question")
    _one(index, "unicorn", kind="history")
    _one(index, "narwhal", kind="say", sub="1")
    _one(index, "okapi", run_ts="20260601-110000")   # the gzipped transcript
    _one(index, "vaporwave", home="conversation", slug="c-plan", kind="instruction")
    _one(index, "seventeen", home="conversation", kind="finish")


def test_engine_injections_and_memory_index_excluded(index):
    assert index.search("xylophone") == []   # source=engine injection
    assert index.search("xylograph") == []   # .memory/INDEX.md is derived, not indexed


def test_incremental_refresh(server, index):
    stats = index.refresh()
    assert stats["indexed"] == 0 and stats["pending"] == 0   # warm index: nothing to redo
    ledger = server.routines_home / "alpha" / "LEDGER.md"
    ledger.write_text(ledger.read_text(encoding="utf-8") + "\n### run 2 — brontosaurus audit\n",
                      encoding="utf-8")
    assert index.refresh()["indexed"] == 1   # ONLY the changed file was reindexed
    _one(index, "brontosaurus", kind="ledger")


def test_prune_on_retention(server, index):
    assert index.search("okapi")
    before = index.refresh()["files"]
    shutil.rmtree(server.routines_home / "alpha" / "runs" / "20260601-110000")
    stats = index.refresh()
    assert stats["files"] == before - 1
    assert index.search("okapi") == []


def test_pure_cache_rebuilds(server, index):
    from pathlib import Path

    db_path = index.path
    assert db_path.exists()
    index.close()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    fresh = SearchIndex(server)
    fresh.refresh()
    assert fresh.search("zeppelin")


def test_budget_bounds_work(server):
    idx = SearchIndex(server)
    stats = idx.refresh(budget_s=0)
    assert stats["indexed"] == 1   # progress guarantee: exactly one file per zero-budget pass
    assert stats["pending"] == stats["files"] - 1 > 0
    idx.refresh()   # a later unbounded pass finishes the backlog
    assert idx.refresh()["pending"] == 0


def test_newest_runs_index_first(server):
    idx = SearchIndex(server)
    idx.refresh(budget_s=0)   # exactly one file: the newest run's transcript wins the sort
    assert idx.search("seventeen")        # conversation run 20260702-100000 (the newest)
    assert idx.search("zeppelin") == []   # routine-level backlog still pending


def test_query_injection_safe(index):
    for q in ("foo-bar", '"unbalanced', "NEAR(", "a AND", "(((broken", 'quote"in"middle'):
        assert isinstance(index.search(q), list)   # never a 500-shaped exception
    for q in ("   ", "((()))", "*"):   # nothing searchable survives → the API's clean 400
        with pytest.raises(ValueError):
            index.search(q)


def test_escape_query():
    assert _escape_query("foo-bar baz*") == '"foo-bar" "baz"*'
    assert _escape_query('say "hi"') == '"say" """hi"""'
    with pytest.raises(ValueError):
        _escape_query("- … !")


def test_fts5_syntax_still_works(index):
    assert index.search('"quantum telemetry"')      # phrase
    assert index.search("telem*")                   # prefix
    assert index.search("quantum OR vaporwave")     # boolean
    assert index.search("probes")                   # porter stemming: matches "the probe"


def test_limit_cap(index):
    assert len(index.search("the", limit=1)) == 1
    assert isinstance(index.search("the", limit=10_000), list)   # clamped, not rejected


def test_sources_skip_config_and_state(server):
    paths = [str(s.path) for s in iter_sources(server)]
    assert not any(p.endswith(("routine.yaml", "tuning.yaml", "status.json")) for p in paths)
    assert not any("/inbox/" in p or "/state/" in p for p in paths)


def test_extract_tolerates_broken_files(tmp_path, server):
    from rsched.search.sources import SourceFile

    missing = SourceFile(tmp_path / "nope.md", "routine", "alpha", kind="ledger")
    assert extract(missing) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert extract(SourceFile(bad, "routine", "alpha", kind="decision")) == []


def test_long_file_chunks_completely(tmp_path, server, index):
    ledger = server.routines_home / "alpha" / "LEDGER.md"
    filler = "\n\n".join(f"### entry {i} — routine housekeeping prose" for i in range(400))
    ledger.write_text(filler + "\n\n### final — the pangolin clause\n", encoding="utf-8")
    index.refresh()
    _one(index, "pangolin", kind="ledger")   # the tail of a >8k-char file is still indexed


# ---- API ---------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, make_routine):
    _build_tree(tmp_path, make_routine)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "token": TOKEN,
        "routines_home": str(tmp_path / "routines"),
        "conversations_home": str(tmp_path / "conversations"),
        "background_home": str(tmp_path / "background"),
        "libraries_home": str(tmp_path / "library"),
        "endpoints": {"dummy": {"kind": "openai", "base_url": "http://127.0.0.1:1/v1"}},
        "models": {"m": {"endpoint": "dummy", "model": "m"}},
        "system_model": "m",
    }), encoding="utf-8")
    server, problems = load_server_config(cfg_path)
    assert not problems
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def test_api_requires_auth(client):
    bare = TestClient(client.app)
    assert bare.get("/api/search?q=zebra").status_code == 401


def test_api_search_hits(client):
    resp = client.get("/api/search?q=zeppelin")
    assert resp.status_code == 200
    data = resp.json()
    assert data["hits"][0]["slug"] == "alpha"
    assert data["hits"][0]["kind"] == "ledger"
    assert "pending" in data["index"]


def test_api_empty_and_unsearchable_are_400(client):
    assert client.get("/api/search").status_code == 400
    assert client.get("/api/search?q=%20").status_code == 400
    assert client.get("/api/search?q=(((").status_code == 400   # clean 4xx, never a 500


def test_api_limit(client):
    resp = client.get("/api/search?q=the&limit=1")
    assert resp.status_code == 200
    assert len(resp.json()["hits"]) == 1
