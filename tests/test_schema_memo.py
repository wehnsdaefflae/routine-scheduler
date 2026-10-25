"""The rejection a run earns AGAIN — the correction that teaches, and the retry that is not
the identical ask (F736).

MEASURED on this instance's health stream (76 `wrong_turn_detected` rows, 2026-10-19..24):
`repeat_invalid` — the engine's only repeat detector — has fired ZERO times, while TEN runs
earned a BYTE-IDENTICAL rejection message on two or more SEPARATE turns, 25 turns in all.
`llmsectest-weekday:20261023-040002` was told `(root): 'say' is a required property` at turns
236, 287, 315, 331, 349, 483, 592, 613, 647, 662 and 683. The detector missed every one of
them because `completion.next_action`'s `repeated` flag compares against `prev_raw`, a local
that dies with the turn: a repeat is only ever seen INSIDE one turn, and the real phenomenon
is across turns, after the correction was dropped as debris on the recovering reply.

Two consequences, and this file holds one assertion for each:

* The correction never names what the FIELD IS. `(root): 'say' is a required property` is the
  validator's sentence about a property name; the schema carries a description of what `say`
  means and the model never sees it. Nine of 37 runs earn their FIRST rejection at turn 1,
  where there is no earlier turn to learn from, so the first correction has to teach.
* The retry is the IDENTICAL ask. Same prompt shape, same provider grammar, one more attempt.

What this must NOT do is end the run: at a second identical rejection,
`llmsectest-weekday:20261023-040002` would have died at turn 287 of 683 — it recovered from
every one of those eleven rejections. `test_the_refused_repeat_does_not_end_the_run` is that
line, and it is a REGRESSION guard (it passes on the unchanged tree) rather than a red.
"""

from __future__ import annotations

from pathlib import Path

from conftest import finish
from helpers import health_events, server_for
from rsched.engine import wrongturn
from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20261025-090000"

#: A valid, side-effect-free action — the reply that RECOVERS a turn after its rejection.
READ = {"say": "Reading the recipe.", "kind": "read_file", "path": "main.md"}

#: The specimen itself: an action the schema rejects with `(root): 'say' is a required
#: property`, which is what eleven turns of one run were told.
NO_SAY = {"kind": "read_file", "path": "main.md"}

#: A DIFFERENT rejection — an unknown kind, the `bahnbonus-seat-position` specimen's shape.
BAD_KIND = {"say": "Searching the web.", "kind": "websearch"}


def _run_home(make_routine, scripted, replies, *, slug="memor", ts=TS):
    """One scripted run, returning (routines_home, endpoint, status, transcript events) — the
    home is where the health stream lands, for the one test that reads a filed row."""
    d = make_routine(slug=slug)
    ep = scripted(replies)
    status, run_dir = run_routine(d, server_for(d), run_ts=ts)
    events, _ = read_events(Path(run_dir) / "transcript.jsonl")
    return d.parent, ep, status, events


def _run(make_routine, scripted, replies, **kw):
    _home, ep, status, events = _run_home(make_routine, scripted, replies, **kw)
    return ep, status, events


def _accepted(events: list[dict]) -> list[str]:
    return [e["payload"]["kind"] for e in events if e["type"] == "assistant_action"]


def _corrections(ep) -> list[str]:
    """Every correction the engine posted, in order — the last user message of each call that
    carries one. The retry prompt is what the model actually reads after a rejection.
    """
    out = []
    for call in ep.calls:
        last = call["messages"][-1]["content"] if call["messages"] else ""
        if isinstance(last, str) and "was not a valid action" in last:
            out.append(last)
    return out


def test_the_first_rejection_names_the_field_not_only_the_validator_message(
        make_routine, scripted):
    """`(root): 'say' is a required property` names a property; it does not say what `say` IS.
    The schema's own description does, and the model never sees it — while nine of 37 runs
    earn their first rejection at turn 1, with no earlier turn to learn from.
    """
    ep, status, events = _run(make_routine, scripted, [
        NO_SAY,                                  # rejected: `say` missing
        READ,                                    # …the same turn recovers
        finish(summary="recovered after one rejection"),
    ])
    assert status == "ok"
    corrections = _corrections(ep)
    assert len(corrections) == 1, corrections
    first = corrections[0]
    assert "say" in first
    # the schema's OWN words about the field, read out of the contract so they cannot drift
    described = ACTION_SCHEMA["properties"]["say"]["description"]
    assert described.split(".")[0] in first, (
        "the correction repeats the validator's message and never says what `say` is:\n"
        + first)
    assert _accepted(events) == ["read_file", "finish"]


