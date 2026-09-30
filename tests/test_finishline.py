"""The goal model: a routine's FINISH LINE (`engine/finishline.py`), what one run delivers (the
recipe's `## Done when`, `engine/donewhen.py`), the finish ACCOUNTING that answers for both
(`engine/accounting.py`), the digest block that says so at boot (`engine/finish_digest.py`) and
the routine page's endpoint (`web/api_finishline.py`).

The three questions have three owners; most of what is pinned here is that none of them can
speak for another: a run cannot claim an outcome only the operator judges, the operator's save
cannot erase what a run reported, and a date outcome is nobody's verdict but the calendar's.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from rsched.engine import accounting, donewhen, finish_digest, finishline

DAY = dt.date(2026, 9, 29)


def _line(tmp_path, outcomes, until=""):
    return finishline.save(tmp_path, {"outcomes": outcomes, "until": until}, now="t")


# ---- the finish line ---------------------------------------------------------------------------

def test_save_assigns_stable_ids_and_keeps_existing_ones(tmp_path):
    doc = _line(tmp_path, [{"text": "submitted", "judge": "run"},
                           {"id": "g4", "text": "funded"},
                           {"text": "  "}])
    assert [o["id"] for o in doc["outcomes"]] == ["g1", "g4"]      # a blank outcome is dropped
    assert doc["outcomes"][1]["judge"] == "you"                    # the default judge
    again = _line(tmp_path, [*doc["outcomes"], {"text": "reported"}])
    assert [o["id"] for o in again["outcomes"]] == ["g1", "g4", "g2"]


def test_a_missing_or_corrupt_file_is_the_empty_finish_line(tmp_path):
    assert finishline.load(tmp_path) == {"outcomes": [], "until": ""}
    finishline.path(tmp_path).parent.mkdir(parents=True)
    finishline.path(tmp_path).write_text("{not json", encoding="utf-8")
    assert finishline.load(tmp_path) == {"outcomes": [], "until": ""}


def test_a_date_outcome_keeps_its_date_and_no_other_judge_does():
    doc = finishline.normalize({"outcomes": [
        {"text": "deadline", "judge": "date", "date": "2026-10-01"},
        {"text": "x", "judge": "run", "date": "2026-10-01"},
        {"text": "y", "judge": "date", "date": "soon"}]})
    assert [o["date"] for o in doc["outcomes"]] == ["2026-10-01", "", ""]
    assert finishline.problems(doc) == ["y: a date outcome needs its date (YYYY-MM-DD)"]


@pytest.mark.parametrize(("words", "expected"), [
    (["run: the PDF is submitted"], [("run", "the PDF is submitted", "")]),
    (["you: Mark is happy with it"], [("you", "Mark is happy with it", "")]),
    (["2026-10-15: the call closes"], [("date", "the call closes", "2026-10-15")]),
    (["whatever the person said"], [("you", "whatever the person said", "")]),
])
def test_from_words_reads_the_judge_from_the_line(words, expected):
    doc = finishline.from_words(words)
    assert [(o["judge"], o["text"], o["date"]) for o in doc["outcomes"]] == expected


def test_from_words_reads_until_apart_from_the_outcomes():
    doc = finishline.from_words(["run: submitted", "until 2026-12-31"])
    assert doc["until"] == "2026-12-31" and len(doc["outcomes"]) == 1


def test_until_ends_the_routine_whatever_the_outcomes_say(tmp_path):
    _line(tmp_path, [{"text": "submitted", "judge": "run"}], until="2026-09-28")
    assert finishline.reached(finishline.load(tmp_path), DAY) == \
        "its end date 2026-09-28 has passed"
    assert finishline.reached(finishline.load(tmp_path), dt.date(2026, 9, 28)) == ""


def test_every_outcome_must_be_reached(tmp_path):
    _line(tmp_path, [{"text": "a", "judge": "date", "date": "2026-09-01"},
                     {"text": "b", "judge": "you"}])
    assert finishline.reached(finishline.load(tmp_path), DAY) == ""
    _line(tmp_path, [{"id": "g1", "text": "a", "judge": "date", "date": "2026-09-01"},
                     {"id": "g2", "text": "b", "judge": "you", "status": "met"}])
    assert finishline.reached(finishline.load(tmp_path), DAY) == \
        "every outcome of its finish line is reached"


def test_no_outcomes_and_no_until_runs_until_switched_off(tmp_path):
    assert finishline.reached(finishline.load(tmp_path), DAY) == ""
    assert not finishline.goal_reached(tmp_path, DAY)


def test_a_run_only_lands_met_on_an_outcome_it_judges(tmp_path):
    _line(tmp_path, [{"text": "a", "judge": "run"}, {"text": "b", "judge": "you"},
                     {"text": "c", "judge": "date", "date": "2027-01-01"}])
    newly = finishline.record(tmp_path, {"g1": ("met", "the portal says received"),
                                         "g2": ("met", "I think so"),
                                         "g3": ("met", "sure")}, run_id="r:1", now="t")
    assert newly == ["g1"]
    rows = {o["id"]: o for o in finishline.load(tmp_path)["outcomes"]}
    assert rows["g1"]["status"] == "met" and rows["g1"]["evidence"] == "the portal says received"
    assert rows["g1"]["met_run"] == "r:1"
    assert rows["g2"]["status"] == "open" and rows["g3"]["status"] == "open"


def test_a_distance_is_kept_per_outcome_for_the_next_reader(tmp_path):
    _line(tmp_path, [{"text": "b", "judge": "you"}])
    finishline.record(tmp_path, {"g1": ("distance", "two sections left")}, run_id="r:2",
                      now="t2")
    row = finishline.load(tmp_path)["outcomes"][0]
    assert (row["distance"], row["distance_run"], row["distance_ts"]) == \
        ("two sections left", "r:2", "t2")


def test_a_met_outcome_stays_met_until_the_operator_reopens_it(tmp_path):
    _line(tmp_path, [{"text": "a", "judge": "run"}])
    finishline.record(tmp_path, {"g1": ("met", "done")}, run_id="r:1", now="t")
    finishline.record(tmp_path, {"g1": ("distance", "hm")}, run_id="r:2", now="t")
    assert finishline.load(tmp_path)["outcomes"][0]["status"] == "met"
    assert finishline.reopen(tmp_path) == ["g1"]
    row = finishline.load(tmp_path)["outcomes"][0]
    assert row["status"] == "open" and row["evidence"] == "done"      # the evidence is kept


def test_an_operator_save_never_erases_what_a_run_reported(tmp_path):
    _line(tmp_path, [{"text": "b", "judge": "you"}])
    finishline.record(tmp_path, {"g1": ("distance", "one step left")}, run_id="r:1", now="t",
                      disputes={"g1": "unused"})
    _line(tmp_path, [{"id": "g1", "text": "b, reworded", "judge": "you",
                      "distance": "forged", "evidence": "forged"}])
    row = finishline.load(tmp_path)["outcomes"][0]
    assert row["text"] == "b, reworded" and row["distance"] == "one step left"
    assert row["evidence"] == ""


def test_the_operators_own_tick_is_stamped(tmp_path):
    doc = finishline.save(tmp_path, {"outcomes": [{"text": "b", "judge": "you",
                                                   "status": "met"}]}, now="2026-09-29T10:00")
    assert doc["outcomes"][0]["met_ts"] == "2026-09-29T10:00"


def test_a_disputed_met_keeps_the_objection(tmp_path):
    """The model keeps the last word; the engine keeps the disagreement for the operator."""
    _line(tmp_path, [{"text": "a", "judge": "run"}])
    finishline.record(tmp_path, {"g1": ("met", "I did")}, run_id="r:1", now="t",
                      disputes={"g1": "no action opened the file"})
    row = finishline.load(tmp_path)["outcomes"][0]
    assert row["status"] == "met" and row["disputed"] == "no action opened the file"


# ---- done when ---------------------------------------------------------------------------------

RECIPE = """# Recipe

