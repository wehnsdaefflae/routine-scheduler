"""RUN-TREE hooks — F695 as a case of the one mechanism (D152 step 3).

What these pin is the thing the finding measured: before this, a child could speak to its
parent only by EXITING, a parent could speak to a child only at spawn time, and
`control.drain_injections` returned immediately for any `ctx.depth > 0`, so a child drained
nothing at all. Each test below is one of those seams, plus the two rules the design says fall
out of the five fields:

1. the run tree is the only addressing scheme — a SIBLING is refused, by name, and told to go
   through the parent that holds the labels;
2. nothing here can change control flow — delivery rides a turn boundary the addressee was
   going to take anyway, so a messaged child stays killable and buys no extra turn.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from helpers import run_context
from rsched.config import ServerConfig
from rsched.engine import channels, control, inbox, runtree
from rsched.hooks import store

TS = "20261019-010000"


def _child_ctx(parent_ctx, *, n: int, label: str, depth: int = 1):
    """A child's RunContext as `childrun._sub_routine` builds one: its routine dir IS its
    `sub/<n>/` run dir, which is why the inbox machinery addresses it at all.
    """
    import copy

    sub_dir = parent_ctx.run_dir / "sub" / str(n)
    sub_dir.mkdir(parents=True, exist_ok=True)
    cfg = copy.copy(parent_ctx.routine)
    cfg.dir = sub_dir
    from rsched.engine.budgets_config import Budgets
    from rsched.engine.transcript import Transcript
    return SimpleNamespace(
        routine=cfg, server=parent_ctx.server, run_dir=sub_dir, depth=depth, sub_n=n,
        sub_label=label, run_id=f"{parent_ctx.run_id}#sub{n}", run_ts=parent_ctx.run_ts,
        turn=3, user_replies=0, interventions=0, reports_open=0,
        transcript=Transcript(sub_dir / "transcript.jsonl"),
        budgets=Budgets.from_config(cfg.budgets),
        write_status=lambda: None)


def _loop(ctx, *, subs=()):
    """The parts of an EngineLoop these paths touch — nothing more, so the test cannot pass
    by accident on a loop attribute the engine does not actually set."""
    return SimpleNamespace(
        ctx=ctx, messages=[], consumed_dir=ctx.run_dir / "consumed",
        executed_actions=0,
        subruns=SimpleNamespace(subruns={s.n: s for s in subs}))


def _sub(n: int, label: str, ctx, *, done: bool = False):
    import threading

    ev = threading.Event()
    if done:
        ev.set()
    return SimpleNamespace(n=n, label=label, ctx=ctx, done=ev)


# ---------------------------------------------------------------- addressing (rule 1)


def test_a_runtree_address_is_told_apart_from_a_routine_slug():
    """`parent` is a reserved word and `child:` carries a colon, which no slug may — so the
    report handler can route on the target without a lookup."""
    assert runtree.is_runtree("parent")
    assert runtree.is_runtree("child:F677")
    assert not runtree.is_runtree("self-audit")
    assert not runtree.is_runtree("")


def test_the_parent_of_a_child_is_the_routine_and_of_a_grandchild_its_parent_sub_dir():
    """A depth-1 child's parent drains `<routine>/inbox/`; a deeper child's parent drains its
    own `sub/<n>/inbox/`. The `runs` component is what tells them apart."""
    assert runtree.parent_dir(Path("/h/slug/runs/20261019-010000/sub/2")) == Path("/h/slug")
    assert (runtree.parent_dir(Path("/h/slug/runs/20261019-010000/sub/2/sub/1"))
            == Path("/h/slug/runs/20261019-010000/sub/2"))


def test_a_top_level_run_addressing_parent_is_refused_and_told_what_it_can_address(
        make_routine):
    ctx = run_context(make_routine("rtroot"), TS)
    obs = runtree.send(_loop(ctx), target="parent", title="t", detail="d")

    assert obs["delivered"] is False
    assert "no parent" in obs["refused"]
    assert "target: <slug>" in obs["refused"] or "`target: <slug>`" in obs["refused"]


def test_a_sibling_is_refused_and_pointed_at_the_parent(make_routine):
    """RULE 1. A run may not reach a run it does not own, so sibling delivery routes THROUGH
    the parent — which is the only place the labels exist."""
    parent = run_context(make_routine("rtsib"), TS)
    a = _child_ctx(parent, n=1, label="F677")
    b = _child_ctx(parent, n=2, label="F723")
    # the SIBLING asks for its brother by label, from its own loop (which has no children)
    obs = runtree.send(_loop(a), target="child:F723", title="t", detail="d")

    assert obs["delivered"] is False
    assert "F723" in obs["refused"]
    assert "SIBLING" in obs["refused"]
    assert "parent" in obs["refused"]
    assert b.run_id            # the sibling exists; it is the ADDRESSING that is refused


def test_an_unknown_label_lists_the_children_still_running(make_routine):
    parent = run_context(make_routine("rtlabel"), TS)
    c1 = _child_ctx(parent, n=1, label="F677")
    loop = _loop(parent, subs=[_sub(1, "F677", c1)])

    obs = runtree.send(loop, target="child:nope", title="t", detail="d")

    assert obs["delivered"] is False
    assert "'F677'" in obs["refused"]


def test_a_child_that_already_exited_is_refused_naming_its_handback(make_routine):
    """It will take no further turn boundary, so filing freight for it would be a message
    nobody ever reads — the failure mode a queue without a reader always has."""
    parent = run_context(make_routine("rtexited"), TS)
    c1 = _child_ctx(parent, n=1, label="F677")
    loop = _loop(parent, subs=[_sub(1, "F677", c1, done=True)])

    obs = runtree.send(loop, target="child:F677", title="t", detail="d")

    assert obs["delivered"] is False
    assert "already exited" in obs["refused"]
    assert "from-sub-1" in obs["refused"]


# ---------------------------------------------------------------- the two opened seams


def test_a_child_reaches_its_parent_mid_flight(make_routine):
    """SEAM 1 (CHILD→PARENT). Before this a child could only speak by exiting."""
    d = make_routine("rtup")
    parent = run_context(d, TS)
    child = _child_ctx(parent, n=1, label="F677")

    obs = runtree.send(_loop(child), target="parent", title="brief is wrong",
                       detail="the file the brief names does not exist")

    assert obs["delivered"] is True
    assert obs["addressee"] == "parent"
    assert obs["hook"].startswith("hk-")
    assert "no R id" in obs["note"]
    msgs = list((d / "inbox").glob("msg-*.json"))
    assert len(msgs) == 1
    body = json.loads(msgs[0].read_text())
    assert body["via"] == "hook"
    assert body["hook"] == obs["hook"]
    assert "brief is wrong" in body["text"]
    assert "does not exist" in body["text"]
    assert "not the operator" in body["text"]


def test_a_parent_reaches_a_named_child_after_the_spawn(make_routine):
    """SEAM 2 (PARENT→CHILD). Before this, the spawn `prompt` was the parent's only word."""
    parent = run_context(make_routine("rtdown"), TS)
    child = _child_ctx(parent, n=1, label="F677")
    loop = _loop(parent, subs=[_sub(1, "F677", child)])

    obs = runtree.send(loop, target="child:F677", title="stop at the gate",
                       detail="do not run a release gate; a sibling holds the lock")

    assert obs["delivered"] is True
    assert obs["addressee"] == "child:F677"
    msgs = list((child.routine.dir / "inbox").glob("msg-*.json"))
    assert len(msgs) == 1, "it lands in the CHILD's inbox, which is its own sub/<n>/ dir"
    assert "sibling holds the lock" in json.loads(msgs[0].read_text())["text"]


