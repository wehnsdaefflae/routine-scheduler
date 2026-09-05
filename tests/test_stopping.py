"""Semantic stopping conditions (F334/D98): the user's meaning-level bounds on a job.

The order these serve (2026-08-14): a run should stop on a MEANING-level condition, **not only
on budget walls**. So the parts that matter are (a) the engine never judges semantics but forces
the model's accounting, (b) conditions are LOGICALLY CONNECTED — groups combine with all/any,
`requires` sequences them, `stage` scopes them to a routine phase — and (c) the model's verdict
is RECORDED, which is what lets every reader agree instead of staring at a store frozen at
`open`.

And (d) the SCOPE split, which is what makes (c) survivable: a `run` bound is re-asked every run
and never transitions, a `goal` condition is sticky and retires the routine when it is met. Sticky
per-run conditions are what left 22 of 31 live routines reading "the job is DONE. Finish NOW" at
the top of every run. `evaluate()` therefore answers about GOAL conditions only — the one durable
question this document can answer.
"""

from __future__ import annotations

from rsched.engine import stopping, stopping_digest

NOW = "2026-08-27T00:00:00+02:00"


def _doc(conditions, *, groups=None, mode="all"):
    return {"mode": mode, "groups": groups or [], "conditions": conditions}


def _goal(conditions, *, groups=None, mode="all"):
    """The same document with every condition scoped to the routine's FINAL GOAL — what the
    boolean structure below is actually about, since `evaluate` answers about goals only."""
    return _doc([{**c, "scope": "goal"} for c in conditions], groups=groups, mode=mode)


# ---- store ------------------------------------------------------------------------------------

def test_save_normalizes_and_assigns_stable_ids(tmp_path):
    out = stopping.save(tmp_path, _doc([
        {"text": "stop once the PDF is published and verified"},
        {"id": "s7", "text": "only diagnose — do not start fixing", "status": "met"},
        {"text": "   "},                          # blank → dropped
        {"id": "junk!", "text": "bad id gets a fresh one", "status": "nonsense"},
    ]), now=NOW)
    rows = out["conditions"]
    assert [r["id"] for r in rows] == ["s1", "s7", "s2"]   # well-formed id kept, gaps filled
    assert rows[0]["status"] == "open" and rows[0]["ts"] == NOW
    assert rows[1]["status"] == "met"
    assert rows[2]["status"] == "open"                     # unknown status falls back
    assert stopping.load(tmp_path) == out                  # round-trips
    # every condition lands in a group, so evaluation has exactly one shape
    assert {r["group"] for r in rows} == {stopping.DEFAULT_GROUP}


def test_load_survives_missing_and_corrupt_store(tmp_path):
    empty = {"mode": "all", "groups": [], "conditions": []}
    assert stopping.load(tmp_path) == empty
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "stopping.json").write_text("not json", encoding="utf-8")
    assert stopping.load(tmp_path) == empty


def test_a_dangling_requires_is_dropped_not_left_blocking(tmp_path):
    """A gate pointing at nothing would read as 'blocked' forever with no way to see why."""
    out = stopping.save(tmp_path, _doc([
        {"id": "s1", "text": "a", "requires": ["s9"]},
        {"id": "s2", "text": "b", "requires": ["s2"]},     # self-reference
    ]), now=NOW)
    assert out["conditions"][0]["requires"] == []
    assert out["conditions"][1]["requires"] == []


# ---- logical structure -------------------------------------------------------------------------

def test_any_group_is_satisfied_by_one_member():
    doc = stopping.normalize(_goal(
        [{"id": "s1", "text": "user says stop", "status": "met", "group": "g1"},
         {"id": "s2", "text": "deadline passes", "group": "g1"}],
        groups=[{"id": "g1", "name": "escape hatch", "mode": "any"}]))
    v = stopping.evaluate(doc)
    assert v["goal_satisfied"] is True
    assert v["groups"][0] == {"id": "g1", "name": "escape hatch", "mode": "any",
                              "satisfied": True, "met": 1, "total": 2}


