"""A per-secret DECLINE withholds THAT SECRET — it does not refuse the whole util (F756).

The live specimen, measured by `llmsectest-weekday:20261023-040002` and called a bug by the
operator: he granted the `GMAIL_*` secrets to `gu mail` and declined the `FAU_*` ones forever.
The engine then refused EVERY call of that util — including `--account default`, which needs no
FAU credential. The grant UI let him express per-secret intent the enforcement could not honour.

The rule these tests pin: **a secret decline protects the CREDENTIAL, not the util.** The
declined name is scrubbed from the child environment exactly as a `?`-declared optional one is
(F290), the call proceeds, and the observation says how many were withheld because he declined
them. A util that genuinely cannot work without it fails INSIDE, visibly, with its own message —
which is what `_exposable` already accepts for a declared-but-unset secret. Forbidding a util is
a `util:` grant or a reservation, never a secret row.

Three invariants ride along, because moving a refusal must not move a credential:

* the declined VALUE never reaches the child env on any path (`scoped_env` is the seam);
* a denial still enumerates nothing in what the model reads (R17);
* an UNDECIDED secret is not a decline — that call still does not run.
"""

from __future__ import annotations

import threading
import time

from conftest import finish
from helpers import server_for, transcript_events
from rsched import utils_lib, utils_run
from rsched.engine.runtime import run_routine
from rsched.paths import atomic_write_json, read_json

TS = "20260708-070000"
#: the specimen's shape: one credential he granted, one he declined, in one util's declarations
GRANTED, DECLINED = "GMAIL_APP_PASSWORD", "FAU_PASSWORD"
STORE = {GRANTED: "gmail-secret-value", DECLINED: "fau-secret-value"}


def _mailish(monkeypatch, calls: list, optional: set | None = None) -> None:
    """`gu mail`'s shape: one util declaring both credentials as REQUIRED, recording every
    call the engine dispatches with the exact withhold set it was given."""
    from rsched import secrets as secrets_mod

    def fake_run_util(home, name, args, timeout=0, policy=None, extra_secrets=None,
                      withhold_secrets=None, **_kw):
        calls.append({"name": name, "args": list(args),
                      "withhold": set(withhold_secrets or ())})
        return 0, "ran", ""

    monkeypatch.setattr(utils_run, "run_util", fake_run_util)
    monkeypatch.setattr(utils_lib, "exists", lambda home, name: True)
    monkeypatch.setattr(utils_run, "util_needs", lambda home, name: utils_run.UtilNeeds(
        {GRANTED, DECLINED}, False, optional or set(), True, ()))
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: dict(STORE))


def _grants(routine_dir, grants: dict) -> None:
    import yaml as _yaml

    cfg = _yaml.safe_load((routine_dir / "routine.yaml").read_text())
    cfg["grants"] = grants
    (routine_dir / "routine.yaml").write_text(_yaml.safe_dump(cfg))


def _util_obs(run_dir) -> dict:
    return next(e["payload"] for e in transcript_events(run_dir)
                if e["type"] == "observation" and e["payload"].get("kind") == "util")