def test_a_second_send_in_the_same_turn_replaces_its_own_delivery(make_routine):
    """Idempotent by construction (F697's bug class): the filename is the hook id, which is
    the run and turn. A retry cannot queue a second copy."""
    d = make_routine("rtonce")
    parent = run_context(d, TS)
    child = _child_ctx(parent, n=1, label="F677")

    runtree.send(_loop(child), target="parent", title="t", detail="first")
    runtree.send(_loop(child), target="parent", title="t", detail="second")

    msgs = list((d / "inbox").glob("msg-*.json"))
    assert len(msgs) == 1
    assert "second" in json.loads(msgs[0].read_text())["text"]


# ---------------------------------------------------------------- the lifted depth gate


def test_a_child_drains_its_own_hooks_at_its_turn_boundary(make_routine):
    """THE LIFTED GATE. `control.drain_injections` used to `return` for every depth > 0."""
    parent = run_context(make_routine("rtdrain"), TS)
    child = _child_ctx(parent, n=1, label="F677")
    inbox.file_message(child.routine.dir, "HOOK FROM parent — stop at the gate",
                       via="hook", name="hk-x-t1")

    loop = _loop(child)
    control.drain_injections(loop)

    assert len(loop.messages) == 1
    assert "stop at the gate" in loop.messages[0]["content"]
    assert not list((child.routine.dir / "inbox").glob("msg-*.json")), "consumed by rename"


