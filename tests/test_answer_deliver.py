"""An UNDELIVERED answer's two exits — `deliver` and `settle` (2026-11-09).

Both act on exactly one state, `pickup: never`, and refuse every other with what IS true: an
answer a run reads tonight needs neither, and an unanswered question has nothing to deliver.
`settle` archives the pair the way a run's consumption does and starts nothing; `deliver`
starts exactly one run, through the same seams the answer route and Run now use.
"""

import pytest
import yaml

from conftest import authed_client, make_test_server
from rsched.paths import atomic_write_json, read_json


@pytest.fixture
def client(tmp_path, make_routine):
    d = make_routine(slug="apir")
    cfg = yaml.safe_load((d / "routine.yaml").read_text())
    cfg["schedule"]["cron"] = ""                 # no clock, no lane, no trigger: never read
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg))
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, d


def _pending(d, qid):
    atomic_write_json(d / "questions" / "pending" / f"{qid}.json",
                      {"qid": qid, "question": "Go?", "options": [], "asked": "20261109",
                       "mode": "deferred"})


def _answer(d, qid):
    atomic_write_json(d / "inbox" / f"answer-{qid}.json",
                      {"qid": qid, "text": "yes please", "source": "web", "ts": "t"})


def test_settle_archives_the_pair_and_starts_nothing(client):
    c, d = client
    _pending(d, "q-s1")
    _answer(d, "q-s1")
    fired = []
    c.app.state.runner.fire = lambda *a, **k: fired.append(a)
    r = c.post("/api/questions/q-s1/settle")
    assert r.status_code == 200 and r.json()["settled"] is True
    assert not (d / "questions" / "pending" / "q-s1.json").exists()
    assert not (d / "inbox" / "answer-q-s1.json").exists()
    record = read_json(d / "questions" / "answered" / "q-s1.json")
    assert record["answer"] == "yes please" and record["settled_by"] == "operator"
    assert not fired
    row = next(q for q in c.get("/api/questions?settled=5").json()["items"]
               if q["qid"] == "q-s1")
    assert row["settled"] is True


def test_deliver_fires_one_manual_run_of_the_routine(client):
    c, d = client
    _pending(d, "q-d1")
    _answer(d, "q-d1")
    calls = []

    async def fire(cfg, *, reason="schedule", brief=""):
        calls.append((cfg.slug, reason))
        return "apir:20261109-170000"

    c.app.state.runner.fire = fire
    r = c.post("/api/questions/q-d1/deliver")
    assert r.status_code == 200, r.text
    assert r.json()["run_id"] == "apir:20261109-170000"
    assert calls == [("apir", "manual")]


def test_both_refuse_an_answer_that_has_a_reader(client):
    c, d = client
    from datetime import UTC, datetime
    _pending(d, "q-r1")
    _answer(d, "q-r1")
    c.app.state.scheduler.next_fires["apir"] = datetime(2026, 11, 11, tzinfo=UTC)
    cfg = yaml.safe_load((d / "routine.yaml").read_text())
    cfg["schedule"]["cron"] = "0 3 * * *"
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg))
    for verb in ("deliver", "settle"):
        r = c.post(f"/api/questions/q-r1/{verb}")
        assert r.status_code == 409 and "already has a reader" in r.json()["detail"]
    assert (d / "inbox" / "answer-q-r1.json").exists()


def test_both_refuse_an_unanswered_question(client):
    c, d = client
    _pending(d, "q-u1")
    for verb in ("deliver", "settle"):
        r = c.post(f"/api/questions/q-u1/{verb}")
        assert r.status_code == 409 and "not answered yet" in r.json()["detail"]
