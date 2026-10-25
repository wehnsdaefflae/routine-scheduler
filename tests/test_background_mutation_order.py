"""D118 phase 3 — a backgrounded MUTATION, ordered by the targets actions declare.

Phase 1 refused every mutation outright, with one reason: a later synchronous action would
read state the deferred one has not written yet. Widening the allowlist is only safe if that
is made IMPOSSIBLE rather than unlikely, so the contract these tests pin is:

* a backgrounded mutation declares its target in the field the action already carries
  (`path` for the file kinds, `name` for `write_util`/`memory_write`);
* a later action that TOUCHES that target — reads it, writes it, or runs a script/util over
  it — is HELD until the write has landed. The barrier closes on the dependency, so a run
  that forgets to `wait` still cannot read stale state;
* two backgrounded writes to the SAME target cannot be ordered against each other, so the
  second is REFUSED with a message that teaches the call that works;
* an action touching nothing a pending write owns is not delayed at all.

The ordering half is proved with a REAL thread doing a REAL slow write: a mock cannot fail
the way a racing thread does.
"""

import threading
import time
from types import SimpleNamespace

import pytest

from rsched.engine import background_order
from rsched.engine.actions import (
    BACKGROUNDABLE_KINDS,
    KIND_FIELDS,
    normalize_action,
    validate_action,
)
from rsched.engine.background import Pending

# ---- the field: a mutation may now carry the flag ----------------------------------------------

MUTATIONS = ("write_file", "edit_file", "memory_write")


@pytest.mark.parametrize("kind", MUTATIONS)
def test_a_targeted_mutation_may_be_backgrounded(kind):
    """The operator's answer: widen BACKGROUNDABLE_KINDS to mutations behind a dependency /
    explicit-barrier model. Each of these carries the target the barrier keys on."""
    assert kind in BACKGROUNDABLE_KINDS
    assert "background" in KIND_FIELDS[kind][1], (
        f"{kind} is backgroundable but KIND_FIELDS does not allow the field, so "
        "validate_action would reject every use of it")


@pytest.mark.parametrize("kind", MUTATIONS)
def test_a_backgrounded_mutation_validates_clean(kind):
    action = {"say": "x", "kind": kind, "background": True}
    for field in KIND_FIELDS[kind][0]:
        # `name` is a kebab-case slug in the util and memory namespaces; `path` is a path.
        action[field] = {"path": "notes/a.md", "name": "a-topic"}.get(field, "x")
    if kind == "memory_write":
        action["about"] = "what this note holds and when to consult it"
    if kind in ("memory_write", "write_util"):
        action["content"] = "the whole new body of the note or script"
    if kind == "edit_file":
        action["anchor"] = "old text"
    assert validate_action(normalize_action(action)) == []


def test_a_mutation_this_model_cannot_order_is_still_refused():
    """`delete`, `move` and `mkdir` stay out because nothing about them is slow, so there is no
    latency to win, and a barrier keyed on one declared target cannot order `move`'s two ends.
    `write_util` and `write_rule` stay out for a harder reason: no `executor.DISPATCH` entry
    (their capability gate lives in `actionroute._route`), so the thread would raise KeyError —
    and `test_background_actions.py::test_the_background_runner_matches_the_foreground_routing_table`
    forbids fixing that with a DISPATCH entry, because it would cement a gate bypass (F633)."""
    for kind in ("delete", "move", "mkdir", "write_util", "write_rule"):
        assert kind not in BACKGROUNDABLE_KINDS


def test_the_control_kinds_are_still_not_backgroundable():
    for kind in ("finish", "ask_user", "report", "spawn", "subtask", "wait", "kill"):
        assert kind not in BACKGROUNDABLE_KINDS


# ---- the targets an action declares ------------------------------------------------------------

def test_the_target_of_each_mutation_kind_is_read_off_the_field_it_carries():
    assert background_order.target_of(
        {"kind": "write_file", "path": "notes/a.md"}) == "notes/a.md"
    assert background_order.target_of(
        {"kind": "edit_file", "path": "notes/a.md"}) == "notes/a.md"
    assert background_order.target_of({"kind": "memory_write", "name": "quirks"}) == "quirks"
    assert background_order.target_of({"kind": "util", "name": "websearch"}) is None
    # `write_util` is a mutation with a perfectly good target and is still NOT admitted: its
    # handler is absent from `executor.DISPATCH` (the capability gate is in
    # `actionroute._route`), so a backgrounded one would raise KeyError in the thread.
    assert background_order.target_of({"kind": "write_util", "name": "mailer"}) is None


def test_an_action_touches_a_target_it_reads_writes_or_runs_over():
    target = "notes/a.md"
    assert background_order.touches({"kind": "read_file", "path": target}, target)
    assert background_order.touches({"kind": "read_file", "paths": ["x", target]}, target)
    assert background_order.touches({"kind": "write_file", "path": target}, target)
    assert background_order.touches({"kind": "edit_file", "path": target}, target)
    assert background_order.touches({"kind": "view_image", "path": target}, target)
    assert background_order.touches({"kind": "delete", "path": target}, target)
    assert background_order.touches({"kind": "move", "src": target, "dst": "b"}, target)
    assert background_order.touches({"kind": "move", "src": "b", "dst": target}, target)
    assert background_order.touches({"kind": "decide", "files": [target]}, target)
    # a script or util RUN OVER the target names it in its args
    assert background_order.touches({"kind": "script", "name": "render", "args": [target]}, target)
    assert background_order.touches({"kind": "util", "name": "json", "args": ["get", target]},
                                    target)
    # and the memory/util namespaces are their own: a name matches a name
    assert background_order.touches({"kind": "memory_read", "name": "quirks"}, "quirks")
    assert not background_order.touches({"kind": "memory_read", "name": "other"}, "quirks")
    assert not background_order.touches({"kind": "read_file", "path": "notes/b.md"}, target)
    assert not background_order.touches({"kind": "llm", "prompt": "unrelated"}, target)