def test_the_second_identical_rejection_is_named_as_a_repeat_across_turns(
        make_routine, scripted):
    """The defect itself. Two SEPARATE turns earn the identical rejection; the correction for
    the second one must say so. Today it cannot: `repeated` compares `prev_raw`, a per-turn
    local, so the second turn's correction is byte-identical to the first's.
    """
    ep, status, events = _run(make_routine, scripted, [
        NO_SAY, READ,                            # turn 1: rejected, then recovered
        NO_SAY, READ,                            # turn 2: the IDENTICAL rejection, again
        finish(summary="two turns earned the same rejection"),
    ])
    assert status == "ok"
    corrections = _corrections(ep)
    assert len(corrections) == 2, corrections
    first, second = corrections
    assert first != second, (
        "the second identical rejection was answered with the identical correction:\n"
        + second)
    assert any(word in second.lower() for word in ("identical", "same", "again")), (
        "the correction for a repeated rejection says nothing about it being a repeat:\n"
        + second)
    assert _accepted(events) == ["read_file", "read_file", "finish"]


def test_a_repeated_rejection_is_retried_with_a_different_ask(make_routine, scripted):
    """"Refuses a second identical rejection" has to mean the next ASK is different, not that
    the run ends. The engine already knows one way to make it different — drop the provider
    grammar, whose signature is exactly a minted object missing a required field — and it
    spends that lever only on the last attempt of a turn. A rejection already earned in this
    run has proven the ask does not work.
    """
    ep, status, _events = _run(make_routine, scripted, [
        NO_SAY, READ,                            # turn 1: the rejection is now known
        NO_SAY, READ,                            # turn 2: earned again
        finish(summary="the repeat was asked differently"),
    ])
    assert status == "ok"
    # the retry AFTER the repeated rejection — the call whose last message is correction #2
    idx = [i for i, c in enumerate(ep.calls)
           if isinstance(c["messages"][-1]["content"], str)
           and "was not a valid action" in c["messages"][-1]["content"]]
    assert len(idx) == 2, idx
    first_retry, repeat_retry = (ep.calls[i] for i in idx)
    assert first_retry["schema"] is not None, "the first retry is the ordinary schema'd ask"
    assert repeat_retry["schema"] is None, (
        "the retry after a rejection this run already earned posted the identical ask again")


def test_a_different_rejection_is_not_treated_as_a_repeat(make_routine, scripted):
    """The refusal must not fire on a run that is making progress through DIFFERENT mistakes:
    `rules-review:20261021-234708` earned three rejections, all different, and recovered from
    each. A correction that cries repeat on those teaches the model to ignore it.
    """
    ep, status, events = _run(make_routine, scripted, [
        NO_SAY, READ,                            # turn 1: `say` missing
        BAD_KIND, READ,                          # turn 2: a different fault entirely
        finish(summary="two different rejections"),
    ])
    assert status == "ok"
    corrections = _corrections(ep)
    assert len(corrections) == 2, corrections
    assert "identical" not in corrections[1].lower(), corrections[1]
    idx = [i for i, c in enumerate(ep.calls)
           if isinstance(c["messages"][-1]["content"], str)
           and "was not a valid action" in c["messages"][-1]["content"]]
    assert ep.calls[idx[1]]["schema"] is not None, (
        "a DIFFERENT rejection is an ordinary one — its retry keeps the schema")
    assert _accepted(events) == ["read_file", "read_file", "finish"]


def test_the_refused_repeat_does_not_end_the_run(make_routine, scripted):
    """The measured anti-requirement, held as a REGRESSION guard (it passes on the unchanged
    tree): a second identical rejection must not end the run.
    `llmsectest-weekday:20261023-040002` earned `'say' is a required property` on eleven
    separate turns — 236 through 683 — and recovered from every one. Ending at the second
    would have discarded 396 turns of work.
    """
    ep, status, events = _run(make_routine, scripted, [
        NO_SAY, READ,
        NO_SAY, READ,
        NO_SAY, READ,                            # a THIRD turn earning the same rejection
        finish(summary="still running after three identical rejections"),
    ])
    assert status == "ok"
    assert _accepted(events) == ["read_file", "read_file", "read_file", "finish"]
    assert len(_corrections(ep)) == 3


