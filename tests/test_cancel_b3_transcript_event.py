"""F586 / D160-C, step B3: the CANCEL LEAVES A TRACE an audit can find.

D160's resolution, verbatim (operator, 2026-09-30): option C, *"B plus a transcript event, so an
audit of the run afterwards SEES the human intervention instead of inferring it from a gap"*.

Why the observation's `cancelled` flag was not already enough: an observation records what the
RUN was told. The event records that somebody OUTSIDE the run reached in — and that is the part
a later reader cannot reconstruct, because a cancelled call and a call that failed fast are
indistinguishable in a record of outcomes alone.

These tests are written the way the ladder's step 6 proved they must be (2026-10-03). That
feature shipped FOUR releases recording nothing at all: its four event types were missing from
`EVENT_TYPES`, `Transcript.event` opens with `assert type_ in EVENT_TYPES`, and its caller
caught `Exception` by design — so every record path raised and the fallback raised too. Its own
suite could not see it, because the suite stubbed the transcript with a list-appending fake. So:

* one test goes through the REAL `Transcript` (a fake that accepts anything cannot fail the way
  production does), and
* the vocabulary test DERIVES the types from the source that writes them instead of restating a
  list — which is what found the ladder's forgotten fourth type.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from rsched.cli_render import _render_event
from rsched.engine.transcript import EVENT_TYPES, Transcript, read_events

SRC = Path(__file__).resolve().parent.parent / "src" / "rsched"
STATIC = Path(__file__).resolve().parent.parent / "static"


# -- the vocabulary, derived from the source that writes it -------------------------------

def test_every_event_the_loop_writes_is_in_the_vocabulary():
    """Greps `transcript.event("<type>"` out of the engine's own sources and diffs against the
    tuple. A restated list cannot catch an ADDITION — this shape is the only vocabulary test
    that can, and it is what surfaced `oversight_dispatch` when a manual grep for three known
    names missed it.
    """
    written: set[str] = set()
    for path in sorted(SRC.rglob("*.py")):
        written |= set(re.findall(r'\.event\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    missing = written - set(EVENT_TYPES)
    assert not missing, (f"these types are WRITTEN but not in EVENT_TYPES, so "
                         f"Transcript.event's assert makes them record nothing: {missing}")


def test_the_cancel_type_is_in_the_contract():
    assert "action_cancelled" in EVENT_TYPES


def test_both_renderers_know_the_type():
    """An event type with no renderer in `components/transcript.js` is silently DROPPED by its
    `add()`, so the console shows nothing at all; `cli_render` falls back to a bare type line.
    Both are checked here against their own source, because this type's whole purpose is being
    VISIBLE to a reader afterwards.
    """
    js = (STATIC / "components" / "transcript.js").read_text(encoding="utf-8")
    assert "action_cancelled:" in js, "no renderer in the console's SIMPLE map → dropped by add()"
    css = (STATIC / "views.css").read_text(encoding="utf-8")
    assert ".ev.cancelled" in css, "the renderer's class has no style"
    assert "action_cancelled" in (SRC / "cli_render.py").read_text(encoding="utf-8")


# -- through the REAL Transcript, not a fake ----------------------------------------------

def test_the_real_transcript_accepts_and_round_trips_the_event(tmp_path):
    """The ladder's defect in one test: with the type absent from `EVENT_TYPES` this raises
    inside `event()` instead of writing, and a caller that swallows exceptions then records
    nothing while looking fine.
    """
    path = tmp_path / "transcript.jsonl"
    transcript = Transcript(path)
    transcript.event("action_cancelled",
                     {"kind": "util", "brief": "slowpoke", "exit": 125}, turn=12)
    transcript.close()

    written, _offset = read_events(path)       # (events, new_offset), never a bare list
    events = [e for e in written if e["type"] == "action_cancelled"]
    assert len(events) == 1
    event = events[0]
    assert event["turn"] == 12
    assert event["payload"]["kind"] == "util"
    assert event["payload"]["brief"] == "slowpoke"
    assert event["payload"]["exit"] == 125
    # and it is one JSON line on disk, like every other event
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert any(line.get("type") == "action_cancelled" for line in lines)


def test_the_cli_renders_a_real_line_not_a_bare_type(tmp_path):
    """`_render_event` falls back to `· <type>`, which is technically visible and tells a reader
    nothing. `tests/test_cli.py` requires a real line for every type; this pins what it says.
    """
    line = _render_event({"type": "action_cancelled", "turn": 7,
                        "payload": {"kind": "shell", "brief": "sleep 600", "exit": 125}})
    assert "cancelled by the user" in line
    assert "shell" in line
    assert "sleep 600" in line
    assert line.strip() != "· action_cancelled"


def test_the_record_survives_a_payload_with_nothing_in_it(tmp_path):
    """A cancel of an action whose brief is empty must still leave the trace: the point of the
    record is that the INTERVENTION happened, not what the call was.
    """
    path = tmp_path / "transcript.jsonl"
    transcript = Transcript(path)
    transcript.event("action_cancelled", {"kind": "util"}, turn=1)
    transcript.close()
    written, _offset = read_events(path)
    assert [e for e in written if e["type"] == "action_cancelled"]
    line = _render_event({"type": "action_cancelled", "turn": 1, "payload": {"kind": "util"}})
    assert "cancelled by the user" in line


# -- the loop writes it, and only for a cancelled call ------------------------------------

def test_the_loop_writes_the_event_off_the_observations_flag():
    """Derived from `loop.py`'s source rather than by driving a whole run: the event must be
    written in `_observe`, guarded by the observation's own `cancelled` flag, so it appears for
    a cancel and for nothing else — an aborted call must not produce one (the run is ending
    anyway and `aborted` already says so).
    """
    source = (SRC / "engine" / "loop.py").read_text(encoding="utf-8")
    assert 'obs.get("cancelled")' in source
    assert 'event("action_cancelled"' in source
    # the guard and the write belong together: the write must sit inside the flag's branch
    guard = source.index('obs.get("cancelled")')
    write = source.index('event("action_cancelled"')
    assert 0 < write - guard < 800, "the write is not inside the cancelled branch"
