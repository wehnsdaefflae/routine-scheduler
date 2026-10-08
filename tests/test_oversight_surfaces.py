"""The escalation ladder is VISIBLE: its events are writable, and its directive is attributable.

Two defects this pins, both found 2026-10-03 while building the ladder's surfaces (step 6), both
invisible to the ladder's own suite because `tests/test_ladder_trigger.py` stubs the transcript
with a list-appending fake:

1. `engine/transcript.py`'s `EVENT_TYPES` did not contain `oversight_directive`,
   `oversight_skipped` or `oversight_no_directive`, and `Transcript.event` opens with
   `assert type_ in EVENT_TYPES`. So EVERY ladder record path raised against the real class.
   `ladder.at_boundary` catches broadly and its fallback `oversight_skipped` raised too — a rung
   that fired would have left no trace at all, which for an oversight mechanism is the one
   failure mode that must never be silent.

2. `engine/control.inject_user_message` built the `user_injection` event without the delivery
   channel, so a rung's directive — machine-authored and binding on the run — was rendered as
   the operator speaking. The channel now rides the payload (`via`, `source`); the console reads
   it (`tests/ui/test_transcript_events.py`).

So these tests go through the REAL `Transcript` and the REAL injection path. A fake that accepts
anything cannot fail the way production did.
"""

from __future__ import annotations

import json
from pathlib import Path

from rsched.engine import control, ladder, oversight
from rsched.engine.transcript import EVENT_TYPES, Transcript

#: Every event type the ladder writes, read off the module rather than restated: a new record
#: added to `ladder.py` without a place in the vocabulary must fail HERE, not in production.
LADDER_EVENT_TYPES = ("oversight_dispatch", "oversight_directive", "oversight_skipped",
                      "oversight_no_directive")