def test_all_group_needs_every_member():
    doc = stopping.normalize(_goal(
        [{"id": "s1", "text": "verified", "status": "met", "group": "g1"},
         {"id": "s2", "text": "published", "group": "g1"}],
        groups=[{"id": "g1", "name": "publish", "mode": "all"}]))
    assert stopping.evaluate(doc)["goal_satisfied"] is False


def test_two_levels_express_and_of_ors_and_or_of_ands():
    """(A AND B) OR (C) — the shape a flat list cannot say, and the reason a run treated an
    either/or as a checklist and worked past where the user meant it to stop."""
    conds = [{"id": "s1", "text": "verified", "status": "met", "group": "g1"},
             {"id": "s2", "text": "published", "group": "g1"},
             {"id": "s3", "text": "user says stop", "status": "met", "group": "g2"}]
    groups = [{"id": "g1", "name": "publish", "mode": "all"},
              {"id": "g2", "name": "hatch", "mode": "any"}]
    # root ANY: the satisfied escape hatch ends the job even though publish is incomplete
    assert stopping.evaluate(stopping.normalize(
        _goal(conds, groups=groups, mode="any")))["goal_satisfied"] is True
    # root ALL: it does not
    assert stopping.evaluate(stopping.normalize(
        _goal(conds, groups=groups, mode="all")))["goal_satisfied"] is False


def test_dropped_conditions_are_excluded_from_every_verdict():
    doc = stopping.normalize(_goal(
        [{"id": "s1", "text": "verified", "status": "met", "group": "g1"},
         {"id": "s2", "text": "abandoned", "status": "dropped", "group": "g1"}],
        groups=[{"id": "g1", "mode": "all"}]))
    v = stopping.evaluate(doc)
    assert v["goal_satisfied"] is True and v["groups"][0]["total"] == 1


def test_an_empty_group_expresses_no_requirement():
    """Vacuously satisfied under EITHER mode — the strict reading of an empty `any` would let
    an emptied group block the job forever."""
    for mode in ("all", "any"):
        doc = stopping.normalize(_doc([], groups=[{"id": "g1", "mode": mode}]))
        assert stopping.evaluate(doc)["groups"][0]["satisfied"] is True


def test_no_goal_conditions_means_no_opinion_not_satisfied():
    """None, not True — so nothing announces a finish line the user never drew, and nothing
    retires a routine that was always meant to run forever."""
    assert stopping.evaluate(stopping.normalize(_doc([])))["goal_satisfied"] is None
    # a document of RUN bounds only is equally silent: they have no durable verdict at all
    assert stopping.evaluate(stopping.normalize(
        _doc([{"id": "s1", "text": "one increment landed", "status": "met"}]),
    ))["goal_satisfied"] is None


# ---- sequencing + stage scoping ------------------------------------------------------------------

def test_requires_keeps_a_condition_dormant_until_its_gate_is_met():
    doc = stopping.normalize(_doc([
        {"id": "s1", "text": "draft written", "group": "g1"},
        {"id": "s2", "text": "draft reviewed", "requires": ["s1"], "group": "g1"}]))
    by_id = {c["id"]: c for c in doc["conditions"]}
    assert [c["id"] for c in stopping.active(doc)] == ["s1"]
    assert stopping.blocked_reason(by_id["s2"], by_id) == "waiting on s1"

    doc["conditions"][0]["status"] = "met"
    by_id = {c["id"]: c for c in doc["conditions"]}
    assert [c["id"] for c in stopping.active(doc)] == ["s2"]
    assert stopping.blocked_reason(by_id["s2"], by_id) == ""


