"""Per-util execution stats (rsched.util_stats): stream aggregation, the coverage rule
(a record carrying `utils` marks its run counted — no transcript double counting),
transcript backfill for pre-stream history (gzip included), and created/revised dates
from a real library git history.
"""

import gzip
import json
import subprocess

from rsched.config import ServerConfig
from rsched.util_stats import util_stats

UTIL_SRC = '''"""fetch — fetches a page.

usage: gu fetch URL
tags: web
"""
print("ok")
'''


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.conversations_home = tmp_path / "conversations"
    s.libraries_home = tmp_path / "library"
    (s.routines_home / ".control").mkdir(parents=True, exist_ok=True)
    return s


def _add_util(server, name, src=UTIL_SRC):
    d = server.libraries_home / "utils" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(src, encoding="utf-8")


def _stream(server, records):
    (server.routines_home / ".control" / "workflow-usage.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _run_with_transcript(server, slug, ts, events, *, gz=False):
    d = server.routines_home / slug
    (d / "runs" / ts).mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(f"slug: {slug}\n", encoding="utf-8")
    data = "".join(json.dumps(e) + "\n" for e in events)
    path = d / "runs" / ts / "transcript.jsonl"
    if gz:
        with gzip.open(path.with_suffix(path.suffix + ".gz"), "wt", encoding="utf-8") as fh:
            fh.write(data)
    else:
        path.write_text(data, encoding="utf-8")


def _obs(name, *, exit_code=0, missing=False, ts="2026-06-01T08:00:00+00:00"):
    payload = {"kind": "util", "name": name}
    if missing:
        payload["missing"] = True
    else:
        payload["exit"] = exit_code
    return {"ts": ts, "type": "observation", "payload": payload}


def test_stream_records_aggregate_and_span(tmp_path):
    server = _server(tmp_path)
    _add_util(server, "fetch")
    _stream(server, [
        {"run_id": "r:1", "ts": "2026-07-01T07:00:00+00:00",
         "utils": {"fetch": {"ok": 2, "error": 1}}},
        {"run_id": "r:2", "ts": "2026-07-03T07:00:00+00:00",
         "utils": {"fetch": {"ok": 1, "denied": 2, "usage_error": 1},
                   "gone-util": {"ok": 1}}},
        # subrun records count their OWN calls (parents never fold them in)
        {"run_id": "r:2#sub1", "ts": "2026-07-03T07:05:00+00:00",
         "utils": {"fetch": {"ok": 1}}},
        {"run_id": "r:3", "ts": "2026-07-04T07:00:00+00:00", "utils": {}},
    ])
    out = util_stats(server)
    rows = {r["name"]: r for r in out["utils"]}
    fetch = rows["fetch"]
    assert fetch["executed"] == 6 and fetch["ok"] == 4
    assert fetch["error"] == 1 and fetch["usage_error"] == 1 and fetch["denied"] == 2
    assert fetch["first_executed"].startswith("2026-07-01")
    assert fetch["last_executed"].startswith("2026-07-03")
    assert fetch["in_library"] is True
    # counted under a name no longer in the library: kept, honestly flagged
    assert rows["gone-util"]["in_library"] is False and rows["gone-util"]["ok"] == 1


def test_backfill_scans_only_uncovered_runs(tmp_path):
    """A run whose stream record carries `utils` was counted at the source — its
    transcript is skipped; a pre-stream run's transcript (gzip included) is scanned."""
    server = _server(tmp_path)
    _add_util(server, "fetch")
    # covered run: stream has utils for it; its transcript would double count if read
    _run_with_transcript(server, "r", "20260701-070000", [_obs("fetch")])
    # legacy runs: no utils key in any record → backfilled, one of them gzipped
    _run_with_transcript(server, "r", "20260601-070000", [
        _obs("fetch", ts="2026-06-01T07:01:00+00:00"),
        _obs("fetch", exit_code=2, ts="2026-06-01T07:02:00+00:00"),
        _obs("fetch", exit_code=1, ts="2026-06-01T07:03:00+00:00"),
        _obs("ghost", missing=True),
        _obs("list"),   # catalog discovery — never counted
        {"ts": "x", "type": "assistant_action", "payload": {"kind": "util", "name": "fetch"}},
    ])
    _run_with_transcript(server, "r", "20260501-070000",
                         [_obs("fetch", ts="2026-05-01T07:00:00+00:00")], gz=True)
    _stream(server, [
        {"run_id": "r:20260701-070000", "ts": "2026-07-01T07:10:00+00:00",
         "utils": {"fetch": {"ok": 1}}},
        {"run_id": "r:20260601-070000", "ts": "2026-06-01T07:10:00+00:00"},  # pre-stream shape
    ])
    out = util_stats(server)
    rows = {r["name"]: r for r in out["utils"]}
    fetch = rows["fetch"]
    # 1 from the stream + 3 from the plain transcript + 1 from the gz one
    assert fetch["executed"] == 5
    assert fetch["ok"] == 3 and fetch["usage_error"] == 1 and fetch["error"] == 1
    assert fetch["first_executed"].startswith("2026-05-01")   # the gz backfill run
    assert fetch["last_executed"].startswith("2026-07-01")    # the stream record
    assert rows["ghost"]["missing"] == 1 and rows["ghost"]["executed"] == 0
    assert "list" not in rows
    assert out["backfill_runs"] == 2

    # second call: memoized per-file, same answer
    assert util_stats(server) == out


def test_git_dates_created_and_revised(tmp_path):
    import os

    server = _server(tmp_path)
    _add_util(server, "fetch")
    lib = server.libraries_home

    def commit(msg, date):
        subprocess.run(["git", "-C", str(lib), "-c", "user.name=t", "-c", "user.email=t@t",
                        "add", "-A"], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(lib), "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", msg], capture_output=True, check=True,
                       env={**os.environ, "GIT_COMMITTER_DATE": date,
                            "GIT_AUTHOR_DATE": date})

    subprocess.run(["git", "-C", str(lib), "init", "-q"], capture_output=True, check=True)
    commit("create fetch", "2026-05-01T10:00:00+00:00")
    (lib / "utils" / "fetch" / "main.py").write_text(UTIL_SRC + "# v2\n", encoding="utf-8")
    commit("revise fetch", "2026-06-15T10:00:00+00:00")
    _add_util(server, "fresh")   # in the library but never committed: no dates

    out = util_stats(server)
    rows = {r["name"]: r for r in out["utils"]}
    assert rows["fetch"]["created"].startswith("2026-05-01")
    assert rows["fetch"]["revised"].startswith("2026-06-15")
    assert rows["fresh"]["created"] is None and rows["fresh"]["revised"] is None
    assert rows["fresh"]["executed"] == 0 and rows["fresh"]["first_executed"] is None


def test_empty_world(tmp_path):
    out = util_stats(_server(tmp_path))
    assert out["utils"] == [] and out["backfill_runs"] == 0


def test_write_snapshot_persists_to_xdg_state(tmp_path, monkeypatch):
    """write_util_stats_snapshot persists util_stats() to snapshot_path() (under
    XDG_STATE_HOME so a Landlock-jailed util can read it) with a `generated` stamp — the
    single file both the Stats tab and the util-review routine's `util-stats` util read."""
    from rsched.util_stats import snapshot_path, write_util_stats_snapshot

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    server = _server(tmp_path)
    _add_util(server, "fetch")
    _stream(server, [
        {"run_id": "r:1", "ts": "2026-07-01T07:00:00+00:00",
         "utils": {"fetch": {"ok": 2, "error": 1}}},
    ])

    returned = write_util_stats_snapshot(server)
    path = snapshot_path()
    assert path == tmp_path / "state" / "routine-scheduler" / "util-stats.json"
    assert path.is_file()

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == returned
    assert on_disk["generated"]                       # ISO stamp present
    rows = {r["name"]: r for r in on_disk["utils"]}
    assert rows["fetch"]["executed"] == 3 and rows["fetch"]["ok"] == 2
