"""The inbox's two invariants that nothing else pins: ONE writer of the `msg-*` shape, and
what a leg that may not consume its freight is told about it.

`engine/inbox.file_message` has always called itself the one writer; seven modules wrote the
filename themselves anyway, one of them character-for-character. The other half is the read
side: every scanner selected "any file that is not `answer-*`", which also matches
`atomic_write`'s in-flight temp file.
"""
from __future__ import annotations

from pathlib import Path

from rsched.engine import inbox
from rsched.engine.inbox import LIVE_MESSAGE_VIAS


def _routine(tmp_path: Path) -> Path:
    d = tmp_path / "r"
    (d / "inbox").mkdir(parents=True)
    return d


def test_the_one_writer_takes_a_deterministic_name_for_the_channels_that_need_one(tmp_path):
    """A report delivery and a background result read their own pending message back BY NAME
    (retract, re-deliver), so their filename is an idempotency key. That is the whole reason
    six modules hand-rolled the write — `file_message` could only produce a timestamp — so the
    single writer has to be able to say it, or it stays a single writer nobody uses.
    """
    d = _routine(tmp_path)
    first = inbox.file_message(d, "hello", via="report", name="rep-R7",
                               extra={"report": "R7", "from": "sender"})
    assert first.name == "msg-rep-R7.json"
    # filing it again REPLACES it — that is what a key buys; two timestamped files would not
    again = inbox.file_message(d, "hello again", via="report", name="rep-R7",
                               extra={"report": "R7", "from": "sender"})
    assert again == first
    assert len(list((d / "inbox").glob("msg-*.json"))) == 1
    # the default stays unique: now_iso() is second-resolution, so two submissions in one
    # second must not clobber each other
    a = inbox.file_message(d, "one", via="web")
    b = inbox.file_message(d, "two", via="web")
    assert a != b


def test_a_half_written_message_is_neither_drained_nor_counted(tmp_path):
    """`paths.atomic_write` creates its temp file IN the target directory, so a
    `.msg-….json.XXXX.tmp` sits in the inbox for the instant before the rename. The old
    scanners selected every file that was not `answer-*`: a fresh boot's drain reached that
    temp file, could not parse it, logged "not a message file" and RENAMED it into consumed/
    — so the writer's own `replace()` failed and the message was lost. Every scanner now
    names the stem the one writer produces.
    """
    d = _routine(tmp_path)
    inbox.file_message(d, "a real message", via="web")
    (d / "inbox" / ".msg-2026-09-22T120000.json.ab12.tmp").write_text("{partial",
                                                                      encoding="utf-8")
    drained = inbox.drain_messages(d, tmp_path / "consumed")
    assert [m["text"] for m in drained] == ["a real message"]
    # the temp file is untouched — still there for its writer to rename over
    assert (d / "inbox" / ".msg-2026-09-22T120000.json.ab12.tmp").exists()
    assert not inbox.has_pending_messages(d)


def test_a_leg_that_may_not_consume_its_freight_is_told_what_is_waiting(tmp_path):
    """F529. A resumed leg drains LIVE_MESSAGE_VIAS only — an audit decision answer, a
    sibling's report delivery and a routine-page queued message belong to the routine's next
    FRESH run (D92/D93). That exclusion is right; the silence was not. On 2026-09-21 a
    decision answered at 16:01 sat in the inbox while three continuation legs explained it as
    still open, until the operator asked whether his answer had been lost again.

    `queued_freight` is the read half: it names what is waiting and consumes nothing.
    """
    d = _routine(tmp_path)
    inbox.file_message(d, "[AUDIT decision · D141] selected: option B\nand a second line",
                       via="web-audit", extra={"kind": "decision", "target": "D141"})
    inbox.file_message(d, "R1815 from sibling: the util needs a flag", via="report",
                       name="rep-R1815", extra={"report": "R1815", "from": "sibling"})
    inbox.file_message(d, "talking to THIS run", via="web")

    freight = inbox.queued_freight(d, exclude_vias=LIVE_MESSAGE_VIAS)
    assert [f["via"] for f in freight] == ["web-audit", "report"]
    # the first line only: enough to recognise, not enough to act on without opening the file
    assert freight[0]["text"] == "[AUDIT decision · D141] selected: option B"
    assert freight[1]["report"] == "R1815" and freight[1]["from"] == "sibling"
    # …and nothing was consumed: the next FRESH run still gets all three
    assert len(list((d / "inbox").glob("msg-*.json"))) == 3
    assert len(inbox.drain_messages(d, tmp_path / "consumed")) == 3
    # a fresh boot drains everything, so after it there is no freight to report
    assert inbox.queued_freight(d, exclude_vias=LIVE_MESSAGE_VIAS) == []