## Run flow

1. gather, then publish.

## Done when

- d1 · gather — every due source was read, or recorded as unreadable
- d2 · publish — the page is published and read back
- d3 — the ledger records what was decided and why

## Never

- publish unverified numbers
"""


def test_done_when_reads_ids_stages_and_text():
    assert donewhen.parse(RECIPE) == [
        {"id": "d1", "stage": "gather",
         "text": "every due source was read, or recorded as unreadable"},
        {"id": "d2", "stage": "publish", "text": "the page is published and read back"},
        {"id": "d3", "stage": "", "text": "the ledger records what was decided and why"}]


def test_a_recipe_without_the_section_owes_nothing():
    assert donewhen.parse("# Recipe\n\nDo it.\n") == []
    assert donewhen.problems("# Recipe\n\nDo it.\n") == []


def test_done_when_problems_name_format_ids_and_stages():
    bad = RECIPE.replace("- d2 · publish", "- d4 · pubish").replace("- d3 —", "* d3 —")
    found = donewhen.problems(bad, {"gather", "publish"})
    assert any("is not `- d<n>" in p for p in found)
    assert "Done when d4: no stage module stages/pubish.md" in found
    assert "Done when: ids run d1, d2, … in order with no gaps" in found
    assert donewhen.problems(RECIPE, {"gather", "publish"}) == []


def test_an_empty_section_is_a_problem():
    assert "Done when: the section lists no outcome" in donewhen.problems(
        "# R\n\n## Done when\n\n## Never\n\n- x\n")


# ---- the accounting ----------------------------------------------------------------------------

DONE = [{"id": "d1", "stage": "", "text": "a"}, {"id": "d2", "stage": "", "text": "b"}]
OUTCOMES = [{"id": "g1", "judge": "run", "text": "x"}, {"id": "g2", "judge": "you", "text": "y"}]


def test_parse_reads_the_four_verdicts():
    got = accounting.parse(["d1 met: the page reads back", "D2 not due: nothing new since r:4",
                            "g1 distance: two left", "garbage", "g2 unmet: x"])
    assert got == {"d1": ("met", "the page reads back"),
                   "d2": ("not due", "nothing new since r:4"),
                   "g1": ("distance", "two left"), "g2": ("unmet", "x")}
    assert accounting.parse("d1 met: x") == {}       # a string is not the field


def test_a_complete_accounting_has_no_problems():
    verdicts = accounting.parse(["d1 met: yes", "d2 unmet: waits on Kai",
                                 "g1 met: portal receipt", "g2 distance: the review"])
    assert accounting.problems(verdicts, DONE, OUTCOMES) == \
        {"missing": [], "bare": [], "refused": []}


def test_missing_and_bare_entries_are_named():
    verdicts = accounting.parse(["d1 met:", "g1 distance: soon"])
    found = accounting.problems(verdicts, DONE, OUTCOMES)
    assert found["missing"] == ["d2", "g2"] and found["bare"] == ["d1"]


def test_a_run_may_not_claim_an_outcome_the_operator_judges():
    verdicts = accounting.parse(["d1 met: a", "d2 met: b", "g1 unmet: x", "g2 met: done"])
    refused = accounting.problems(verdicts, DONE, OUTCOMES)["refused"]
    assert ("g1: an outcome of the finish line takes `distance` or, when the run proves it, "
            "`met`") in refused
    assert "g2: only the operator judges this outcome — report its distance" in refused


def test_a_done_when_line_takes_no_distance():
    verdicts = accounting.parse(["d1 distance: far", "d2 met: b"])
    assert accounting.problems(verdicts, DONE, [])["refused"] == [
        "d1: a Done-when line or a brief is met, unmet or not due"]


def test_the_deferral_says_what_is_missing_and_the_shape():
    msg = accounting.deferral({"missing": ["d2"], "bare": ["d1"], "refused": []})
    assert msg.startswith("OBSERVATION (finish deferred): your `accounting` is incomplete")
    assert "no entry for d2" in msg and "nothing after the verdict for d1" in msg
    assert "`g<n> distance: <what remains>`" in msg


def test_unmet_residuals_carry_the_runs_own_words():
    assert accounting.unmet_residuals(["d1 met: ok", "d2 unmet: Kai has not answered"]) == \
        ["d2 — Kai has not answered"]


# ---- the digest block --------------------------------------------------------------------------

def test_the_digest_says_nothing_for_a_routine_that_owes_nothing(tmp_path):
    (tmp_path / "main.md").write_text("# R\n\nDo it.\n", encoding="utf-8")
    assert finish_digest.digest_section(tmp_path) == ""


def test_the_digest_renders_the_finish_line_and_the_contract(tmp_path):
    (tmp_path / "main.md").write_text(RECIPE, encoding="utf-8")
    _line(tmp_path, [{"text": "submitted", "judge": "run"},
                     {"text": "approved", "judge": "you", "status": "met"},
                     {"text": "closes", "judge": "date", "date": "2026-10-15"}],
          until="2026-12-31")
    finishline.record(tmp_path, {"g1": ("distance", "the budget table")}, run_id="r:1",
                      now="t")
    text = finish_digest.digest_section(tmp_path)
    assert "FINISH LINE (the operator's" in text
    assert "○ [g1] submitted — the run proves it · last distance: the budget table" in text
    assert "✓ [g2] approved — the operator decides" in text
    assert "○ [g3] closes — on its date 2026-10-15" in text
    assert "The routine stops after 2026-12-31 whatever else is reached." in text
    assert "one entry per line of your recipe's `## Done when`" in text
    assert "one per open finish-line outcome" in text


def test_the_digest_carries_what_the_last_run_left_unmet(tmp_path):
    (tmp_path / "main.md").write_text(RECIPE, encoding="utf-8")
    for ts, entries in (("2026-09-27T10-00-00", ["d1 unmet: old news"]),
                        ("2026-09-28T10-00-00", ["d1 met: x", "d2 unmet: the host was down"])):
        run = tmp_path / "runs" / ts
        run.mkdir(parents=True)
        (run / "status.json").write_text(json.dumps({"accounting": entries}), encoding="utf-8")
    text = finish_digest.digest_section(tmp_path)
    assert "THE LAST RUN LEFT UNMET" in text
    assert "d2 — the host was down" in text and "old news" not in text


# ---- the endpoint ------------------------------------------------------------------------------

def test_the_endpoint_round_trips_the_document(api_client, make_routine):
    c, _tmp = api_client
    d = make_routine(slug="fl1")
    (d / "main.md").write_text(RECIPE, encoding="utf-8")
    got = c.get("/api/routines/fl1/finish-line").json()
    assert got["outcomes"] == [] and got["until"] == "" and got["reached"] == ""
    assert [x["id"] for x in got["done_when"]] == ["d1", "d2", "d3"]
    r = c.put("/api/routines/fl1/finish-line", json={
        "outcomes": [{"text": "submitted", "judge": "run"}], "until": "2027-01-31"})
    assert r.status_code == 200
    body = r.json()
    assert body["outcomes"][0]["id"] == "g1" and body["until"] == "2027-01-31"
    assert body["live_run"] is False


def test_the_endpoint_shows_the_verdicts_the_last_runs_gave(api_client, make_routine):
    c, _tmp = api_client
    d = make_routine(slug="fl2")
    run = d / "runs" / "2026-09-28T10-00-00"
    run.mkdir(parents=True)
    (run / "status.json").write_text(json.dumps({"accounting": ["d1 met: read back"]}),
                                     encoding="utf-8")
    got = c.get("/api/routines/fl2/finish-line").json()
    assert got["verdicts"] == [{"run": "2026-09-28T10-00-00",
                                "accounting": {"d1": ["met", "read back"]}}]


@pytest.mark.parametrize(("body", "status"), [
    ({"until": "next week"}, 400),
    ({"outcomes": [{"text": "x", "judge": "robot"}]}, 400),
    ({"outcomes": [{"text": "x", "judge": "date"}]}, 400),
    ({"outcomes": [{"text": "x", "distance": "forged"}]}, 422),     # engine-owned, not accepted
    ({"goal": "x"}, 422),
])
def test_bad_bodies_are_refused(api_client, make_routine, body, status):
    c, _tmp = api_client
    make_routine(slug="fl3")
    assert c.put("/api/routines/fl3/finish-line", json=body).status_code == status


def test_a_save_that_completes_the_line_queues_the_retirement_card(api_client, make_routine):
    from rsched import pending

    c, tmp = api_client
    make_routine(slug="fl4")
    c.put("/api/routines/fl4/finish-line", json={
        "outcomes": [{"text": "approved", "judge": "you", "status": "met"}]})
    queued = pending.load_all(tmp / "routines")
    assert [(q["kind"], q["routine"]) for q in queued] == [("goal-reached", "fl4")]


# ---- the job brief ------------------------------------------------------------------------------

def test_a_brief_stands_in_for_the_done_when_list(tmp_path):
    from rsched.engine import brief

    (tmp_path / "main.md").write_text(RECIPE, encoding="utf-8")
    text = finish_digest.digest_section(tmp_path, brief="re-check the Q3 figures only")
    assert "THIS RUN'S BRIEF" in text and "re-check the Q3 figures only" in text
    assert "`b1 met: <evidence>` or `b1 unmet: <what remains>`" in text
    assert "one entry per line of your recipe" not in text   # the recipe's list is not owed
    found = accounting.problems(accounting.parse(["b1 distance: far"]),
                                brief.owed("re-check"), [])
    assert found["refused"] == ["b1: a Done-when line or a brief is met, unmet or not due"]
    assert accounting.problems(accounting.parse(["b1 met: the figures match"]),
                               brief.owed("re-check"), []) == {
        "missing": [], "bare": [], "refused": []}


def test_a_brief_round_trips_through_the_run_dir(tmp_path):
    from rsched.engine import brief

    brief.write(tmp_path, "  re-check\n the   figures  ")
    assert brief.read(tmp_path) == "re-check the figures"
    assert brief.read(tmp_path / "nowhere") == ""


def test_run_now_hands_its_brief_to_the_runner(api_client, make_routine):
    c, _tmp = api_client
    make_routine(slug="briefed")
    runner = c.app.state.runner
    fired = []

    async def fire(cfg, *, reason="schedule", brief=""):
        fired.append((cfg.slug, reason, brief))
        return f"{cfg.slug}:1"

    runner.fire = fire
    assert c.post("/api/routines/briefed/run", json={"brief": "only the Q3 figures"}) \
        .status_code == 200
    assert c.post("/api/routines/briefed/run").status_code == 200       # no body: no brief
    assert fired == [("briefed", "manual", "only the Q3 figures"), ("briefed", "manual", "")]
    assert c.post("/api/routines/briefed/run", json={"brief": "x" * 301}).status_code == 422