def test_an_unparseable_reply_is_told_what_the_required_fields_are(make_routine, scripted):
    """The rejection with NO object to name a field in — and the one 9 of 37 runs earn at turn
    1, where no earlier turn exists to learn from. `grant-hunter:20261020-011336` and
    `scheduler-builder:20261021-130003` both died into `turn 1: reply did not contain a
    parseable JSON object`; three separate runs earned it at turn 1 on 2026-10-23 alone.

    There is no field to quote, so the correction teaches the CONTRACT instead: an action is
    one JSON object, and these are the fields every action needs whatever its kind. Derived
    from `ACTION_SCHEMA["required"]` rather than written out here, so a contract change cannot
    leave this assertion asserting the old one.
    """
    ep, status, events = _run(make_routine, scripted, [
        "I'll start by reading the recipe. Let me look at main.md first.",   # prose, no JSON
        READ,                                    # …the same turn recovers
        finish(summary="recovered from an unparseable reply"),
    ], slug="unparser")
    assert status == "ok"
    corrections = _corrections(ep)
    assert len(corrections) == 1, corrections
    first = corrections[0]
    assert "JSON object" in first
    described = [(f, ACTION_SCHEMA["properties"].get(f, {}).get("description", ""))
                 for f in ACTION_SCHEMA["required"]]
    described = [(f, d) for f, d in described if d]
    assert described, "the contract's required fields carry no descriptions to teach with"
    for field, desc in described:
        assert field in first, (field, first)
        assert desc.split(". ")[0].rstrip(".") in first, (
            f"the correction never says what `{field}` is:\n{first}")
    assert _accepted(events) == ["read_file", "finish"]


def test_a_repeat_across_turns_files_repeat_invalid_not_a_second_rejection(
        make_routine, scripted):
    """`repeat_invalid` means "the correction taught nothing" — and until F736 it could only
    ever see a repeat INSIDE one turn (`completion.next_action`'s `prev_raw`, a local that dies
    with the turn). Measured on this instance's health stream: ZERO `repeat_invalid` rows,
    while ten runs earned a byte-identical rejection on separate turns.

    So the second turn's row must carry the repeat reason, not a second `schema_rejected` — the
    claim `health_events.py` and `docs/run-analytics.md` now both make. One row per
    (reason, turn), as everywhere else in this vocabulary.
    """
    home, _ep, status, events = _run_home(make_routine, scripted, [
        NO_SAY, READ,                            # turn 1: the rejection is earned
        NO_SAY, READ,                            # turn 2: the IDENTICAL message, again
        finish(summary="the repeat was filed as a repeat"),
    ], slug="repeatr")
    assert status == "ok"
    rows = health_events(home, event=wrongturn.EVENT)
    assert [r["reason"] for r in rows] == ["schema_rejected", "repeat_invalid"], rows
    assert [r["turn"] for r in rows] == [1, 2], rows
    assert all(r["reason"] in wrongturn.REASONS for r in rows)
    # …and the transcript says WHICH rejection was a recalled one, so a run can be read back
    recalled = [e for e in events
                if e["type"] == "error" and e["payload"].get("recalled")]
    assert len(recalled) == 1, recalled
    assert _accepted(events) == ["read_file", "read_file", "finish"]


def test_a_repeat_with_nothing_to_quote_does_not_cite_a_field_list_it_lacks(
        make_routine, scripted):
    """The OTHER specimen this item was raised on: `bahnbonus-seat-position` passed
    `kind: "websearch"` at turns 64 AND 65 (verified in this instance's health stream). Its
    rejection names `kind`, and `kind` is an enum that carries NO description in the contract —
    so `field_help` has nothing to say and the correction holds no field list.

    The repeat sentence must then point at what IS above (the problem lines) rather than at a
    list that is not there. A correction citing absent evidence teaches the model to discount
    the rest of the sentence, which is the whole mechanism this item buys.
    """
    ep, status, events = _run(make_routine, scripted, [
        BAD_KIND, READ,                          # turn 1: an unknown kind
        BAD_KIND, READ,                          # turn 2: the IDENTICAL rejection
        finish(summary="a repeat with no field to quote"),
    ], slug="nofieldr")
    assert status == "ok"
    corrections = _corrections(ep)
    assert len(corrections) == 2, corrections
    second = corrections[1]
    assert "ALREADY EARNED THIS IDENTICAL REJECTION" in second, second
    assert "field list above" not in second, (
        "the correction cites a field list it does not carry:\n" + second)
    assert "problem lines above" in second, second
    assert _accepted(events) == ["read_file", "read_file", "finish"]
