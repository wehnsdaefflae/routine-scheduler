"""The never-recreate-user-deleted-utils rule: the schema-retry denial, the ask-to-unblock
flow end-to-end, the boot seed-sync guard, and the one-shot header migration.
"""

import shutil
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import finish
from rsched import bootstrap, utils_lib
from rsched.engine.interact import recreate_denial
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json

TS = "20260717-120000"

UTIL_BODY = '''# /// script
# dependencies = []
# ///
"""doomed — a test util.

usage: gu doomed [--selftest]
tags: test
net: none
"""
import sys
if "--selftest" in sys.argv:
    print("selftest: ok", file=sys.stderr)
    sys.exit(0)
print("hi")
'''


def _seed_deleted_util(home, name="doomed"):
    utils_lib.ensure_library(home)
    utils_lib.write_util_file(home, name, UTIL_BODY)
    utils_lib.git_commit(home, f"create {name}")
    shutil.rmtree(utils_lib.util_dir(home, name))
    utils_lib.git_commit(home, f"delete util {name} via web")


def _loop(home, *, depth=0, user_answers=()):
    ctx = SimpleNamespace(server=SimpleNamespace(utils_home=home), depth=depth,
                          user_answers=list(user_answers))
    return SimpleNamespace(ctx=ctx, allowed_tools=None, grants=None)


def _wu(name):
    return {"say": "s", "kind": "write_util", "name": name, "content": UTIL_BODY}


def test_recreate_denial_matrix(tmp_path):
    _seed_deleted_util(tmp_path)
    utils_lib.write_util_file(tmp_path, "alive", UTIL_BODY)

    assert recreate_denial(_loop(tmp_path), {"say": "s", "kind": "util", "name": "doomed"}) == []
    assert recreate_denial(_loop(tmp_path), _wu("alive")) == []          # revision, not recreate
    assert recreate_denial(_loop(tmp_path), _wu("brand-new")) == []      # never existed
    assert recreate_denial(_loop(tmp_path, depth=1), _wu("doomed")) == []  # subruns: own decline
    denial = recreate_denial(_loop(tmp_path), _wu("doomed"))
    assert denial and "DELETED" in denial[0] and "ask_user" in denial[0] and "doomed" in denial[0]
    # an explicit user yes THIS run — an answered ask naming the util — unblocks it
    yes = {"qid": "q1", "question": "Recreate the deleted util 'doomed'?", "answer": "Yes, go."}
    assert recreate_denial(_loop(tmp_path, user_answers=[yes]), _wu("doomed")) == []
    # …but an answer to an unrelated question, or a no, does not
    other = {"qid": "q2", "question": "Ship the release?", "answer": "yes"}
    no = {"qid": "q3", "question": "Recreate the deleted util 'doomed'?", "answer": "no, skip it"}
    assert recreate_denial(_loop(tmp_path, user_answers=[other]), _wu("doomed"))
    assert recreate_denial(_loop(tmp_path, user_answers=[no]), _wu("doomed"))


def test_denial_rides_the_schema_retry_cycle(tmp_path):
    """action_candidate reports the denial as a validation problem — corrected in-cycle,
    never dispatched, exactly like a capability denial."""
    from rsched.engine.completion import action_candidate

    _seed_deleted_util(tmp_path)
    completion = SimpleNamespace(parsed=_wu("doomed"), text="")
    candidate, problems = action_candidate(_loop(tmp_path), completion)
    assert candidate["kind"] == "write_util"
    assert problems and "DELETED" in problems[0]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv required to run utils")
