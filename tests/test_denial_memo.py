"""The capability refusal a run earns AGAIN — answered from the first refusal instead of being
dispatched a second time (F762).

MEASURED: `vodafone-outage-claims:20261023-024513` re-emitted a write to its OWN `main.md` at
turns 17, 18, 19 and 34; `moltbook-heartbeat:20261023-172052` at turns 34 and 35. Every attempt
was refused by the same gate (`grantpolicy.GrantPolicy.deny`, the recipe clause), whose text
already names the right route — so the defect is not the wording. It is that nothing of the
refusal survives the turn: the rejected reply and its correction are dropped as debris the
moment the turn recovers (`engine/turndebris.py`), leaving the next turn to read the run's own
reasoning and no refusal at all. The specimen says so itself at turn 18 — *"I have to correct
myself … I never attempted one"* — and then attempted it again.

Three things this file pins:

* a CAPABILITY refusal leaves a DURABLE line in the prompt, so the turn after it can read that
  the action was refused and that the refusal holds for the whole run;
* a second identical attempt is answered as a REPEAT, naming the turn it was first refused at,
  the permanence, and the route the refusal already names;
* a refusal that could legitimately succeed later — a schema rejection, a missing field, a
  transient fault — is NOT memoized and never claims permanence. That distinction is the whole
  safety of the mechanism: a run making progress through different mistakes must not be told
  its next attempt is hopeless.

…and one anti-requirement, held as a regression guard: a repeat must never END the run
(`test_a_repeated_capability_attempt_does_not_end_the_run`). F736 recorded what that costs — a
rule that killed a run at its second identical rejection would have discarded 396 turns of
finished work.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from conftest import finish
from helpers import server_for
from rsched.engine.denialmemo import PERMANENCE_MARK, answer
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.grantpolicy import GrantPolicy

TS = "20261027-090000"

#: The specimen's own action: a write to the routine's own recipe, which needs the
#: recipe-authoring capability. A routine built by `make_routine` holds none, so the gate
#: refuses it — permanently, for the whole run.
RECIPE_WRITE = {"say": "Landing the recipe correction Mark approved.", "kind": "write_file",
                "path": "main.md", "content": "# corrected recipe\n"}

#: The same refusal reached through the other write kind, for the key's kind component.
RECIPE_EDIT = {"say": "Correcting step 7 in place.", "kind": "edit_file", "path": "main.md",
               "anchor": "# Test flow", "replacement": "# Corrected flow"}

#: A SECOND recipe path — a different target under the same capability.
STAGE_WRITE = {"say": "Writing the stage module.", "kind": "write_file",
               "path": "stages/orient.md", "content": "# orient\n"}

#: A valid, side-effect-free action: the reply that RECOVERS a turn after its refusal.
READ = {"say": "Reading the recipe instead.", "kind": "read_file", "path": "main.md"}

#: A refusal that is NOT a capability one: the schema's own, and the commonest of all.
NO_SAY = {"kind": "read_file", "path": "main.md"}


def _run(make_routine, scripted, replies, *, slug="denialr", ts=TS):
    """One scripted run → (endpoint, status, transcript events)."""
    d = make_routine(slug=slug)
    ep = scripted(replies)
    status, run_dir = run_routine(d, server_for(d), run_ts=ts)
    events, _ = read_events(Path(run_dir) / "transcript.jsonl")
    return ep, status, events


def _accepted(events: list[dict]) -> list[str]:
    return [e["payload"]["kind"] for e in events if e["type"] == "assistant_action"]


def _is_correction(call: dict) -> bool:
    last = call["messages"][-1]["content"] if call["messages"] else ""
    return isinstance(last, str) and "was not a valid action" in last


def _turn_asks(ep) -> list[dict]:
    """The calls that ask for a FRESH turn — every call whose last message is not a correction.

    One per turn (plus the kickoff's), so `_turn_asks(ep)[1]` is the prompt the model reads when
    it chooses turn 2's action: exactly where a refusal earned at turn 1 either survives or does
    not.
    """
    return [c for c in ep.calls if not _is_correction(call=c)]


def _prompt_text(call: dict) -> str:
    out = []
    for msg in call["messages"]:
        content = msg.get("content")
        if isinstance(content, str):
            out.append(content)
    return "\n".join(out)


def _corrections(ep) -> list[str]:
    return [c["messages"][-1]["content"] for c in ep.calls if _is_correction(call=c)]


def test_a_capability_refusal_leaves_a_durable_line_the_next_turn_reads(make_routine, scripted):
    """The defect itself. Turn 1 attempts the recipe write and is refused; the turn recovers on
    a read, and the refusal is dropped as debris. The prompt that asks for turn 2 must still
    say the action was refused — otherwise the run reads its own reasoning and nothing else,
    which is what produced four attempts in the measured specimen.
    """
    ep, status, events = _run(make_routine, scripted, [
        RECIPE_WRITE,                            # turn 1: refused by the capability gate
        READ,                                    # …the same turn recovers
        finish(summary="refused once, recovered"),
    ])
    assert status == "ok"
    assert _accepted(events) == ["read_file", "finish"]
    turn_two = _prompt_text(_turn_asks(ep)[1])
    assert "main.md" in turn_two, (
        "the prompt asking for turn 2 does not mention the refused action at all:\n" + turn_two)
    assert PERMANENCE_MARK in turn_two, (
        "nothing durable says the refusal holds for the whole run — the next turn can only "
        "re-read its own reasoning:\n" + turn_two)


def test_a_second_identical_attempt_is_answered_as_a_repeat(make_routine, scripted):
    """The second attempt's refusal must say it is a repeat of an action already refused, name
    the turn it was first refused at, and say the refusal is permanent for this run. The
    engine's own refusal text already carries the route, so it is quoted rather than restated.
    """
    ep, status, events = _run(make_routine, scripted, [
        RECIPE_WRITE, READ,                      # turn 1: refused, recovered
        RECIPE_WRITE, READ,                      # turn 2: the IDENTICAL attempt
        finish(summary="the repeat was answered from the first refusal"),
    ])
    assert status == "ok"
    assert _accepted(events) == ["read_file", "read_file", "finish"]
    corrections = _corrections(ep)
    assert len(corrections) == 2, corrections
    first, second = corrections
    assert PERMANENCE_MARK not in first, (
        "the FIRST refusal is not a repeat and must not be answered as one:\n" + first)
    assert "REPEAT" in second, (
        "the second identical attempt was answered without saying it is a repeat:\n" + second)
    assert PERMANENCE_MARK in second, (
        "the repeat answer does not say the refusal cannot be retried away:\n" + second)
    assert "turn 1" in second, (
        "the repeat answer does not name the turn the action was first refused at:\n" + second)
    # the route is the gate's own, quoted — never a second wording of it
    assert "recipe-authoring permission" in second, second


def test_a_schema_rejection_is_never_answered_as_a_permanent_refusal(make_routine, scripted):
    """The distinction the mechanism rests on. A missing `say` is rejected twice across two
    turns — and both times the retry can legitimately succeed, so nothing may claim permanence
    and no durable line may be posted. F736's correction still does its own work here.
    """
    ep, status, events = _run(make_routine, scripted, [
        NO_SAY, READ,                            # turn 1: schema rejection, recovered
        NO_SAY, READ,                            # turn 2: the same rejection again
        finish(summary="two schema rejections, neither permanent"),
    ], slug="schemar")
    assert status == "ok"
    assert _accepted(events) == ["read_file", "read_file", "finish"]
    for text in _corrections(ep):
        assert PERMANENCE_MARK not in text, (
            "a schema rejection — which the next attempt can satisfy — was answered as a "
            "permanent refusal:\n" + text)
    for call in _turn_asks(ep):
        assert PERMANENCE_MARK not in _prompt_text(call), (
            "a schema rejection left a durable permanence claim in the prompt")


def test_a_failed_action_that_ran_is_not_memoized(make_routine, scripted):
    """A refusal is only this mechanism's business when the CAPABILITY gate produced it. An
    action that reached the executor and failed — a read of a path that does not exist — could
    succeed later in the same run (the file may be written), so it earns no permanence claim.
    """
    ep, status, events = _run(make_routine, scripted, [
        {"say": "Reading a file that is not there yet.", "kind": "read_file",
         "path": "state/not-yet.json"},
        {"say": "Reading a file that is not there yet.", "kind": "read_file",
         "path": "state/not-yet.json"},
        finish(summary="a failing read is not a refusal"),
    ], slug="missingr")
    assert status == "ok"
    assert _accepted(events) == ["read_file", "read_file", "finish"]
    for call in _turn_asks(ep):
        assert PERMANENCE_MARK not in _prompt_text(call), (
            "an action that RAN and failed was recorded as a permanent capability refusal")


def test_a_different_target_is_a_first_refusal_not_a_repeat(make_routine, scripted):
    """The memo is keyed on (kind, target, reason). Two different recipe paths are two
    different attempts: the second is refused on its own terms, not reported as a repeat of the
    first, or the sentence would be checkably false the first time the model reads it.
    """
    ep, status, events = _run(make_routine, scripted, [
        RECIPE_WRITE, READ,                      # main.md
        STAGE_WRITE, READ,                       # stages/orient.md — same capability, new target
        finish(summary="two targets, two first refusals"),
    ], slug="targetr")
    assert status == "ok"
    assert _accepted(events) == ["read_file", "read_file", "finish"]
    corrections = _corrections(ep)
    assert len(corrections) == 2, corrections
    assert "REPEAT" not in corrections[1], (
        "a refusal for a DIFFERENT target was answered as a repeat:\n" + corrections[1])


def test_the_same_target_through_another_kind_is_its_own_refusal(make_routine, scripted):
    """`write_file main.md` and `edit_file main.md` are different actions, and the key says so.
    The second is a first refusal — and it, too, leaves its own durable line.
    """
    ep, status, events = _run(make_routine, scripted, [
        RECIPE_WRITE, READ,
        RECIPE_EDIT, READ,
        finish(summary="two kinds, two first refusals"),
    ], slug="kindr")
    assert status == "ok"
    assert _accepted(events) == ["read_file", "read_file", "finish"]
    assert "REPEAT" not in _corrections(ep)[1], _corrections(ep)[1]
    assert PERMANENCE_MARK in _prompt_text(_turn_asks(ep)[2])


def test_a_repeated_capability_attempt_does_not_end_the_run(make_routine, scripted):
    """The anti-requirement, as a regression guard. Three turns each attempting the refused
    write still reach a finish: the mechanism refuses the ASK, never the run.
    """
    _ep, status, events = _run(make_routine, scripted, [
        RECIPE_WRITE, READ,
        RECIPE_WRITE, READ,
        RECIPE_WRITE, READ,
        finish(summary="still running after three refused attempts"),
    ], slug="aliver")
    assert status == "ok"
    assert _accepted(events) == ["read_file", "read_file", "read_file", "finish"]


def _fake_loop(policy: GrantPolicy, turn: int = 0) -> SimpleNamespace:
    """A loop stub with the two things an engine note needs: a message list and a transcript.

    Both are what the real loop always carries — a note that is appended without being RECORDED
    would vanish on resume, which is the defect `engine/enginenote.py` exists to prevent.
    """
    recorded: list[tuple] = []
    transcript = SimpleNamespace(event=lambda *a, **k: recorded.append((a, k)))
    loop = SimpleNamespace(ctx=SimpleNamespace(turn=turn, transcript=transcript),
                           messages=[], grants=policy)
    loop.recorded = recorded
    return loop


def test_the_verdict_is_re_asked_of_the_live_policy_never_replayed():
    """The memo enriches an answer; it never GIVES one. A one-run grant landing mid-run
    (`GrantPolicy.with_overlay`, how the user's `allow once` reaches a live run) must make the
    very next attempt legal — so the memo is consulted only when the live policy refuses again.
    """
    locked = GrantPolicy()
    refusal = locked.deny(RECIPE_WRITE)
    assert refusal, "the unlocked-recipe gate is the premise of this test"

    loop = _fake_loop(locked)
    assert answer(loop, RECIPE_WRITE, [refusal]) == [refusal]   # first refusal, unchanged
    assert loop.messages, "the first capability refusal posts its durable line"
    assert loop.recorded, "the durable line is RECORDED too, or a resumed leg loses it"

    repeat = answer(loop, RECIPE_WRITE, [refusal])
    assert PERMANENCE_MARK in repeat[0], repeat

    granted = locked.with_overlay({"action:write_recipe"}, set())
    assert granted.deny(RECIPE_WRITE) is None, "the one-run grant unlocks the recipe"
    loop.grants = granted
    # the action is legal now: validate_action yields no problems, and the memo adds none
    assert answer(loop, RECIPE_WRITE, []) == []
    # …and an unrelated problem on the same action is not dressed up as a capability repeat
    other = ["path: 'main.md' is not valid under any of the given schemas"]
    assert answer(loop, RECIPE_WRITE, other) == other