def test_a_dropped_requirement_does_not_block_forever():
    """A gate nobody is watching must open — otherwise dropping one condition silently kills
    every condition downstream of it."""
    doc = stopping.normalize(_doc([
        {"id": "s1", "text": "gate", "status": "dropped", "group": "g1"},
        {"id": "s2", "text": "gated", "requires": ["s1"], "group": "g1"}]))
    assert [c["id"] for c in stopping.active(doc)] == ["s2"]


def test_stage_scopes_a_routine_condition_to_one_phase():
    """The 'per-stage routine conditions LATER' half of the original order."""
    doc = stopping.normalize(_doc([
        {"id": "s1", "text": "always", "group": "g1"},
        {"id": "s2", "text": "only while drafting", "stage": "draft", "group": "g1"}]))
    assert [c["id"] for c in stopping.active(doc, phase="draft")] == ["s1", "s2"]
    assert [c["id"] for c in stopping.active(doc, phase="review")] == ["s1"]
    by_id = {c["id"]: c for c in doc["conditions"]}
    assert stopping.blocked_reason(by_id["s2"], by_id, phase="review") == "only in stage draft"


# ---- the prompt ---------------------------------------------------------------------------------

def test_digest_renders_the_structure_not_a_flat_list(tmp_path):
    assert stopping_digest.digest_section(tmp_path) == ""   # no store → no section
    stopping.save(tmp_path, _doc(
        [{"id": "s1", "text": "verify the upload", "group": "g1"},
         {"id": "s2", "text": "publish it", "requires": ["s1"], "group": "g1"},
         {"id": "s3", "text": "the user says stop", "group": "g2"},
         {"id": "s4", "text": "old", "status": "dropped", "group": "g1"}],
        groups=[{"id": "g1", "name": "publish", "mode": "all"},
                {"id": "g2", "name": "hatch", "mode": "any"}], mode="any"), now=NOW)
    sec = stopping_digest.digest_section(tmp_path)
    # the run must be able to SEE that the two groups are an OR, or it treats them as an AND
    assert "ANY of these groups" in sec
    assert '"publish" — ALL of:' in sec and '"hatch" — ANY of:' in sec
    assert "[s1] verify the upload" in sec
    assert "[s2] publish it  (waiting on s1)" in sec       # dormant, and it says why
    assert "old" not in sec                                # dropped is gone entirely
    # accounting is demanded for ACTIVE conditions only — never the dormant one
    assert "account for each ACTIVE condition (s1, s3)" in sec
    assert "met LIMIT condition" in sec and "finish NOW" in sec


def test_digest_says_so_when_the_final_goal_is_satisfied(tmp_path):
    stopping.save(tmp_path, _goal([{"id": "s1", "text": "verified", "status": "met"}]),
                  now=NOW)
    sec = stopping_digest.digest_section(tmp_path)
    assert "EVERY final-goal condition is met" in sec
    assert "this ROUTINE is finished" in sec


def test_digest_never_tells_a_run_bound_routine_that_the_job_is_over(tmp_path):
    """THE regression this scope split exists to prevent. A per-run bound reported met is
    history, not a finish line: 22 of 31 live routines opened every run with "the job is DONE.
    Finish NOW" because a run bound went sticky."""
    stopping.save(tmp_path, _doc([{"id": "s1", "text": "one increment landed"}]), now=NOW)
    stopping.record_accounting(tmp_path, "[s1] met — shipped it", run_id="r:1", now=NOW)
    sec = stopping_digest.digest_section(tmp_path)
    assert "this ROUTINE is finished" not in sec
    assert "bounds on THIS RUN" in sec
    assert "last run: met — shipped it" in sec     # the verdict is kept, as history
    assert stopping.unaccounted("", tmp_path) == ["s1"]   # and it is demanded again


