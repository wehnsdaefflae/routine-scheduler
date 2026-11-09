"""The daily owed-work sweep files `answer_undelivered` for an answer NO run will read.

The Decisions page lists such an answer as UNDELIVERED the moment it is in that state; this is
the reminder on the blocked-work surface for one nobody went back to look at — two sat seven
weeks (2026-09-21) before either existed. Only `never` is filed, and only past a day: an answer
a run reads tonight, or one given an hour ago, is nobody's problem yet.
"""

import json
import os
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import yaml

from conftest import seeded_server
from rsched.daemon.owed_watch import OwedWatch
from rsched.paths import atomic_write_json


def _waiting(d, qid, *, days_old):
    atomic_write_json(d / "questions" / "pending" / f"{qid}.json",
                      {"qid": qid, "question": "?", "options": [], "asked": "1",
                       "mode": "deferred"})
    answer = d / "inbox" / f"answer-{qid}.json"
    atomic_write_json(answer, {"qid": qid, "text": "weekly", "source": "web", "ts": "t"})
    stamp = time.time() - days_old * 86400
    os.utime(answer, (stamp, stamp))


def _no_clock(d):
    cfg = yaml.safe_load((d / "routine.yaml").read_text())
    cfg["schedule"]["cron"] = ""
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg))


def test_only_an_old_answer_with_no_reader_is_filed(tmp_path, make_routine):
    server = seeded_server(tmp_path)
    home = server.routines_home
    idle = make_routine(slug="idle")
    _no_clock(idle)
    _waiting(idle, "q-idle", days_old=3)                 # no clock, three days: filed
    busy = make_routine(slug="busy")
    _waiting(busy, "q-busy", days_old=3)                 # a fire tonight: not filed
    conv = server.conversations_home / "c-done"
    _waiting(conv, "q-conv", days_old=2)                 # a finished conversation: filed
    fresh = server.conversations_home / "c-fresh"
    _waiting(fresh, "q-fresh", days_old=0)               # answered an hour ago: not yet

    sched = SimpleNamespace(
        next_fires={"busy": datetime.now(UTC) + timedelta(hours=6)}, lane_next_fires={},
        runner=SimpleNamespace(is_active=lambda _home, _slug: False))
    OwedWatch(server, sched).check(datetime.now(UTC).date())

    events = [json.loads(x) for x in (home / ".control" / "health-events.jsonl").read_text()
              .splitlines()]
    filed = {e["routine"]: e for e in events if e["event"] == "answer_undelivered"}
    assert set(filed) == {"idle", "c-done"}
    assert "q-idle" in filed["idle"]["detail"] and "no schedule" in filed["idle"]["detail"]
    assert "conversation has finished" in filed["c-done"]["detail"]
    assert filed["idle"]["answers"] == 1


def test_without_a_scheduler_nothing_is_claimed(tmp_path, make_routine):
    server = seeded_server(tmp_path)
    idle = make_routine(slug="idle")
    _no_clock(idle)
    _waiting(idle, "q-idle", days_old=3)
    OwedWatch(server).check(datetime.now(UTC).date())
    stream = server.routines_home / ".control" / "health-events.jsonl"
    assert not stream.exists() or "answer_undelivered" not in stream.read_text()
