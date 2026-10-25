"""F750: a person must be able to start a supervisory run by hand WHILE the run it would
supervise is live — the whole point of a supervisor is to act on a run in flight.

The friction this fixes, measured: two UI toasts on 2026-10-16 (11:18:24 and 11:58:56), both
"routine fau is busy", the operator pressing run-now with the brief "rung" while
`fau:20261016-125013` was live. The busy rule (`runner.fire_blocker`) is RIGHT for that door —
`runner.active` is keyed by slug and two concurrent runs of one routine would race its state —
so this does not exempt the manual fire. It opens the door the ladder already has: a rung runs
IN-PROCESS at the live run's own turn boundary (`ladder.escalate` → an OVERSIGHT child), which
is exactly "a supervisory run against a run in flight" and costs the worker no turns of its own.

Three properties are the whole contract, and each is a test below:

1. **It does not depend on a knob the operator never set.** `ladder.enabled` is off by default
   on every routine; a person asking to supervise THIS run gets the rung whatever the config
   says. The request is the authorization.
2. **It is honoured at the next turn boundary and consumed once.** A request is not a mode:
   one ask, one rung.
3. **It cannot fail the worker.** The hard skip (an absent supervisor pattern) still skips, and
   the operator is told so on the surface he asked from rather than by silence.
"""

import json
from pathlib import Path

from rsched.engine import ladder
from test_ladder_trigger import _Ctx, _directive, _Loop, _Sub, _Subs


def _request(run_dir: Path, **extra) -> None:
    """What the web layer writes when a person presses 'supervise now' on a live run."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "control.json").write_text(
        json.dumps({"supervise_now": {"ts": "2026-10-25T03:00:00+02:00", **extra}}),
        encoding="utf-8")


def test_a_person_can_ask_a_live_run_for_a_rung_while_the_ladder_is_off(tmp_path):
    """Property 1. The ladder ships disabled, so a request that needed `enabled` would be a
    door the operator cannot open on the run he is actually watching.
    """
    ctx = _Ctx(tmp_path, turn=4)              # interval nowhere near due, ladder off
    ctx.root_run_dir = tmp_path / "run"
    _request(ctx.root_run_dir)
    loop = _Loop(ctx, _Subs(_Sub(summary=json.dumps(_directive()))))

    ladder.at_boundary(loop)

    assert "oversight_dispatch" in ctx.transcript.kinds(), (
        "a human request must fire a rung even with ladder.enabled false: "
        f"{ctx.transcript.kinds()}")
    assert loop.subruns.calls, "no supervisory child was started"
    assert loop.subruns.calls[0]["action"]["workflow"] == ladder.SUPERVISOR_WORKFLOW


def test_the_request_names_the_person_as_the_reason_so_a_reader_can_tell_who_asked(tmp_path):
    """A rung the operator asked for and a rung the interval produced are different events for
    whoever reads the transcript later: the reason is the only thing that says which.
    """
    ctx = _Ctx(tmp_path, turn=4)
    ctx.root_run_dir = tmp_path / "run"
    _request(ctx.root_run_dir)
    loop = _Loop(ctx, _Subs(_Sub(summary=json.dumps(_directive()))))

    ladder.at_boundary(loop)

    dispatch = [p for k, p in ctx.transcript.events if k == "oversight_dispatch"]
    assert dispatch, ctx.transcript.kinds()
    assert dispatch[0]["reason"] == ladder.REQUESTED_BY_PERSON, dispatch[0]


def test_one_ask_is_one_rung(tmp_path):
    """Property 2. A request consumed once — the second boundary must not fire again, or a
    single click becomes a rung on every turn for the rest of the run.
    """
    ctx = _Ctx(tmp_path, turn=4)
    ctx.root_run_dir = tmp_path / "run"
    _request(ctx.root_run_dir)
    loop = _Loop(ctx, _Subs(_Sub(summary=json.dumps(_directive()))))

    ladder.at_boundary(loop)
    first = len(loop.subruns.calls)
    ctx.turn += 1
    ladder.at_boundary(loop)

    assert first == 1, "the first boundary must fire exactly one rung"
    assert len(loop.subruns.calls) == 1, (
        "the request was honoured twice — one ask must be one rung")


def test_a_missing_supervisor_pattern_still_skips_and_says_so(tmp_path):
    """Property 3. The hard skip (operator, 2026-10-02) is not weakened by the new door: a
    generic child holding authority over a run is the worst outcome in the design.
    """
    ctx = _Ctx(tmp_path, turn=4, library=False)
    ctx.root_run_dir = tmp_path / "run"
    _request(ctx.root_run_dir)
    loop = _Loop(ctx, _Subs(_Sub(summary=json.dumps(_directive()))))

    ladder.at_boundary(loop)

    skipped = [p for k, p in ctx.transcript.events if k == "oversight_skipped"]
    assert skipped, ctx.transcript.kinds()
    assert not loop.subruns.calls, "no child may start without the supervisor pattern"


def test_a_live_run_can_be_asked_for_a_supervisory_rung(api_client, make_routine):
    """THE WEB DOOR, end to end. The endpoint lives here rather than in `tests/test_api.py`
    because that file is 3,011 lines against the repo's 350-line bar and may only shrink; the
    item's own tests are the right home for it anyway.

    It must record the shape `switches.person_asked` reads — a `supervise_now` object carrying a
    `ts`, which is what makes it an edge trigger rather than a mode — and refuse a terminal run,
    which has no turn boundary left to honour the ask at.
    """
    from conftest import mk_run
    from rsched.paths import atomic_write_json, read_json

    c, tmp = api_client
    make_routine(slug="apir")
    run_dir = mk_run(tmp / "routines" / "apir", "20260709-110000", "running")
    rid = "apir:20260709-110000"

    body = c.post(f"/api/runs/{rid}/supervise").json()
    assert body["ok"] and "turn boundary" in body["supervise"], body
    ask = read_json(run_dir / "control.json")["supervise_now"]
    assert isinstance(ask, dict) and ask.get("ts"), ask

    atomic_write_json(run_dir / "status.json", {"run_id": rid, "state": "finished"})
    assert c.post(f"/api/runs/{rid}/supervise").status_code == 409


def test_a_run_with_no_request_is_untouched(tmp_path):
    """The read must be cheap and silent: every turn boundary of every run passes through it,
    and a run nobody asked about must behave exactly as before.
    """
    ctx = _Ctx(tmp_path, turn=4)
    ctx.root_run_dir = tmp_path / "run"
    (ctx.root_run_dir).mkdir(parents=True, exist_ok=True)
    loop = _Loop(ctx, _Subs(_Sub(summary=json.dumps(_directive()))))

    ladder.at_boundary(loop)

    assert ctx.transcript.kinds() == [], ctx.transcript.kinds()
    assert not loop.subruns.calls
