"""The fair-share job queue for an exclusive machine (a GPU box).

The operator's ask was specific — "i would prefer they found a way to schedule it so everyone
gets their turn" — and it rules out the two obvious answers. A mutex REFUSES, so on a daily cron
two of three routines get "no" every day. An flock blocks in arbitrary order, so a routine that
submits three jobs starves one that submits one. What these pin is the third thing: an order, a
position every run can read, and a failure mode that never reads as "free".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rsched import machine_queue as mq


def _t(holder: str, job: str, submitted: str, **extra) -> dict:
    return {"holder": holder, "job": job, "submitted": submitted, **extra}


# ---- the order ---------------------------------------------------------------------------------

def test_one_routines_three_jobs_do_not_starve_anothers_one():
    """THE property. FIFO would run f1 f2 f3 v1 and make voice wait behind three jobs it had
    nothing to do with; round-robin across HOLDERS puts it second."""
    order = mq.fair_share_order([
        _t("funscript", "f1", "1"), _t("funscript", "f2", "2"),
        _t("funscript", "f3", "3"), _t("voice", "v1", "4")])
    assert [t["job"] for t in order] == ["f1", "v1", "f2", "f3"]


def test_holders_enter_the_rotation_in_arrival_order():
    """A newcomer does not jump ahead of someone already waiting — it joins the end of the
    rotation, not the front."""
    order = mq.fair_share_order([
        _t("a", "a1", "1"), _t("b", "b1", "2"), _t("c", "c1", "3"), _t("a", "a2", "4")])
    assert [t["job"] for t in order] == ["a1", "b1", "c1", "a2"]


def test_one_holder_is_plain_fifo():
    order = mq.fair_share_order([_t("a", "a2", "2"), _t("a", "a1", "1"), _t("a", "a3", "3")])
    assert [t["job"] for t in order] == ["a1", "a2", "a3"]


def test_position_reads_the_order_it_is_given_and_never_re_sorts():
    """The mirror holds what the BOX returned, already in the box's own order. Re-deriving it
    here would answer for a round the reader cannot see — see the regression below."""
    ordered = [_t("f", "f1", "1"), _t("v", "v1", "3"), _t("f", "f2", "2")]
    assert mq.position_of(ordered, "f1") == 1
    assert mq.position_of(ordered, "v1") == 2
    assert mq.position_of(ordered, "f2") == 3
    assert mq.position_of(ordered, "ghost") is None


def test_a_partly_served_round_needs_the_spent_turns_too():
    """The defect the util's end-to-end harness caught, pinned here because the DEFINITION lives
    in this module and the box ships this very function.

    Deleting the ticket that just ran also deletes the evidence that its holder used a turn, so
    ordering the remaining live tickets alone silently collapses to FIFO — f1 f2 f3 v1 instead of
    f1 v1 f2 f3. Given the whole round (spent + live) it is right again, which is why the box
    retires a finished ticket into `round/` rather than deleting it.
    """
    spent = [_t("f", "f1", "1")]
    live = [_t("f", "f2", "2"), _t("f", "f3", "3"), _t("v", "v1", "4")]

    # WRONG: the live set alone hands funscript the head again
    assert next(t["job"] for t in mq.fair_share_order(live)) == "f2"
    # RIGHT: the whole round remembers f already took a turn
    whole = [t for t in mq.fair_share_order(spent + live) if t not in spent]
    assert [t["job"] for t in whole] == ["v1", "f2", "f3"]


def test_an_empty_queue_has_no_order_and_no_positions():
    assert mq.fair_share_order([]) == []
    assert mq.position_of([], "x") is None


# ---- the mirror --------------------------------------------------------------------------------

def test_the_mirror_round_trips(tmp_path):
    mq.save(tmp_path, "predator", [_t("funscript", "f1", "1", state="running")])
    doc = mq.load(tmp_path, "predator")
    assert doc["machine"] == "predator" and doc["stale"] is False
    assert [t["job"] for t in doc["tickets"]] == ["f1"]


def test_a_machine_never_read_is_stale_not_empty(tmp_path):
    """An absent mirror must not read as a free machine — that is the exact mistake that would
    cause the collision this exists to prevent."""
    doc = mq.load(tmp_path, "never-seen")
    assert doc["stale"] is True and doc["tickets"] == []


def test_an_old_mirror_goes_stale(tmp_path):
    mq.save(tmp_path, "predator", [])
    path = mq.mirror_path(tmp_path, "predator")
    import json
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["fetched"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert mq.load(tmp_path, "predator")["stale"] is True


# ---- what the run is told ------------------------------------------------------------------------

def test_a_free_machine_says_so(tmp_path):
    mq.save(tmp_path, "predator", [])
    assert "COMPUTE FREE" in mq.capability_note(tmp_path, "predator", "funscript")


def test_a_queued_machine_names_the_run_own_position(tmp_path):
    # saved in the box's order, which is what the mirror always holds
    mq.save(tmp_path, "predator", [
        _t("voice", "v1", "1", state="running"),
        _t("funscript", "f1", "2"), _t("funscript", "f2", "3")])
    note = mq.capability_note(tmp_path, "predator", "funscript")
    assert "COMPUTE QUEUED" in note
    assert "3 job(s) queued" in note
    assert "voice is running now" in note
    assert "yours: #2, #3" in note
    # the load-bearing half: a queued job costs the run nothing, so do other work
    assert "does NOT block this run" in note


def test_an_unreachable_machine_reads_as_unknown_never_as_free(tmp_path):
    mq.save(tmp_path, "predator", [], error="ssh: connect: no route to host")
    note = mq.capability_note(tmp_path, "predator", "funscript")
    assert "COMPUTE QUEUE UNKNOWN" in note and "no route to host" in note
    assert "FREE" not in note


def test_a_stale_mirror_also_reads_as_unknown(tmp_path):
    note = mq.capability_note(tmp_path, "never-seen", "funscript")
    assert "COMPUTE QUEUE UNKNOWN" in note and "FREE" not in note


# ---- the refresh -------------------------------------------------------------------------------

def test_refresh_is_a_noop_without_an_exclusive_machine(tmp_path):
    from types import SimpleNamespace

    server = SimpleNamespace(routines_home=tmp_path,
                             machines={"box": SimpleNamespace(exclusive=False)})
    assert mq.refresh(server) == {}


def test_a_util_that_cannot_answer_records_why(tmp_path, monkeypatch):
    """Including the case that matters during rollout: a `remote` util too old to know the verb.
    It must record a reason, not an empty queue."""
    from types import SimpleNamespace

    from rsched import machine_queue

    mac = SimpleNamespace(exclusive=True, key_var="", name="predator", host="h", user="u",
                          port=22, host_key="", workdir="", share="", description="",
                          tags=[])
    server = SimpleNamespace(routines_home=tmp_path, libraries_home=tmp_path / "lib",
                             machines={"predator": mac})
    monkeypatch.setattr(machine_queue, "machine_public_of", lambda m, n: {"name": n})
    monkeypatch.setattr("rsched.sandbox.base_policy", lambda s: None)
    monkeypatch.setattr("rsched.utils_run.run_util",
                        lambda *a, **k: (2, "", "unknown command 'queue'"))
    out = mq.refresh(server)
    assert out["predator"]["tickets"] == []
    assert "unknown command" in out["predator"]["error"]
    assert "UNKNOWN" in mq.capability_note(tmp_path, "predator", "funscript")
