"""The Decisions read model's memo. `/api/questions` is the most-fetched read model in the
console (five surfaces, once per open tab, on every bus event), so a call over unchanged
sources must cost no catalog walk — and every source that can change the answer must miss:
a new record, an answer, a snooze, a run's state, a routine appearing or vanishing, the
audit report. 2026-09-14: 10-20 unmemoized copies in flight at 7-25 s each starved the
daemon until the settings page would not open."""

from __future__ import annotations

import shutil

import yaml

from conftest import make_test_server, mk_run
from rsched.paths import atomic_write_json
from rsched.readmodels import memo
from rsched.readmodels.items import SELF_AUDIT_SLUG
from rsched.web import decisions_read

RUN = "20260708-110000"


def _server(tmp_path):
    memo.reset()
    return make_test_server(tmp_path, conversations_home=str(tmp_path / "conversations"),
                            background_home=str(tmp_path / "background"))


class _Walks:
    """Count the catalog walks the read model performs, per home kind, plus the audit
    report parses — the memo's whole promise is how many of these a call costs."""

    def __init__(self, monkeypatch):
        self.homes: list[str] = []
        self.audit = 0
        fresh, audit_fresh = decisions_read._all_questions_fresh, decisions_read._audit_decisions_fresh

        def counted(server, home_kind, home):
            self.homes.append(home_kind)
            return fresh(server, home_kind, home)

        def counted_audit(rdir):
            self.audit += 1
            return audit_fresh(rdir)

        monkeypatch.setattr(decisions_read, "_all_questions_fresh", counted)
        monkeypatch.setattr(decisions_read, "_audit_decisions_fresh", counted_audit)
        self._seen = 0

    def routine_walks(self) -> int:
        """Routine-home walks since the previous call."""
        now = self.homes.count("routine")
        delta, self._seen = now - self._seen, now
        return delta


def _pending(routine_dir, qid, **extra):
    atomic_write_json(routine_dir / "questions" / "pending" / f"{qid}.json",
                      {"qid": qid, "question": f"{qid}?", "options": [], "mode": "deferred",
                       **extra})


def _write_routine(home, slug, **extra):
    d = home / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(yaml.safe_dump({"name": slug, "slug": slug, "enabled": True,
                                                    **extra}), encoding="utf-8")
    return d


def test_unchanged_sources_reuse_the_walk_and_hand_out_copies(tmp_path, make_routine,
                                                               monkeypatch):
    d = make_routine(slug="apir")
    server = _server(tmp_path)
    walks = _Walks(monkeypatch)
    _pending(d, "q1")
    first = decisions_read.open_decisions(server)
    assert [q["qid"] for q in first] == ["q1"]
    assert walks.homes == ["routine", "conversation", "background"] and walks.audit == 1
    first[0]["poisoned"] = True                      # a caller mutating what it was handed…
    again = decisions_read.open_decisions(server)
    assert walks.homes == ["routine", "conversation", "background"] and walks.audit == 1
    assert again[0]["qid"] == "q1" and "poisoned" not in again[0]   # …never reaches the cache