def test_the_specimen_runs_with_the_declined_secret_withheld(make_routine, scripted,
                                                             monkeypatch):
    """F756's acceptance: GMAIL granted, FAU declined forever, and the call that needs only
    the granted credential. Before this it was refused outright ("the util was not run");
    now it runs and the FAU name is withheld from its environment.
    """
    calls: list = []
    _mailish(monkeypatch, calls)
    d = make_routine(slug="mailgate")
    _grants(d, {f"secret:{GRANTED}": True, f"secret:{DECLINED}": False})
    scripted([{"say": "read the default account", "kind": "util", "name": "mail",
               "args": ["--account", "default"]}, finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    assert [c["args"] for c in calls] == [["--account", "default"]], "the call must RUN"
    assert calls[0]["withhold"] == {DECLINED}, "exactly the declined name is withheld"
    obs = _util_obs(run_dir)
    assert obs["exit"] == 0
    assert "declined_secrets" not in obs, (
        "`declined_secrets` means the call did NOT run to three consumers "
        "(observations._FAILURE_KEYS, requests._UNDISPATCHED_KEYS, format_observation): a "
        "call that ran must not carry it")
    assert obs["withheld_declined"] == 1
    # the exposure question was never asked: a recorded decline is an answer already given
    assert not any(e["type"] == "question" for e in transcript_events(run_dir))


def test_the_declined_value_cannot_reach_the_child_env(make_routine, scripted, monkeypatch):
    """THE SECURITY INVARIANT, proven over the two links that compose it: the engine hands
    the declined name to the runner's `withhold_secrets`, and `scoped_env` — the one
    declared-only injection gate every jailed subprocess goes through — scrubs exactly that
    name while the granted value is still injected. Moving a refusal must not expose a
    credential.
    """
    from rsched import secrets as secrets_mod

    calls: list = []
    _mailish(monkeypatch, calls)
    d = make_routine(slug="mailenv")
    _grants(d, {f"secret:{GRANTED}": True, f"secret:{DECLINED}": False})
    scripted([{"say": "call it", "kind": "util", "name": "mail", "args": []}, finish()])
    run_routine(d, server_for(d), run_ts=TS)
    assert calls, "the call must run for this invariant to be about anything"
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: dict(STORE))
    env = utils_run.scoped_env({GRANTED, DECLINED}, withhold=calls[0]["withhold"])
    assert DECLINED not in env, "the declined NAME must not be in the child env"
    assert STORE[DECLINED] not in env.values(), "nor its VALUE, under any name"
    assert env[GRANTED] == STORE[GRANTED], "the granted credential is still injected"


def test_what_the_model_reads_enumerates_no_declined_name(make_routine, scripted, monkeypatch):
    """R17 survives the change: a denial enumerates nothing. The rendered observation — what
    the model actually reads — carries a COUNT and the repair route, never the names the user
    just protected.
    """
    from rsched.engine.observations import format_observation

    calls: list = []
    _mailish(monkeypatch, calls)
    d = make_routine(slug="mailmute")
    _grants(d, {f"secret:{GRANTED}": False, f"secret:{DECLINED}": False})
    scripted([{"say": "call it", "kind": "util", "name": "mail", "args": []}, finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    assert calls and calls[0]["withhold"] == {GRANTED, DECLINED}
    obs = _util_obs(run_dir)
    assert obs["withheld_declined"] == 2
    rendered = format_observation(obs)
    assert GRANTED not in rendered and DECLINED not in rendered
    assert "2" in rendered and "declined" in rendered
    # and the run is TOLD, so an auth failure inside the util is not a mystery (R2621)
    assert "routine page" in rendered


def test_a_decline_at_the_ask_withholds_exactly_as_a_recorded_one_does(make_routine, scripted,
                                                                       monkeypatch):
    """One word, one meaning: a decline given AT THE EXPOSURE ASK is the same user intent as
    a `grants:` false row, so it withholds and the call proceeds too. `requests._seed` puts
    the entity in `ctx.denied_now` before this gate resumes, which is why one code path can
    serve both.
    """
    calls: list = []
    _mailish(monkeypatch, calls)
    d = make_routine(slug="mailask", budgets={"ask_timeout_min": 1})
    _grants(d, {f"secret:{GRANTED}": True})          # FAU_PASSWORD: undecided -> it asks

    def decline_soon():
        deadline = time.time() + 20
        while time.time() < deadline:
            recs = list((d / "questions" / "pending").glob("*.json"))
            if recs:
                rec = read_json(recs[0])
                atomic_write_json(d / "inbox" / f"answer-{rec['qid']}.json",
                                  {"qid": rec["qid"], "decision": "deny_forever",
                                   "text": "no", "source": "web"})
                return
            time.sleep(0.02)

    th = threading.Thread(target=decline_soon)
    th.start()
    scripted([{"say": "call it", "kind": "util", "name": "mail", "args": []}, finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    th.join()
    assert status == "ok"
    assert calls, "a decline at the ask must withhold, not refuse the call"
    assert calls[0]["withhold"] == {DECLINED}
    assert _util_obs(run_dir)["withheld_declined"] == 1


def test_an_undecided_secret_is_not_a_decline(make_routine, scripted, monkeypatch):
    """The UNDECIDED paths must not move: an unanswered exposure request is not an answer,
    so that call still does not run — and no secret is handed over while nobody has decided.
    """
    calls: list = []
    _mailish(monkeypatch, calls)
    d = make_routine(slug="mailwait", budgets={"ask_timeout_min": 0})
    _grants(d, {f"secret:{GRANTED}": True})          # FAU_PASSWORD undecided, nobody answers
    scripted([{"say": "call it", "kind": "util", "name": "mail", "args": []}, finish()])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    assert calls == [], "an undecided secret must still block the call"
    obs = _util_obs(run_dir)
    assert obs["pending_secrets"] == [DECLINED]
    assert "exit" not in obs


def test_a_declined_secret_a_script_declares_is_withheld_too(make_routine, scripted,
                                                             monkeypatch):
    """The same gate serves the routine's own `scripts/<name>.py` (one jail, one env), so the
    script path must proceed withheld as well — its env builder already filters on
    `secret_state`, so the only thing that refused it was this gate.
    """
    from rsched import scripts as scripts_mod
    from rsched import secrets as secrets_mod

    ran: list = []
    monkeypatch.setattr(secrets_mod, "load_secrets", lambda: dict(STORE))
    monkeypatch.setattr(scripts_mod, "needs",
                        lambda home, name, lib: ({GRANTED, DECLINED}, "none", set()))
    monkeypatch.setattr(scripts_mod, "run_script",
                        lambda home, name, args, **kw: (ran.append(dict(kw.get("env_secrets")
                                                                        or {})), (0, "ran", ""))[1])
    d = make_routine(slug="scriptgate")
    _grants(d, {f"secret:{GRANTED}": True, f"secret:{DECLINED}": False})
    (d / "scripts").mkdir(exist_ok=True)
    (d / "scripts" / "probe.py").write_text(
        '"""probe — fixture.\n\nusage: probe\nnet: none\n'
        f'secrets: {GRANTED}, {DECLINED}\n"""\nprint("ok")\n')
    scripted([{"say": "run it", "kind": "script", "name": "probe", "args": []}, finish()])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert status == "ok"
    assert ran, "the script must RUN with the declined credential withheld"
    assert DECLINED not in ran[0] and ran[0].get(GRANTED) == STORE[GRANTED]