def _types_written_by(path: Path) -> set[str]:
    """The event types a module writes, read from its source: `transcript.event("<type>"`."""
    import re
    return set(re.findall(r'transcript\.event\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))


def test_every_event_the_ladder_writes_is_in_the_vocabulary():
    """The assert in `Transcript.event` is an invariant, not input validation: a type missing
    from the tuple is an exception at the moment the record would have been made."""
    written = _types_written_by(Path(ladder.__file__))
    missing = sorted(written - set(EVENT_TYPES))
    assert not missing, f"ladder event types the transcript refuses to write: {missing}"
    # and the three are really there, so this test cannot pass by the ladder writing none
    for t in LADDER_EVENT_TYPES:
        assert t in written, f"{t} is no longer written by engine/ladder.py"


def test_the_real_transcript_accepts_each_ladder_record(tmp_path):
    """Against the real class — the fake in test_ladder_trigger.py accepts anything."""
    t = Transcript(tmp_path / "transcript.jsonl")
    t.event("oversight_dispatch", {"rung": 1, "reason": "interval", "turn": 20,
                                   "since_turn": 1, "oversight_turns": 11})
    t.event("oversight_directive", {"rung": 1, "verdict": "off_track",
                                    "disposition": "redirect", "next_rung_in": 6,
                                    "next_look": "the gate verdict"})
    t.event("oversight_skipped", {"rung": 2, "reason": "no supervisor pattern"})
    t.event("oversight_no_directive", {"rung": 2, "status": "failed"})
    t.event("oversight_failed", {"error": "AttributeError: 'EngineLoop' object has no "
                                          "attribute 'subs'"})
    t.close()
    lines = [json.loads(ln) for ln
             in (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [ln["type"] for ln in lines] == [*LADDER_EVENT_TYPES, "oversight_failed"]
    assert lines[0]["payload"]["oversight_turns"] == 11
    assert lines[1]["payload"]["verdict"] == "off_track"


class _Ctx:
    """The narrow slice of RunContext that `inject_user_message` touches."""

    def __init__(self, tmp_path: Path):
        self.transcript = Transcript(tmp_path / "transcript.jsonl")
        self.user_replies = 0
        self.routine = None

    def credit_suspended(self, _seconds: float) -> None:    # pragma: no cover - unused here
        pass


class _Loop:
    def __init__(self, ctx: _Ctx):
        self.ctx = ctx
        self.messages: list[dict] = []


def _events(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


def test_an_injection_event_carries_the_channel_that_filed_it(tmp_path):
    """A rung's directive and the operator's own message are the SAME event type. Without the
    channel on the record, nothing downstream — the console, the CLI, a reader — can tell a
    supervisor redirecting the run from the person who owns it."""
    loop = _Loop(_Ctx(tmp_path))
    control.inject_user_message(loop, {"text": "OVERSIGHT DIRECTIVE (rung 1) — do X",
                                       "via": "oversight", "source": "rung-1"})
    loop.ctx.transcript.close()
    ev = _events(tmp_path / "transcript.jsonl")[0]
    assert ev["type"] == "user_injection"
    assert ev["payload"]["via"] == "oversight"
    assert ev["payload"]["source"] == "rung-1"
    # a machine delivering is not the user speaking: the channel decides, and `oversight` is
    # not in inbox.USER_MESSAGE_VIAS
    assert loop.ctx.user_replies == 0


def test_the_operators_own_message_names_no_channel_and_still_counts_as_the_user(tmp_path):
    """The contrast that makes the label worth anything — and the guarantee that adding the two
    keys changed nothing for every other writer: an injection with no `via` carries neither."""
    loop = _Loop(_Ctx(tmp_path))
    control.inject_user_message(loop, {"text": "ignore the first half", "via": "conversation"})
    loop.ctx.transcript.close()
    payload = _events(tmp_path / "transcript.jsonl")[0]["payload"]
    assert payload["via"] == "conversation"
    assert "source" not in payload                 # nothing is invented that was not filed
    assert loop.ctx.user_replies == 1


def test_an_engine_note_still_reads_as_the_engine_not_as_a_channel(tmp_path):
    """`source == "engine"` is read by four consumers (the console renderer, rewind, search,
    the playbook distiller) and is written ONLY by the direct event writers (engine/loopnudge,
    engine/enginenote), never through this path. Adding `source` here must not let an inbox
    message claim to be the engine."""
    loop = _Loop(_Ctx(tmp_path))
    control.inject_user_message(loop, {"text": "a report arrived", "via": "report",
                                       "source": "self-audit", "report": True})
    loop.ctx.transcript.close()
    payload = _events(tmp_path / "transcript.jsonl")[0]["payload"]
    assert payload["source"] == "self-audit"
    assert payload["source"] != "engine"       # an inbox message cannot claim to be the engine
    assert payload["report"] is True


def _write(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_a_run_with_no_rung_has_no_ladder_state(tmp_path):
    """None, not an empty dict: the rail HIDES its card on None, and the overwhelming majority
    of runs have no ladder at all. An always-present empty card teaches a reader to ignore the
    one place supervision is reported."""
    from rsched.readmodels import ladder as ladder_rm
    from rsched.readmodels import memo

    memo.reset()
    _write(tmp_path / "transcript.jsonl",
           [{"type": "assistant_action", "turn": 1, "payload": {"kind": "util", "say": "hi"}}])
    assert ladder_rm.ladder_state(tmp_path) is None


def test_the_ladder_state_folds_the_rungs_into_the_three_facts_a_reader_needs(tmp_path):
    """Which rung is current, how long until the next escalation, what the last one ruled."""
    from rsched.readmodels import ladder as ladder_rm
    from rsched.readmodels import memo

    memo.reset()
    _write(tmp_path / "transcript.jsonl", [
        {"type": "oversight_dispatch",
         "payload": {"rung": 1, "reason": "interval", "turn": 20, "since_turn": 1,
                     "oversight_turns": 11}},
        {"type": "oversight_directive",
         "payload": {"rung": 1, "verdict": "off_track", "disposition": "redirect",
                     "next_rung_in": 6, "next_look": "the gate verdict"}},
    ])
    st = ladder_rm.ladder_state(tmp_path, turn=23)
    assert st is not None
    assert st["rung"] == 1
    assert st["verdict"] == "off_track" and st["disposition"] == "redirect"
    assert st["last"] == "ruled"
    # counted from the DISPATCH turn, the way the engine counts it (ctx.last_rung_turn), so the
    # strip and the trigger can never disagree: 20 + 6 - 23 = 3
    assert st["turns_to_next"] == 3
    assert st["dispatched"] == 1 and st["skipped"] == 0


def test_a_skip_is_kept_and_named_not_folded_away(tmp_path):
    """A run with the ladder ON and no supervision in it is indistinguishable from a healthy
    one unless the REASON survives. That reason is the whole value of the record."""
    from rsched.readmodels import ladder as ladder_rm
    from rsched.readmodels import memo

    memo.reset()
    _write(tmp_path / "transcript.jsonl", [
        {"type": "oversight_skipped",
         "payload": {"rung": 2, "reason": "the supervise-a-run pattern is not in the library"}},
    ])
    st = ladder_rm.ladder_state(tmp_path, turn=40)
    assert st is not None
    assert st["last"] == "skipped"
    assert "supervise-a-run" in st["last_reason"]
    assert st["skipped"] == 1 and st["dispatched"] == 0
    assert st["turns_to_next"] is None          # nothing set an interval: there is no next


def test_a_finished_run_counts_down_to_nothing(tmp_path):
    """turn=0 (a finished run read without a live turn) leaves the countdown None rather than
    inventing a next escalation that will never come."""
    from rsched.readmodels import ladder as ladder_rm
    from rsched.readmodels import memo

    memo.reset()
    _write(tmp_path / "transcript.jsonl", [
        {"type": "oversight_dispatch",
         "payload": {"rung": 1, "reason": "interval", "turn": 20, "since_turn": 1,
                     "oversight_turns": 11}},
        {"type": "oversight_directive",
         "payload": {"rung": 1, "verdict": "on_track", "disposition": "continue",
                     "next_rung_in": 6, "next_look": ""}},
        {"type": "oversight_no_directive", "payload": {"rung": 2, "status": "failed"}},
    ])
    st = ladder_rm.ladder_state(tmp_path, turn=0)
    assert st is not None
    assert st["turns_to_next"] is None
    assert st["rung"] == 2 and st["last"] == "silent"


def test_a_rendered_directive_is_the_text_the_injection_carries():
    """The prose half of the surface: what `render_directive` produces is what the console
    labels as the rung's, so the rung number has to be IN the text as well as on the record —
    a transcript read without the payload must still say who spoke."""
    text = oversight.render_directive(
        {"verdict": "off_track", "disposition": "redirect",
         "instruction": ["gate what you have"], "next_look": "", "next_rung_in": 6}, rung=1)
    assert "OVERSIGHT DIRECTIVE (rung 1)" in text
    assert "gate what you have" in text
