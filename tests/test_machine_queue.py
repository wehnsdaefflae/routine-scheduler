"""The fair-share job queue for an exclusive machine (a GPU box).

The operator's ask was specific — "i would prefer they found a way to schedule it so everyone
gets their turn" — and it rules out the two obvious answers. A mutex REFUSES, so on a daily cron
two of three routines get "no" every day. An flock blocks in arbitrary order, so a routine that
submits three jobs starves one that submits one. The third thing is an ORDER, and it is the
`remote` util's: it ships its `fair_share_order` to the box by source, and its selftest pins that
order — the spent-turn regression included — on the very helper the box runs. What these pin
is the scheduler's half: a position every run can read in the box's own order, and a failure
mode that never reads as "free".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rsched import machine_queue as mq


def _t(holder: str, job: str, submitted: str, **extra) -> dict:
    return {"holder": holder, "job": job, "submitted": submitted, **extra}


# ---- the position --------------------------------------------------------------------------------

def test_position_reads_the_order_it_is_given_and_never_re_sorts():
    """The mirror holds what the BOX returned, already in the box's own order — computed over
    the whole round, the turns already spent included. Here f1 has run and been retired, so the
    box puts v1 next even though f2 and f3 were submitted earlier; nothing in these live
    tickets shows why, so any order derived from them alone would put f2 first."""
    ordered = [_t("v", "v1", "4"), _t("f", "f2", "2"), _t("f", "f3", "3")]
    assert mq.position_of(ordered, "v1") == 1
    assert mq.position_of(ordered, "f2") == 2
    assert mq.position_of(ordered, "f3") == 3
    assert mq.position_of(ordered, "ghost") is None


def test_an_empty_queue_has_no_positions():
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


def test_a_mirror_without_a_readable_queue_is_unknown_never_free(tmp_path):
    """The mirror is derived state anyone may delete or mangle. A fresh stamp over a missing or
    non-list `tickets` used to default to an EMPTY queue — "COMPUTE FREE", the one answer this
    module promises an unreadable box never gets."""
    import json
    path = mq.mirror_path(tmp_path, "predator")
    fresh = datetime.now(UTC).isoformat()
    for doc in ({"machine": "predator", "fetched": fresh, "error": ""},
                {"machine": "predator", "fetched": fresh, "error": "", "tickets": {"j": 1}}):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
        note = mq.capability_note(tmp_path, "predator", "funscript")
        assert "COMPUTE QUEUE UNKNOWN" in note and "FREE" not in note


def test_a_naive_fetched_stamp_is_stale_not_a_crash(tmp_path):
    """capability_note runs while a bound routine's prompt is composed; a naive stamp minus an
    aware now raised TypeError out of it."""
    import json
    path = mq.mirror_path(tmp_path, "predator")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"machine": "predator", "fetched": "2026-09-30T10:00:00",
                                "tickets": [], "error": ""}), encoding="utf-8")
    assert mq.load(tmp_path, "predator")["stale"] is True
    assert "COMPUTE QUEUE UNKNOWN" in mq.capability_note(tmp_path, "predator", "funscript")


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

    mac = SimpleNamespace(exclusive=True, key_var="", name="predator", host="h", user="u",
                          port=22, host_key="", workdir="", share="", description="",
                          tags=[])
    server = SimpleNamespace(routines_home=tmp_path, libraries_home=tmp_path / "lib",
                             machines={"predator": mac})
    # `resolve_machines` owns BOTH env-var shapes the util reads — the metadata list and
    # `{machine NAME: PEM}`. `refresh` must go through it rather than assembling either by hand.
    monkeypatch.setattr("rsched.machines.resolve_machines",
                        lambda names, catalog, secrets: ([{"name": n} for n in names], {}, []))
    monkeypatch.setattr("rsched.sandbox.base_policy", lambda s: None)
    monkeypatch.setattr("rsched.utils_run.run_util",
                        lambda *a, **k: (2, "", "unknown command 'queue'"))
    out = mq.refresh(server)
    assert out["predator"]["tickets"] == []
    assert "unknown command" in out["predator"]["error"]
    assert "UNKNOWN" in mq.capability_note(tmp_path, "predator", "funscript")


def test_refresh_hands_the_util_the_shapes_the_resolver_defines(tmp_path, monkeypatch):
    """Regression, found live: the PEM dict was keyed by `key_var` instead of by MACHINE NAME, so
    the util answered "no private key available" and every mirror recorded UNKNOWN forever. The
    contract has one owner — `machines.resolve_machines` — and refresh must use it."""
    import json
    from types import SimpleNamespace

    mac = SimpleNamespace(exclusive=True, key_var="PREDATOR_AGENT_KEY", name="predator",
                          host="h", user="u", port=22, host_key="", workdir="", share="",
                          description="", tags=[])
    server = SimpleNamespace(routines_home=tmp_path, libraries_home=tmp_path / "lib",
                             machines={"predator": mac})
    monkeypatch.setattr("rsched.machines.resolve_machines",
                        lambda names, catalog, secrets: ([{"name": "predator"}],
                                                         {"predator": "PEM"}, []))
    monkeypatch.setattr("rsched.sandbox.base_policy", lambda s: None)
    seen = {}

    def fake_run(lib, util, args, *, timeout, policy, extra_secrets):
        seen.update(extra_secrets)
        return 0, json.dumps({"tickets": []}), ""

    monkeypatch.setattr("rsched.utils_run.run_util", fake_run)
    mq.refresh(server)
    # keyed by the machine NAME, which is what the util looks the PEM up under
    assert json.loads(seen["RSCHED_MACHINE_KEYS"]) == {"predator": "PEM"}
    assert json.loads(seen["RSCHED_MACHINES"]) == [{"name": "predator"}]


def _exclusive_server(tmp_path, monkeypatch, run_util):
    from types import SimpleNamespace

    mac = SimpleNamespace(exclusive=True, key_var="", name="predator", host="h", user="u",
                          port=22, host_key="", workdir="", share="", description="", tags=[])
    server = SimpleNamespace(routines_home=tmp_path, libraries_home=tmp_path / "lib",
                             machines={"predator": mac})
    monkeypatch.setattr("rsched.machines.resolve_machines",
                        lambda names, catalog, secrets: ([{"name": n} for n in names], {}, []))
    monkeypatch.setattr("rsched.sandbox.base_policy", lambda s: None)
    monkeypatch.setattr("rsched.utils_run.run_util", run_util)
    return server


def _age_the_mirror(tmp_path, machine: str, seconds: float) -> None:
    import json
    path = mq.mirror_path(tmp_path, machine)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["fetched"] = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_a_fresh_mirror_is_not_re_read(tmp_path, monkeypatch):
    """The rate is set by what READERS tolerate, not by the 5s tick that notices.

    Every read is an SSH session and a `uv run --script` interpreter boot on the box. Refreshed
    per tick that was ~17k sessions a day for a mirror whose consumers accept 15 minutes of age —
    and an unreachable box, whose attempt lives for its 20-60s connect timeout, started a new one
    every 5s onto the loop's 8-worker default executor.
    """
    import json
    calls: list[list[str]] = []

    def fake_run(lib, util, args, **kw):
        calls.append(args)
        return 0, json.dumps({"tickets": []}), ""

    server = _exclusive_server(tmp_path, monkeypatch, fake_run)
    assert mq.refresh(server)["predator"]["tickets"] == []
    assert mq.refresh(server) == {}      # the mirror is seconds old — nothing to ask the box
    assert len(calls) == 1


def test_a_mirror_past_its_ttl_is_read_again(tmp_path, monkeypatch):
    import json
    calls: list[list[str]] = []

    def fake_run(lib, util, args, **kw):
        calls.append(args)
        return 0, json.dumps({"tickets": []}), ""

    server = _exclusive_server(tmp_path, monkeypatch, fake_run)
    mq.refresh(server)
    _age_the_mirror(tmp_path, "predator", mq.REFRESH_AFTER_S + 5)
    assert "predator" in mq.refresh(server)
    assert len(calls) == 2


def test_an_unreachable_box_is_logged_once_not_once_a_minute(tmp_path, monkeypatch, caplog):
    """A box that is down stays down for hours. Only the TRANSITION is worth a daemon-log line —
    the mirror itself carries the current reason for every reader."""
    server = _exclusive_server(tmp_path, monkeypatch,
                              lambda *a, **k: (1, "", "connect: timed out"))
    with caplog.at_level("WARNING", logger="rsched.machine_queue"):
        mq.refresh(server)
        _age_the_mirror(tmp_path, "predator", mq.REFRESH_AFTER_S + 5)
        mq.refresh(server)
    assert sum("queue unreadable" in r.getMessage() for r in caplog.records) == 1