def test_digest_renders_the_two_scopes_apart(tmp_path):
    stopping.save(tmp_path, _doc([
        {"id": "s1", "text": "this run's increment is verified"},
        {"id": "s2", "text": "the application is submitted", "scope": "goal"}]), now=NOW)
    sec = stopping_digest.digest_section(tmp_path)
    assert "STOPPING CONDITIONS" in sec and "FINAL GOAL" in sec
    assert sec.index("STOPPING CONDITIONS") < sec.index("FINAL GOAL")
    assert "[s1] this run's increment is verified" in sec
    assert "[s2] the application is submitted" in sec
    # both are accounted for: a goal's `unmet` note is where the run states the distance left
    assert "account for each ACTIVE condition (s1, s2)" in sec
    assert "the `unmet` note is where you state the distance" in sec
    assert "The final goal is NOT yet met" in sec


# ---- the accounting contract ----------------------------------------------------------------------

def test_unaccounted_demands_active_conditions_only(tmp_path):
    stopping.save(tmp_path, _doc([
        {"id": "s1", "text": "a"},
        {"id": "s2", "text": "b"},
        {"id": "s3", "text": "c", "status": "met"},
        {"id": "s4", "text": "d", "requires": ["s1"]}]), now=NOW)
    assert stopping.unaccounted("no accounting at all", tmp_path) == ["s1", "s2"]
    assert stopping.unaccounted("[s1] met — done; [s2] unmet — blocked", tmp_path) == []
    assert stopping.unaccounted("[s1] met only", tmp_path) == ["s2"]
    assert stopping.unaccounted("", tmp_path) == ["s1", "s2"]


def test_read_accounting_parses_the_contract_line():
    got = stopping.read_accounting(
        "wrapped up.\n[s1] met — PDF verified byte-identical\n[S2] unmet: still blocked\n"
        "[s3] met\nnot a line about [s4]")
    assert got == {"s1": ("met", "PDF verified byte-identical"),
                   "s2": ("unmet", "still blocked"),
                   "s3": ("met", "")}
    assert "s4" not in got


def test_record_accounting_stamps_the_verdict_back(tmp_path):
    """THE gap that made the status column dead: without this a condition stayed `open`
    however often a run reported it met, so every reader saw a stale list."""
    stopping.save(tmp_path, _doc([{"id": "s1", "text": "a"}, {"id": "s2", "text": "b"}]),
                  now=NOW)
    changed = stopping.record_accounting(
        tmp_path, "[s1] met — verified; [s2] unmet — the source was down",
        run_id="r:1", now=NOW)
    assert changed == []                       # nothing GOAL-shaped was met, so nothing retires
    rows = {c["id"]: c for c in stopping.load(tmp_path)["conditions"]}
    # a RUN bound records its verdict and stays open — it is re-asked next run
    assert rows["s1"]["status"] == "open" and rows["s1"]["last_verdict"] == "met"
    assert rows["s1"]["note"] == "verified" and rows["s1"]["resolved_run"] == "r:1"
    # unmet records the REASON and stays open — that is what the next run needs to read
    assert rows["s2"]["status"] == "open" and rows["s2"]["note"] == "the source was down"
    assert rows["s2"]["last_verdict"] == "unmet"


def test_a_goal_condition_transitions_and_a_run_bound_never_does(tmp_path):
    stopping.save(tmp_path, _doc([
        {"id": "s1", "text": "increment verified"},
        {"id": "s2", "text": "the application is submitted", "scope": "goal"}]), now=NOW)
    changed = stopping.record_accounting(
        tmp_path, "[s1] met — verified; [s2] met — submitted 2026-09-05",
        run_id="r:1", now=NOW)
    assert changed == ["s2"]                   # ONLY the goal — this is what retires a routine
    rows = {c["id"]: c for c in stopping.load(tmp_path)["conditions"]}
    assert rows["s1"]["status"] == "open" and rows["s2"]["status"] == "met"
    assert stopping.goal_reached(tmp_path) is True