def test_each_kind_of_source_change_misses_exactly_once(tmp_path, make_routine, monkeypatch):
    d = make_routine(slug="apir")
    server = _server(tmp_path)
    walks = _Walks(monkeypatch)

    def by() -> dict[str, dict]:
        return {q["qid"]: q for q in decisions_read.open_decisions(server)}

    assert by() == {} and walks.routine_walks() == 1
    assert by() == {} and walks.routine_walks() == 0

    # a new deferred record
    _pending(d, "q1", asked=RUN)
    assert "q1" in by() and walks.routine_walks() == 1
    # its answer landing in the inbox flips it to answered — the inbox dir is a source
    atomic_write_json(d / "inbox" / "answer-q1.json", {"qid": "q1", "text": "yes", "source": "web"})
    assert by()["q1"]["answered"] is True and walks.routine_walks() == 1
    # a snooze is a rewrite of the record itself
    _pending(d, "q1", asked=RUN, snoozed_until="2999-01-01T00:00:00+00:00")
    assert by()["q1"]["snoozed"] is True and walks.routine_walks() == 1
    # a run armed on the record's `asked`: a blocking question appears and the deferred record
    # links back to the run's live state
    mk_run(d, RUN, "waiting_user", question={"qid": "q-b", "question": "Block?", "options": []})
    qs = by()
    assert qs["q-b"]["mode"] == "blocking" and qs["q1"]["run_state"] == "waiting_user"
    assert walks.routine_walks() == 1
    # the run's status.json rewritten (it finished) — nothing else moved, and it is still seen:
    # the blocking item leaves, the linked state follows
    mk_run(d, RUN, "finished")
    qs = by()
    assert "q-b" not in qs and qs["q1"]["run_state"] == "finished"
    assert walks.routine_walks() == 1
    # a routine dir appearing in two steps: the mkdir stamps the home, the yaml lands only
    # after a call has already listed the dir — the per-subdir routine.yaml marker sees it
    new = tmp_path / "routines" / "later"
    (new / "questions" / "pending").mkdir(parents=True)
    _pending(new, "q-later")
    assert "q-later" not in by() and walks.routine_walks() == 1   # listed, not a routine yet
    _write_routine(tmp_path / "routines", "later")
    assert by()["q-later"]["routine"] == "later" and walks.routine_walks() == 1
    # and vanishing
    shutil.rmtree(new)
    assert "q-later" not in by() and walks.routine_walks() == 1
    # nothing changed → no walk, and the other homes never re-walked at all
    by()
    assert walks.routine_walks() == 0
    assert walks.homes.count("conversation") == 1 and walks.homes.count("background") == 1


def test_audit_decisions_ride_their_own_memo(tmp_path, make_routine, monkeypatch):
    make_routine(slug="apir")
    server = _server(tmp_path)
    walks = _Walks(monkeypatch)
    audit = tmp_path / "routines" / SELF_AUDIT_SLUG / "audit"

    def by() -> dict[str, dict]:
        return {q["qid"]: q for q in decisions_read.open_decisions(server)}

    assert by() == {} and walks.audit == 1
    atomic_write_json(audit / "report.json",
                      {"generated": "2026-09-14T08:00:00+00:00",
                       "decisions": [{"id": "D1", "title": "Pick", "options": ["a", "b"]}]})
    assert by()["audit:D1"]["meta"] is True and walks.audit == 2
    walks.routine_walks()
    # the report rewritten with the decision settled: only the audit memo misses — the
    # routine home's sources did not move
    atomic_write_json(audit / "report.json",
                      {"generated": "2026-09-14T09:00:00+00:00",
                       "decisions": [{"id": "D1", "title": "Pick", "options": ["a", "b"],
                                      "status": "settled"}]})
    assert "audit:D1" not in by() and walks.audit == 3 and walks.routine_walks() == 0


def test_homes_are_memoized_apart(tmp_path, make_routine, monkeypatch):
    """A change in one home re-walks that home only; the three lists keep their markers."""
    make_routine(slug="apir")
    server = _server(tmp_path)
    conv = _write_routine(tmp_path / "conversations", "conv1")
    task = _write_routine(tmp_path / "background", "task1", owner={"slug": "conv1"})
    walks = _Walks(monkeypatch)
    _pending(conv, "q-conv")
    _pending(task, "q-task")
    by = {q["qid"]: q for q in decisions_read.open_decisions(server)}
    assert by["q-conv"]["conversation"] is True
    assert by["q-task"]["background"] is True and by["q-task"]["owner"] == "conv1"
    assert walks.homes == ["routine", "conversation", "background"]
    _pending(conv, "q-conv-2")
    assert "q-conv-2" in {q["qid"] for q in decisions_read.open_decisions(server)}
    assert walks.homes == ["routine", "conversation", "background", "conversation"]