def test_ask_then_recreate_flow(make_routine, scripted):
    """End-to-end: write_util on a deleted slug is corrected in-cycle (no turn), the model
    asks, the user says yes, the retry goes through — and the approval is visible in the
    transcript as a normal answered blocking decision."""
    from test_loop import _server

    d = make_routine(slug="guardian", budgets={"ask_timeout_min": 1})
    server = _server(d)   # confirm "never": the recreate rule must gate on its own
    _seed_deleted_util(server.utils_home)

    def answer_soon():
        deadline = time.time() + 10
        while time.time() < deadline:
            recs = list((d / "questions" / "pending").glob("*.json"))
            if recs:
                from rsched.paths import read_json
                rec = read_json(recs[0])
                atomic_write_json(d / "inbox" / f"answer-{rec['qid']}.json",
                                  {"qid": rec["qid"], "text": "yes", "source": "web"})
                return
            time.sleep(0.02)

    t = threading.Thread(target=answer_soon)
    t.start()
    scripted([
        _wu("doomed"),                                     # rejected in-cycle: deleted slug
        {"say": "asking first", "kind": "ask_user", "mode": "blocking",
         "question": "Recreate the deleted util 'doomed'? The workflow needs it.",
         "default": "skip the util"},
        _wu("doomed"),                                     # unblocked by the answered yes
        finish(),
    ])
    status, run_dir = run_routine(d, server, run_ts=TS)
    t.join()
    assert status == "ok"
    events, _ = read_events(run_dir / "transcript.jsonl")
    schema_errors = [e for e in events if e["type"] == "error"
                     and e["payload"].get("where") == "schema"]
    assert any("DELETED" in e["payload"]["message"] for e in schema_errors)
    # the first write_util never became a turn: exactly ONE write_util observation
    wu_obs = [e for e in events if e["type"] == "observation"
              and e["payload"].get("kind") == "write_util"]
    assert len(wu_obs) == 1 and wu_obs[0]["payload"].get("selftest_ok") is True
    assert utils_lib.exists(server.utils_home, "doomed")


def test_seed_sync_never_resurrects_deleted(tmp_path, monkeypatch):
    """The boot seed-sync obeys the same rule: a user-deleted seed util stays deleted."""
    fake_repo = tmp_path / "repo"
    seed = fake_repo / "util-seed" / "utils" / "doomed"
    seed.mkdir(parents=True)
    (seed / "main.py").write_text(UTIL_BODY, encoding="utf-8")
    monkeypatch.setattr(bootstrap, "repo_root", lambda: fake_repo)
    home = tmp_path / "library"
    utils_lib.ensure_library(home)
    utils_lib.git_commit(home, "init")

    assert bootstrap.sync_seed_utils(home) == 1            # lands like any new seed
    shutil.rmtree(utils_lib.util_dir(home, "doomed"))
    utils_lib.git_commit(home, "delete util doomed via web")
    assert bootstrap.sync_seed_utils(home) == 0            # never resurrected
    assert not utils_lib.exists(home, "doomed")


LEGACY_UTIL = '''# /// script
# dependencies = []
# ///
"""legacy — a pre-sandbox util.

usage: gu legacy
calls: (none)
tags: test
"""
import os, subprocess
key = os.environ.get("LEGACY_API_KEY")
subprocess.run(["gu", "adder", "1", "2"])
'''


def test_migrate_util_headers(tmp_path):
    """MIGRATION(expires=2026-08-17) coverage: legacy headers gain net: outbound (behavior-
    preserving), real `gu` sibling invocations land on calls:, undeclared credential vars
    land on secrets:; a compliant util is untouched and the pass is idempotent."""
    home = tmp_path / "library"
    utils_lib.ensure_library(home)
    utils_lib.write_util_file(home, "legacy", LEGACY_UTIL)
    utils_lib.write_util_file(home, "modern", UTIL_BODY.replace("doomed", "modern"))
    before_modern = utils_lib.read_util(home, "modern")

    assert bootstrap.migrate_util_headers(home) == 1
    migrated = utils_lib.read_util(home, "legacy")
    header = utils_lib.parse_header(migrated)
    assert header["net"] == "outbound"
    assert header["calls"] == ["adder"]
    assert "LEGACY_API_KEY" in header["secrets"]
    assert utils_lib.header_problems(migrated) == []
    assert utils_lib.read_util(home, "modern") == before_modern
    assert bootstrap.migrate_util_headers(home) == 0       # converged: nothing to do