def test_a_met_goal_is_sticky_across_runs(tmp_path):
    """A later run does not silently reopen a goal the user has been told is done."""
    stopping.save(tmp_path, _goal([{"id": "s1", "text": "a"}]), now=NOW)
    stopping.record_accounting(tmp_path, "[s1] met — done", run_id="r:1", now=NOW)
    stopping.record_accounting(tmp_path, "[s1] unmet — I changed my mind", run_id="r:2", now=NOW)
    assert stopping.load(tmp_path)["conditions"][0]["status"] == "met"


def test_reopening_a_goal_puts_the_routine_back_and_keeps_the_evidence(tmp_path):
    """Declining a retirement means "not yet". It has to change the DOCUMENT, because that is
    what retirement is derived from — dropping the proposal alone would leave the routine
    unscheduled with nothing left to act on."""
    stopping.save(tmp_path, _goal([{"id": "s1", "text": "submitted"}]), now=NOW)
    stopping.record_accounting(tmp_path, "[s1] met — sent it", run_id="r:1", now=NOW)
    assert stopping.goal_reached(tmp_path) is True

    assert stopping.reopen_goal(tmp_path, now=NOW) == ["s1"]
    assert stopping.goal_reached(tmp_path) is False
    row = stopping.load(tmp_path)["conditions"][0]
    assert row["status"] == "open"
    assert row["note"] == "sent it" and row["last_verdict"] == "met"   # evidence survives
    assert stopping.reopen_goal(tmp_path, now=NOW) == []               # idempotent


def test_record_accounting_is_a_noop_without_verdicts(tmp_path):
    stopping.save(tmp_path, _doc([{"id": "s1", "text": "a"}]), now=NOW)
    assert stopping.record_accounting(tmp_path, "just a summary", run_id="r:1", now=NOW) == []
    assert stopping.load(tmp_path)["conditions"][0]["status"] == "open"


# ---- the API: one implementation, both homes ------------------------------------------------------

def test_routine_endpoints_round_trip_the_whole_document(api_client, make_routine):
    """Routines are the 'per-stage routine conditions LATER' half of the 2026-08-14 order —
    they had no endpoint at all until now."""
    c, _tmp = api_client
    make_routine(slug="goalr")
    assert c.get("/api/routines/goalr/stopping").json() == {
        "mode": "all", "groups": [], "conditions": [], "verdict": {"goal_satisfied": None,
                                                                   "groups": []}}
    r = c.put("/api/routines/goalr/stopping", json={
        "mode": "any",
        "groups": [{"id": "g1", "name": "publish", "mode": "all"},
                   {"id": "g2", "name": "hatch", "mode": "any"}],
        "conditions": [
            {"id": "s1", "text": "verified", "group": "g1"},
            {"id": "s2", "text": "published", "group": "g1", "requires": ["s1"]},
            {"id": "s3", "text": "user says stop", "group": "g2", "stage": "review"}]})
    assert r.status_code == 200
    doc = r.json()
    assert doc["mode"] == "any"
    rows = {x["id"]: x for x in doc["conditions"]}
    assert rows["s2"]["requires"] == ["s1"] and rows["s3"]["stage"] == "review"
    # the verdict rides the read, so the panel never re-derives the boolean structure itself
    # goal-scoped counts: these three are RUN bounds, so the goal verdict has no opinion
    assert doc["verdict"]["groups"][0] == {"id": "g1", "name": "publish", "mode": "all",
                                           "satisfied": True, "met": 0, "total": 0}
    assert doc["verdict"]["goal_satisfied"] is None
    assert rows["s1"]["scope"] == "run"        # the default, so an untouched panel is unchanged
    # `blocked` travels with the row so the panel can grey it and SAY why
    assert rows["s2"]["blocked"] == "waiting on s1"
    assert c.get("/api/routines/goalr/stopping").json()["conditions"] == doc["conditions"]


