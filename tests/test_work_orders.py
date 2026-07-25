"""The `hand_off` action — the inter-routine referral channel: its schema gate, the
capability that gates it, the engine handler that files a `W<n>` and delivers it into the
TARGET's inbox, the delivery stamp the target's own drain writes back, and the Items read
model that makes the whole hand-off visible.

The load-bearing property, asserted directly below: a work order NEVER starts a run. It only
writes a file the target's next scheduled run drains.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from rsched.engine.actions import ALWAYS_KINDS, KIND_EXAMPLES, KINDS, validate_action
from rsched.engine.inbox import drain_messages
from rsched.engine.interact import handle_hand_off
from rsched.grants import GATED_KINDS, GrantPolicy
from rsched.readmodels import items
from rsched.work_orders import (
    file_work_order,
    next_id,
    read_work_orders,
    stamp_delivered,
    work_orders_path,
)


def _routine(home: Path, slug: str) -> Path:
    d = home / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(f"slug: {slug}\n", encoding="utf-8")
    return d


def _loop(tmp_path, *, slug="self-audit", run_id="self-audit:20260726-020000"):
    home = tmp_path / "routines"
    home.mkdir(parents=True, exist_ok=True)
    _routine(home, slug)
    ctx = SimpleNamespace(server=SimpleNamespace(routines_home=home),
                          routine=SimpleNamespace(slug=slug), run_id=run_id)
    return SimpleNamespace(ctx=ctx), home


# -- schema + capability gate -------------------------------------------------------------


def test_hand_off_is_a_gated_kind_with_an_example():
    assert "hand_off" in KINDS
    assert "hand_off" in GATED_KINDS          # cross-routine: it needs the capability
    assert "hand_off" not in ALWAYS_KINDS
    assert KIND_EXAMPLES["hand_off"]["kind"] == "hand_off"


def test_validate_action_hand_off():
    ok = {"say": "s", "kind": "hand_off", "target": "config-optimizer",
          "title": "token-lab lacks the memory capability",
          "detail": "run token-lab:20260725-1 turn 14 — memory_write denied"}
    assert validate_action(ok) == []
    assert validate_action({"say": "s", "kind": "hand_off", "target": "x",
                            "title": "t", "answers": "W3"}) == []
    # target and title are both required
    assert validate_action({"say": "s", "kind": "hand_off", "title": "t"})
    assert validate_action({"say": "s", "kind": "hand_off", "target": "x"})


def test_hand_off_is_denied_without_the_capability():
    obj = {"say": "s", "kind": "hand_off", "target": "self-audit", "title": "t"}
    assert validate_action(obj, grants=GrantPolicy(actions=frozenset()))
    assert validate_action(obj, grants=GrantPolicy(actions=frozenset({"hand_off"}))) == []


# -- the handler: ledger + delivery -------------------------------------------------------


def test_hand_off_files_an_id_and_delivers_to_the_target_inbox(tmp_path):
    loop, home = _loop(tmp_path)
    _routine(home, "routine-improver")
    obs = handle_hand_off(loop, {"target": "routine-improver",
                                 "title": "newsletter-digest names utils in 6 stage files",
                                 "detail": "rsched validate lists them; fix upstream too"})
    assert obs["filed"] is True
    assert obs["id"] == "W1"

    rows = read_work_orders(work_orders_path(home))
    assert len(rows) == 1
    assert rows[0]["from"] == "self-audit"
    assert rows[0]["to"] == "routine-improver"
    assert "names utils" in rows[0]["title"]

    delivered = list((home / "routine-improver" / "inbox").glob("msg-wo-*.json"))
    assert len(delivered) == 1
    msg = json.loads(delivered[0].read_text())
    assert msg["via"] == "work-order"
    assert msg["work_order"] == "W1"
    assert "WORK ORDER W1 from routine `self-audit`" in msg["text"]


def test_hand_off_never_starts_a_run(tmp_path):
    """The invariant. Delivery writes ONE file into the target's inbox and touches nothing
    else — no one-shot spool, no state, no status. The target's next scheduled run drains it.
    """
    loop, home = _loop(tmp_path)
    target = _routine(home, "global-utils-review")
    handle_hand_off(loop, {"target": "global-utils-review", "title": "t", "detail": "d"})

    written = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    assert written == ["inbox/msg-wo-W1.json", "routine.yaml"]
    # nothing was armed anywhere for the target, and no run dir came into being
    assert not (home / ".control" / "schedule-once").exists()
    assert not (target / "runs").exists()
    assert not (target / "status.json").exists()


def test_hand_off_refuses_self_target_and_unknown_target(tmp_path):
    loop, home = _loop(tmp_path)
    _routine(home, "config-optimizer")
    assert handle_hand_off(loop, {"target": "self-audit", "title": "t"})["self_target"] is True

    obs = handle_hand_off(loop, {"target": "config-optimiser", "title": "t"})
    assert obs["unknown_target"] is True
    assert "config-optimizer" in obs["suggestions"]      # a near-miss slug is offered back
    assert "config-optimizer" in obs["valid_targets"]
    assert not (home / "config-optimizer" / "inbox").exists()   # a miss delivers nothing


def test_ids_are_monotonic_across_senders(tmp_path):
    loop, home = _loop(tmp_path)
    _routine(home, "routine-improver")
    assert next_id(work_orders_path(home)) == "W1"
    handle_hand_off(loop, {"target": "routine-improver", "title": "one"})
    handle_hand_off(loop, {"target": "routine-improver", "title": "two"})
    assert [r["id"] for r in read_work_orders(work_orders_path(home))] == ["W1", "W2"]
    assert next_id(work_orders_path(home)) == "W3"


# -- delivery stamp: the target's drain reports back ---------------------------------------


def test_the_targets_drain_stamps_delivery(tmp_path):
    loop, home = _loop(tmp_path)
    target = _routine(home, "routine-improver")
    handle_hand_off(loop, {"target": "routine-improver", "title": "t", "detail": "d"})

    assert read_work_orders(work_orders_path(home))[0].get("delivered") is None

    consumed = tmp_path / "consumed"
    msgs = drain_messages(target, consumed)
    assert len(msgs) == 1
    assert msgs[0]["work_order"] == "W1"
    assert msgs[0]["from"] == "self-audit"       # keeps it out of the user-message channel

    stamp_delivered(home, msgs, run_id="routine-improver:20260726-010000")
    row = read_work_orders(work_orders_path(home))[0]
    assert row["delivered"]["run_id"] == "routine-improver:20260726-010000"


def test_a_plain_user_message_carries_no_work_order_keys(tmp_path):
    _, home = _loop(tmp_path)
    target = _routine(home, "routine-improver")
    (target / "inbox").mkdir(exist_ok=True)
    (target / "inbox" / "msg-1.json").write_text(json.dumps({"text": "hi"}), encoding="utf-8")
    msgs = drain_messages(target, tmp_path / "consumed")
    assert msgs == [{"text": "hi", "attachments": []}]
    stamp_delivered(home, msgs, run_id="r:1")     # a no-op, and it must not create the ledger
    assert not work_orders_path(home).exists()


# -- the Items read model ------------------------------------------------------------------


def _items(home: Path, audit_dir: Path) -> dict:
    return {i["id"]: i for i in items._build(*items.source_paths(audit_dir, home))["items"]}


def test_items_shows_a_work_order_and_its_lifecycle(tmp_path):
    loop, home = _loop(tmp_path)
    audit = _routine(home, "self-audit")
    target = _routine(home, "routine-improver")
    handle_hand_off(loop, {"target": "routine-improver", "title": "fix the pattern",
                           "detail": "see W1"})

    item = _items(home, audit)["W1"]
    assert item["type"] == "work_order"
    assert item["status"] == "open"           # filed; the target has not run yet
    assert item["origin"]["routine"] == "self-audit"
    assert item["to"] == "routine-improver"

    msgs = drain_messages(target, tmp_path / "consumed")
    stamp_delivered(home, msgs, run_id="routine-improver:20260726-010000")
    assert _items(home, audit)["W1"]["status"] == "in_progress"   # it is in their prompt

    # the target closes it with a hand_off back, carrying answers
    back = SimpleNamespace(ctx=SimpleNamespace(
        server=SimpleNamespace(routines_home=home),
        routine=SimpleNamespace(slug="routine-improver"),
        run_id="routine-improver:20260726-010000"))
    handle_hand_off(back, {"target": "self-audit", "title": "fixed it", "answers": "W1"})

    closed = _items(home, audit)
    assert closed["W1"]["status"] == "settled"
    assert closed["W1"]["answered_by"] == "W2"
    assert closed["W2"]["answers"] == "W1"


def test_work_order_ids_never_collide_with_bug_or_finding_ids(tmp_path):
    """`W` is its own namespace beside `F`/`D`/`R`, and the historical changelog prose scan
    must not match it — W ids postdate every archived row.
    """
    assert items.TYPE_BY_PREFIX["W"] == "work_order"
    assert items.ID_RE.findall("W7 and F3") == ["F3"]
    assert set(items.REF_RE.findall("W7 and F3")) == {"W7", "F3"}


def test_a_failed_write_is_reported_not_swallowed(tmp_path):
    """Unlike report_bug's best-effort append, a hand-off that did not land must say so — a
    run that believes it routed work will not route it again.
    """
    home = tmp_path / "routines"
    target = _routine(home, "routine-improver")
    (home / ".control").mkdir(parents=True, exist_ok=True)
    # a directory where the ledger's lock file must go makes the append fail
    (home / ".control" / "work-orders.lock").mkdir()
    assert file_work_order(home, sender="self-audit", run_id="r:1", target="routine-improver",
                           target_dir=target, title="t", detail="d") is None
