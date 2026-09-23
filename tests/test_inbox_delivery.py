"""`via` is the delivery-policy switch, and one predicate answers "is something waiting".

Two contracts, one file, because they are two halves of the same thing: what a channel MEANS
(when the message is consumed, whether a person wrote it, whether the reap may resume a
finished run for it) and who gets to ask about it.

The first was chosen freehand at every writer, which is how a branch hand-back came to be
filed on the USER channel — read by the parent as the operator speaking, offered in the
composer as the operator's own editable text, and swept by the reap as a reason to resume a
conversation the hand-back's own contract says it never wakes.

The second was five hand-rolled loops with three different filters, where the differences
that mattered — a fail-OPEN scanner beside four fail-CLOSED ones — were undocumented and
therefore unrepeatable.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from rsched.engine import inbox

SRC = Path(__file__).resolve().parents[1] / "src" / "rsched"

#: `via` is also the word two NON-inbox records use for "how this arrived". They are listed
#: here rather than excluded by filename so that a third one has to be looked at: if it is an
#: inbox message, it belongs in `inbox.VIAS`, and a writer choosing a channel the closed set
#: never heard of is the failure this pins.
_NOT_INBOX_CHANNELS = {
    "vision-util",   # engine/mediaops — how an image was described, not where it came from
    "web-settings",  # web/settings/restart — the restart sentinel's provenance
}

_VIA_LITERAL = re.compile(r'(?:"via":\s*|via=)"([a-z0-9_-]*)"')


def _routine(tmp_path: Path, name: str = "r") -> Path:
    d = tmp_path / name
    (d / "inbox").mkdir(parents=True)
    return d


def test_the_closed_set_holds_every_via_the_tree_actually_writes():
    """The trap in closing this set: seeding it from the two named tuples would have left
    `pending`, `trigger`, `schedule_once`, `report` and `web-audit` out — five legitimate
    writers, one of them on the daemon's own tick. The set is seeded from what the code
    WRITES, and this is what keeps it that way.
    """
    written = {m for path in SRC.rglob("*.py")
               for m in _VIA_LITERAL.findall(path.read_text(encoding="utf-8"))}
    unknown = written - inbox.VIAS - _NOT_INBOX_CHANNELS
    assert not unknown, (f"{sorted(unknown)} is written as a `via` but is not an inbox "
                         "channel — add it to inbox.VIAS (and decide its delivery policy) "
                         "or to _NOT_INBOX_CHANNELS if it is not an inbox message")


def test_the_one_writer_refuses_a_channel_nobody_chose(tmp_path):
    d = _routine(tmp_path)
    with pytest.raises(ValueError, match="unknown inbox via"):
        inbox.file_message(d, "hello", via="conversaton")     # a typo is a silent policy
    assert list((d / "inbox").glob("msg-*.json")) == []
    # every member of the closed set is accepted, "" (the routine page's queued message)
    # included — the set IS the vocabulary, not a subset of it
    for via in sorted(inbox.VIAS):
        inbox.file_message(d, "ok", via=via, name=f"x-{via or 'none'}")


def test_only_a_person_advances_the_user_reply_count():
    """`user_replies` answers "has the user spoken since the draft" for the create_routine
    confirm gate and the `user-spoke` assist predicate. A report, a background result, a
    branch hand-back, a trigger text and a proposal outcome are machines delivering.
    """
    assert inbox.MACHINE_VIAS < inbox.VIAS
    for via in ("report", "background", "branch", "trigger", "schedule_once", "pending"):
        assert not inbox.user_authored(via)
    for via in ("", "conversation", "web", "web-converse", "web-audit"):
        assert inbox.user_authored(via)


def test_a_live_leg_sees_exactly_what_its_drain_would_consume(tmp_path):
    """The predicate mirrors `drain_messages` EXACTLY. Counting freight the drain will never
    take is what makes a live leg's wait-yield or finish-deferral spin forever on it.
    """
    d = _routine(tmp_path)
    inbox.file_message(d, "for the next fresh run", via="report", name="rep-R1",
                       extra={"report": "R1", "from": "sibling"})
    assert inbox.has_pending_messages(d)                               # something is waiting
    assert not inbox.has_pending_messages(d, vias=inbox.LIVE_MESSAGE_VIAS)   # …not for US
    assert inbox.drain_messages(d, tmp_path / "c", vias=inbox.LIVE_MESSAGE_VIAS) == []

    inbox.file_message(d, "talking to this run", via="web")
    assert inbox.has_pending_messages(d, vias=inbox.LIVE_MESSAGE_VIAS)
    assert inbox.count_pending(d) == 2


def test_the_third_axis_is_what_an_unreadable_file_counts_as(tmp_path):
    """The one difference two flags could not express. The trigger manager is fail-OPEN by
    contract — anything it cannot read WAKES a run, because a spurious run costs a run and a
    missed one costs silence — while every live-run predicate is fail-CLOSED for the reason
    above. Collapsing the five loops without this axis reintroduces the spin.
    """
    d = _routine(tmp_path)
    (d / "inbox" / "msg-corrupt.json").write_text("{not json", encoding="utf-8")
    assert not inbox.has_pending_messages(d)                        # fail-closed: the default
    assert inbox.has_pending_messages(d, on_unparseable=True)       # fail-open: the trigger
    assert inbox.count_pending(d, on_unparseable=True) == 1


def test_a_closure_is_the_end_of_work_not_work(tmp_path):
    """The report trigger's exemption: a reply that only CLOSES a thread must not buy a run.
    It lived in exactly one of the five loops, so any other caller that started counting
    closures would fire runs the trigger manager deliberately ignores.
    """
    d = _routine(tmp_path)
    inbox.file_message(d, "R9 closed: nothing left to do", via="report", name="rep-R9",
                       extra={"report": "R9", "from": "sibling", "closes": True})
    assert inbox.has_pending_messages(d)                                  # it IS waiting
    assert not inbox.has_pending_messages(d, include_closures=False)      # …but it is not work
    inbox.file_message(d, "R10: please look at this", via="report", name="rep-R10",
                       extra={"report": "R10", "from": "sibling"})
    assert inbox.count_pending(d, include_closures=False) == 1


def test_an_answer_is_never_freight_for_anyone(tmp_path):
    """An answer has its own matching pass (`collect_deferred_answers`) and is the one thing
    that must not buy a run — the caller that counted "any file that is not a message" made a
    queued answer read as pending work.
    """
    d = _routine(tmp_path)
    (d / "inbox" / "answer-q-1.json").write_text('{"qid": "q-1", "text": "yes"}',
                                                 encoding="utf-8")
    assert not inbox.has_pending_messages(d)
    assert not inbox.has_pending_messages(d, on_unparseable=True)
    assert inbox.count_pending(d) == 0