def test_a_met_goal_condition_flips_the_verdict_through_the_api(api_client, make_routine):
    c, _tmp = api_client
    make_routine(slug="goalr2")
    c.put("/api/routines/goalr2/stopping", json={"conditions": [
        {"id": "s1", "text": "a", "status": "met", "scope": "goal"},
        {"id": "s2", "text": "b", "scope": "goal"}]})
    assert c.get("/api/routines/goalr2/stopping").json()["verdict"]["goal_satisfied"] is False
    c.put("/api/routines/goalr2/stopping", json={"conditions": [
        {"id": "s1", "text": "a", "status": "met", "scope": "goal"},
        {"id": "s2", "text": "b", "status": "met", "scope": "goal"}]})
    assert c.get("/api/routines/goalr2/stopping").json()["verdict"]["goal_satisfied"] is True


def test_an_unknown_scope_is_a_legible_400(api_client, make_routine):
    c, _tmp = api_client
    make_routine(slug="goalr3")
    r = c.put("/api/routines/goalr3/stopping",
              json={"conditions": [{"text": "a", "scope": "project"}]})
    assert r.status_code == 400 and "scope must be one of" in r.json()["detail"]


def test_bad_bodies_are_legible_400s_and_a_stray_key_is_a_422(api_client, make_routine):
    c, _tmp = api_client
    make_routine(slug="goalr3")
    bad = [{"conditions": [{"text": "a", "status": "nonsense"}]},
           {"mode": "sometimes", "conditions": []},
           {"groups": [{"id": "g1", "mode": "maybe"}], "conditions": []}]
    for body in bad:
        assert c.put("/api/routines/goalr3/stopping", json=body).status_code == 400
    # a misspelled key silently dropped would read as "saved"
    assert c.put("/api/routines/goalr3/stopping",
                 json={"conditions": [{"text": "a", "stauts": "open"}]}).status_code == 422
    # ...and nothing was written by any of them
    assert c.get("/api/routines/goalr3/stopping").json()["conditions"] == []


def test_a_goal_nobody_can_read_is_refused(api_client, make_routine):
    c, _tmp = api_client
    make_routine(slug="goalr4")
    r = c.put("/api/routines/goalr4/stopping",
              json={"conditions": [{"text": f"c{i}"} for i in range(61)]})
    assert r.status_code == 400 and "60 max" in r.json()["detail"]


def test_a_user_save_never_erases_the_runs_own_conclusion(tmp_path):
    """The ownership line: the USER owns the text, structure and status; the ENGINE owns the
    resolution stamps. Before this, editing a condition's text wiped the note and the run id
    that recorded WHY it was met — the whole audit value of the writer."""
    stopping.save(tmp_path, _doc([{"id": "s1", "text": "verify it"}]), now=NOW)
    stopping.record_accounting(tmp_path, "[s1] met — byte-identical", run_id="r:9", now=NOW)

    # the user renames the condition and regroups it, as the panel's whole-document PUT does
    stopping.save(tmp_path, _doc([{"id": "s1", "text": "verify the upload", "status": "met"}]),
                  now=NOW)
    row = stopping.load(tmp_path)["conditions"][0]
    assert row["text"] == "verify the upload"          # the user's edit landed
    assert row["note"] == "byte-identical"             # the run's conclusion survived
    assert row["resolved_run"] == "r:9"


def test_the_api_cannot_fabricate_a_resolution(api_client, make_routine):
    """A client that could set resolved_run could claim a run concluded something it never did."""
    c, _tmp = api_client
    make_routine(slug="goalr5")
    r = c.put("/api/routines/goalr5/stopping", json={"conditions": [
        {"id": "s1", "text": "a", "status": "met", "resolved_run": "made-up:1"}]})
    assert r.status_code == 422        # the field is not on the input model at all
    c.put("/api/routines/goalr5/stopping",
          json={"conditions": [{"id": "s1", "text": "a", "status": "met"}]})
    assert c.get("/api/routines/goalr5/stopping").json()["conditions"][0]["resolved_run"] == ""
