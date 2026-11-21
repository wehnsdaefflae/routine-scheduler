"""The D39 secret gate on a BACKGROUNDED call — the two answers, and which one defers."""

import yaml

from conftest import finish, util
from helpers import server_for, set_capabilities
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20260708-090000"
SECRET = "BG_TOKEN"


def _server(routine_dir):
    s = server_for(routine_dir)
    set_capabilities(routine_dir, actions=["memory_read", "memory_write"])
    return s


def _grants(routine_dir, grants: dict) -> None:
    cfg = yaml.safe_load((routine_dir / "routine.yaml").read_text(encoding="utf-8"))
    cfg["grants"] = grants
    (routine_dir / "routine.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")


def _declaring_util(monkeypatch, record):
    """One util declaring `SECRET` as REQUIRED, with the dispatch replaced by `record` so the
    test sees exactly what the engine handed the call."""
    import rsched.engine.executor as executor_mod

    monkeypatch.setitem(executor_mod.DISPATCH, "util", record)
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {SECRET: "v-1"})
    monkeypatch.setattr("rsched.utils_lib.exists", lambda _home, _name: True)
    monkeypatch.setattr(
        "rsched.utils_run.util_needs",
        lambda _home, _name: type("N", (), {"secrets": {SECRET}, "optional": set()})())


def _background_call(scripted):
    scripted([
        {**util("mailer", say="Backgrounding a credential-using call."), "background": True},
        finish(summary="done" + " ." * 20),
    ])


def test_an_undecided_secret_is_decided_on_the_starting_turn_not_deferred(
        make_routine, scripted, monkeypatch):
    """The case that cannot be deferred: with nobody's answer on record the gate must ask, and
    an ask cannot happen inside a thread — so nothing is backgrounded and the asking turn's own
    observation carries the open request.
    """
    ran: list[str] = []

    def record(action, _ctx):
        ran.append(str(action.get("name")))
        return {"kind": "util", "name": action.get("name"), "exit": 0, "stdout": "ran",
                "stderr": ""}

    _declaring_util(monkeypatch, record)
    d = make_routine(slug="bgundecided", budgets={"ask_timeout_min": 0})
    _grants(d, {})                      # undecided: the gate asks, and nobody answers
    _background_call(scripted)
    status, run_dir = run_routine(d, _server(d), run_ts=TS)

    assert status == "ok", status
    assert ran == [], (
        "the backgrounded util RAN with a secret nobody had decided — the flag walked around "
        f"the D39 call-time gate: {ran}")
    events, _ = read_events(run_dir / "transcript.jsonl")
    obs = [e for e in events if e["type"] == "observation"]
    assert not [e for e in obs if e["payload"].get("started")], (
        "nothing may be backgrounded while the gate has an open question about the call")
    held = next(e for e in obs if e["payload"].get("pending_secrets"))
    assert held["turn"] == 1, "the decision must reach the turn that asked for the call"


def test_a_declined_secret_is_withheld_from_a_backgrounded_util_not_refused(
        make_routine, scripted, monkeypatch):
    """F756 in the background path: the call starts, the declined name is scrubbed from its
    environment, and nothing carries `declined_secrets` — that key means the call did NOT run,
    to three separate consumers. The flag must not change the answer in either direction.
    """
    seen: list[set] = []

    def record(action, _ctx):
        from rsched.engine.secretgate import withheld_for_util
        seen.append(withheld_for_util(_ctx, str(action.get("name"))).names)
        return {"kind": "util", "name": action.get("name"), "exit": 0, "stdout": "ran",
                "stderr": ""}

    _declaring_util(monkeypatch, record)
    d = make_routine(slug="bgwithhold")
    _grants(d, {f"secret:{SECRET}": False})          # declined forever
    _background_call(scripted)
    status, run_dir = run_routine(d, _server(d), run_ts=TS)

    assert status == "ok", status
    assert seen == [{SECRET}], (
        f"the declined credential must be withheld from the backgrounded call: {seen}")
    events, _ = read_events(run_dir / "transcript.jsonl")
    payloads = [e["payload"] for e in events if e["type"] == "observation"]
    assert not [p for p in payloads if p.get("declined_secrets")], (
        "`declined_secrets` means the call did NOT run — a call that ran must not carry it")
    assert [p for p in payloads if p.get("started")], "the call must still be backgrounded"
    # and no exposure question was filed: a recorded decline is an answer already given
    assert not [e for e in events if e["type"] == "question"]
