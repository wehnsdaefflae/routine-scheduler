"""The first assist of a rule brings the rule whole.

A fired line used to end "(the full rule: read_rule name=…)" and leave the read to the run,
which almost never took it: 4 reads in 177 fires. Now the first assist of a rule to fire in a
guard scope carries the rule's body beneath its line, framed so an ERASE unbind can cut it out,
and a later assist of the same rule carries the line alone.
"""

import json
from types import SimpleNamespace

from conftest import finish, util, write_file
from helpers import prompt_text, server_for
from rsched.assists import Assist
from rsched.engine import assist
from rsched.engine.runtime import run_routine
from rsched.paths import atomic_write_json

TS = "20261009-120000"
BODY = "# rule: fix-the-cause — remove the cause\n\nTrace it back.\n"


def _assist(aid: str = "on-a-second-failure", rule: str = "fix-the-cause") -> Assist:
    return Assist(rule=rule, id=aid, moment="observation", predicate="repeated-failure",
                  payload="remind", line="change route")


def _loop(tmp_path, *, consulted=()):
    rules = tmp_path / "rules"
    rules.mkdir(exist_ok=True)
    (rules / "fix-the-cause.md").write_text(f"---\ntags: [a, b, c]\n---\n{BODY}",
                                            encoding="utf-8")
    ctx = SimpleNamespace(server=SimpleNamespace(rules_home=rules),
                          consulted_rules=set(consulted))
    return SimpleNamespace(ctx=ctx, assist_bodies=set())


def test_the_first_assist_of_a_rule_carries_its_body_and_a_later_one_the_line(tmp_path):
    loop = _loop(tmp_path)
    first = assist._delivered(loop, [_assist()])[0]
    assert first.startswith("[RULE fix-the-cause — the same call has now failed twice this run]"
                            " change route\n")
    assert assist.body_open("fix-the-cause") in first
    assert "Trace it back." in first and first.endswith(assist.body_close("fix-the-cause"))
    assert "read_rule name=" not in first          # the text is here; no pointer to it
    later = assist._delivered(loop, [_assist("on-a-correction")])[0]
    assert "Trace it back." not in later
    assert later.endswith("(the full rule: read_rule name=fix-the-cause)")


def test_a_rule_bound_mid_run_already_arrived_whole(tmp_path):
    """The bind note delivered the text; an assist of that rule adds only its line."""
    loop = _loop(tmp_path, consulted={"fix-the-cause"})
    assert "Trace it back." not in assist._delivered(loop, [_assist()])[0]
    assert loop.assist_bodies == set()


def test_a_rule_the_library_lost_keeps_the_bare_line(tmp_path):
    loop = _loop(tmp_path)
    (tmp_path / "rules" / "fix-the-cause.md").unlink()
    got = assist._delivered(loop, [_assist()])[0]
    assert got.endswith("(the full rule: read_rule name=fix-the-cause)")
    assert loop.assist_bodies == set()              # still owed, should the rule come back


def test_withdraw_cuts_only_the_framed_body():
    framed = (f"observation text\n[RULE fix-the-cause — x] change route\n"
              f"{assist.body_open('fix-the-cause')}\n{BODY}{assist.body_close('fix-the-cause')}"
              "\nmore of the observation")
    cut = assist.withdraw(framed, "fix-the-cause")
    assert cut is not None
    assert "Trace it back." not in cut
    assert cut.startswith("observation text\n[RULE fix-the-cause — x] change route\n")
    assert "[removed: the general rule fix-the-cause was unbound" in cut
    assert cut.endswith("more of the observation")
    assert assist.withdraw(framed, "ask-policy") is None
    assert assist.withdraw("no body here", "fix-the-cause") is None


def test_a_resumed_leg_owes_a_body_only_where_the_replay_lost_it():
    """A hold, a boundary note and a finish deferral replay their text verbatim; an
    observation's tail is not in the transcript, so the body that rode it is owed again."""
    loop = SimpleNamespace()
    assist.rebuild(loop, [
        {"type": "observation", "payload": {"kind": "util", "assists": ["tail-rule/a"]}},
        {"type": "observation", "payload": {"kind": "assist_hold", "assists": ["hold-rule/a"]}},
        {"type": "user_injection", "payload": {"text": "…", "assists": ["note-rule/a"]}},
        {"type": "observation", "payload": {"kind": "finish", "assist": True,
                                            "assists": ["finish-rule/a"]}},
    ])
    assert loop.assists_fired == {"tail-rule/a", "hold-rule/a", "note-rule/a", "finish-rule/a"}
    assert loop.assist_bodies == {"hold-rule", "note-rule", "finish-rule"}


def _rule(server) -> None:
    home = server.rules_home
    home.mkdir(parents=True, exist_ok=True)
    (home / "fix-the-cause.md").write_text(
        "---\ntags: [a, b, c]\nassists:\n  - id: m\n    moment: observation\n"
        "    predicate: repeated-failure\n    payload: remind\n    line: change route now\n"
        f"---\n{BODY}", encoding="utf-8")


def test_an_erase_unbind_cuts_the_body_out_and_keeps_the_observation(make_routine, scripted):
    """Erasing a rule withdraws its TEXT from the context. A body that rode another action's
    observation is cut out of it; the observation itself is the run's record and stays."""
    import yaml

    d = make_routine(slug="bodies")
    server = server_for(d)
    _rule(server)
    raw = yaml.safe_load((d / "routine.yaml").read_text(encoding="utf-8"))
    raw["rules"] = ["fix-the-cause"]
    (d / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")

    def erase_then_write():
        atomic_write_json(d / "runs" / TS / "control.json",
                          {"drop_rules": {"slugs": ["fix-the-cause"], "ts": "drop-1",
                                          "erase": True}})
        return write_file("state/a.txt")

    ep = scripted([util("nonexistent-util"), util("nonexistent-util"), erase_then_write,
                   finish()])
    status, _run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    before = json.dumps(ep.calls[2]["messages"], ensure_ascii=False)
    assert "Trace it back." in before                     # delivered with the second failure
    shown = prompt_text(ep)
    assert "Trace it back." not in shown
    assert "[removed: the general rule fix-the-cause was unbound" in shown
    assert "nonexistent-util" in shown                    # the observation it rode is kept