def test_a_then_script_riding_a_write_touches_what_it_names():
    assert background_order.touches(
        {"kind": "write_file", "path": "other.md", "then_script": ["check", "notes/a.md"]},
        "notes/a.md")


# ---- the barrier: a dependency holds the later action ------------------------------------------

def _loop_with_pending(target, work, kind="write_file"):
    """A loop stand-in holding ONE live background mutation whose thread runs `work`.

    The claim is registered through the real `claim()` the engine calls, so these tests
    exercise the shipped path rather than a hand-placed attribute.
    """
    pending = Pending(handle="bg1", kind=kind, brief=target, turn=1, thread=None)  # type: ignore[arg-type]
    pending.thread = threading.Thread(target=work, name="rsched-background-bg1", daemon=True)
    loop = SimpleNamespace(_background=[pending], _background_seq=1)
    field = background_order.TARGET_FIELD[kind]
    background_order.claim(loop, {"kind": kind, field: target}, {"handle": "bg1"})
    pending.thread.start()
    return loop, pending


def test_a_later_action_that_touches_a_pending_write_reads_what_it_wrote(tmp_path):
    """The whole point of phase 3. The write takes its time; the read is held until it lands,
    so the stale content is unreachable even though nothing called `wait`."""
    target = tmp_path / "a.md"
    target.write_text("STALE")

    def slow_write():
        time.sleep(0.4)
        target.write_text("FRESH")

    loop, pending = _loop_with_pending(str(target), slow_write)
    waited = background_order.await_dependencies(loop, {"kind": "read_file", "path": str(target)})

    assert waited == ["bg1"], "the read was not held on the pending write"
    assert pending.done, "await_dependencies returned before the write landed"
    assert target.read_text() == "FRESH"


def test_an_unrelated_action_is_not_held(tmp_path):
    """A barrier that held everything would make the feature pointless."""
    target = tmp_path / "a.md"
    started = threading.Event()

    def slow_write():
        started.set()
        time.sleep(1.5)   # writes nothing: this test only needs the pending to stay LIVE

    loop, pending = _loop_with_pending(str(target), slow_write)
    started.wait(1.0)
    began = time.monotonic()
    waited = background_order.await_dependencies(
        loop, {"kind": "read_file", "path": str(tmp_path / "unrelated.md")})
    elapsed = time.monotonic() - began

    assert waited == []
    assert elapsed < 0.5, f"an unrelated read waited {elapsed:.2f}s on a pending write"
    assert not pending.done


def test_a_finished_pending_holds_nobody(tmp_path):
    target = tmp_path / "a.md"

    def quick_write():
        target.write_text("FRESH")

    loop, pending = _loop_with_pending(str(target), quick_write)
    pending.thread.join(2.0)
    assert background_order.await_dependencies(
        loop, {"kind": "read_file", "path": str(target)}) == []


# ---- the conflict: two deferred writes to one target cannot be ordered -------------------------

def test_a_second_background_write_to_the_same_target_is_refused(tmp_path):
    target = tmp_path / "a.md"

    def slow_write():
        # Writes NOTHING: this test needs only a pending that is still LIVE. A thread that
        # outlives `tmp_path` writes into a deleted directory, and pytest reports that as an
        # unhandled thread exception against whatever test happened to run next.
        time.sleep(1.0)

    loop, _pending = _loop_with_pending(str(target), slow_write)
    refusal = background_order.refuse_conflict(
        loop, {"kind": "write_file", "path": str(target), "content": "x", "background": True})

    assert refusal is not None, "two deferred writes to one target were allowed to race"
    assert refusal.get("refused") is True
    text = refusal["error"]
    assert "bg1" in text, "the refusal must name the call already holding the target"
    assert "wait" in text, "the refusal must teach the call that works"


def test_a_background_write_to_a_free_target_is_not_refused(tmp_path):
    target = tmp_path / "a.md"

    def slow_write():
        # Writes NOTHING: this test needs only a pending that is still LIVE. A thread that
        # outlives `tmp_path` writes into a deleted directory, and pytest reports that as an
        # unhandled thread exception against whatever test happened to run next.
        time.sleep(1.0)

    loop, _pending = _loop_with_pending(str(target), slow_write)
    assert background_order.refuse_conflict(
        loop, {"kind": "write_file", "path": str(tmp_path / "b.md"), "content": "x",
               "background": True}) is None


def test_a_foreground_write_to_a_pending_target_is_ordered_not_refused(tmp_path):
    """A SYNCHRONOUS write to the same target is safe — the barrier orders it behind the
    deferred one. Only a second DEFERRED write has no defined order."""
    target = tmp_path / "a.md"

    def slow_write():
        time.sleep(0.3)
        target.write_text("FRESH")

    loop, _pending = _loop_with_pending(str(target), slow_write)
    action = {"kind": "write_file", "path": str(target), "content": "x"}
    assert background_order.refuse_conflict(loop, action) is None
    assert background_order.await_dependencies(loop, action) == ["bg1"]