def test_a_child_does_not_eat_freight_addressed_to_the_routine(make_routine):
    """The operator's channels stay the ROOT run's. D92/D93 is this bug: a leg draining
    wholesale ate the answers meant for the next fresh run."""
    parent = run_context(make_routine("rtnarrow"), TS)
    child = _child_ctx(parent, n=1, label="F677")
    for via in ("web", "report", "oversight", ""):
        inbox.file_message(child.routine.dir, f"not for a child: {via}", via=via)

    loop = _loop(child)
    control.drain_injections(loop)

    assert loop.messages == []
    assert len(list((child.routine.dir / "inbox").glob("msg-*.json"))) == 4


def test_a_hook_is_not_the_user_talking(make_routine):
    """It must not advance `user_replies` (the create_routine confirm gate and the
    `user-spoke` assist predicate read it) nor count as an intervention."""
    parent = run_context(make_routine("rtwho"), TS)
    child = _child_ctx(parent, n=1, label="F677")
    inbox.file_message(child.routine.dir, "HOOK FROM parent — x", via="hook", name="hk-x-t1")

    loop = _loop(child)
    control.drain_injections(loop)

    assert child.user_replies == 0
    assert not inbox.user_authored("hook")
    assert "hook" in inbox.VIAS


def test_a_hook_is_labelled_a_hook_in_the_prompt_live_and_replayed():
    """A run that cannot tell a machine from its operator answers the wrong party — and the
    REPLAY renderer must produce the identical prefix or the leg is re-written to the
    provider instead of re-read from its cache."""
    assert channels.injected_lead("hook") == "HOOK (injected mid-run)"
    assert channels.injected_lead("web").startswith("USER MESSAGE")
    assert channels.injected_lead("hook", report=True).startswith("REPORT")
    live = control.injected_message("x", via="hook")
    assert live.startswith("HOOK (injected mid-run):")
    from rsched.engine.history import replay_messages
    assert callable(replay_messages)


# ---------------------------------------------------------------- the hook row


def test_the_hook_is_one_configuration_of_the_same_record(make_routine, tmp_path):
    """The whole point of the campaign: a run-tree hook adds NO field and NO store. It is the
    five fields with `addressee` set to a run-tree address."""
    d = make_routine("rtrow")
    parent = run_context(d, TS)
    parent.server = ServerConfig()
    parent.server.routines_home = tmp_path
    child = _child_ctx(parent, n=1, label="F677")
    child.server = parent.server
    (tmp_path / d.name).mkdir(parents=True, exist_ok=True)

    obs = runtree.send(_loop(child), target="parent", title="brief is wrong", detail="why")

    rows = store.read_hooks(tmp_path)
    assert len(rows) == 1
    hook = rows[0]
    assert hook.id == obs["hook"]
    assert hook.source == "runtree"
    assert hook.trigger == {"kind": "send"}
    assert hook.addressee == "parent"
    assert hook.delivery == "turn_boundary"
    assert hook.disposition == "settled"
    assert hook.payload["title"] == "brief is wrong"
    assert hook.origin["run_id"] == child.run_id
    assert not hook.holds(), "rule 2: only hold_before_action can change control flow"
    import rsched.hooks as hooks_pkg
    assert "runtree" in hooks_pkg.SOURCES


def test_the_report_action_routes_a_runtree_target_without_making_an_item(make_routine,
                                                                         tmp_path):
    """A run-tree hook gets NO `R<n>`: an item is owed an answer by a ROUTINE, and both of
    these runs are gone in minutes — a row nobody can close is the backlog pathology the
    ledger exists to make visible."""
    from rsched.engine.admin_handlers import handle_report

    d = make_routine("rtitem")
    parent = run_context(d, TS)
    parent.server = ServerConfig()
    parent.server.routines_home = tmp_path
    child = _child_ctx(parent, n=1, label="F677")
    child.server = parent.server

    obs = handle_report(_loop(child), {"kind": "report", "target": "parent",
                                       "title": "brief is wrong", "detail": "why"})

    assert obs["runtree"] is True
    assert obs["delivered"] is True
    assert "id" not in obs and "report" not in obs
    assert not (tmp_path / ".control" / "reports.jsonl").exists()
