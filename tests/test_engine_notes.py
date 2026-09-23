"""ENGINE NOTES — one seam writes them, one renderer replays them.

The defect: every engine-authored message was recorded as a `user_injection` carrying a short
STUB ("[engine] setup gaps at boot") while the model read the full prose, and
`history.replay_messages` rendered every `user_injection` as "USER MESSAGE (injected mid-run)"
with no test of `source`. A resumed conversation therefore read ~380 phantom user messages in
21 days — mislabelled (the harness contract says an injected message IS the user talking) and
lossy (the gap list, the compaction warning and the archive pointer were gone), and a second
resume replayed the stub where the previous leg carried the prose, so the prompt prefix
differed between legs and the provider cache was re-written instead of re-read.
"""

from types import SimpleNamespace

from rsched.engine import enginenote
from rsched.engine.history import replay_messages


def _loop():
    events = []
    return SimpleNamespace(
        messages=[],
        ctx=SimpleNamespace(transcript=SimpleNamespace(
            event=lambda kind, payload: events.append({"type": kind, "payload": payload}))),
    ), events


def test_a_note_is_recorded_verbatim_and_replays_as_an_engine_note():
    loop, events = _loop()
    enginenote.append(loop, "the middle of this conversation is about to be ARCHIVED —\n"
                            "you have this turn to externalize what matters.")

    # what the model read and what the transcript kept are ONE string
    assert events[0]["payload"]["source"] == "engine"
    assert events[0]["payload"]["text"] in loop.messages[0]["content"]
    assert loop.messages[0]["content"].startswith("ENGINE NOTE: ")

    replayed, _turn, _records = replay_messages(events)
    assert replayed == loop.messages          # byte-identical on resume


def test_a_note_the_resume_re_authors_is_not_replayed():
    """`replay=False` marks the notes `boot` writes afresh on every leg — the resume framing
    and the setup-gap list. Replaying them too stacked one copy per leg: a 40-reply
    conversation carried 40 identical "[engine] setup gaps at boot" messages.
    """
    loop, events = _loop()
    enginenote.append(loop, "this run was interrupted and is now RESUMED.", replay=False)
    enginenote.append(loop, "the user bound the general rule 'x' to this routine")

    assert len(loop.messages) == 2            # the live leg reads both
    replayed, _turn, _records = replay_messages(events)
    assert len(replayed) == 1
    assert "general rule" in replayed[0]["content"]


def test_a_real_user_message_still_replays_as_a_user_message():
    events = [
        {"type": "user_injection", "payload": {"text": "did you send the ping?"}},
        {"type": "user_injection", "payload": {"text": "REPORT R7 from routine x: look at y",
                                               "report": True}},
    ]
    replayed, _turn, _records = replay_messages(events)
    assert replayed[0]["content"] == "USER MESSAGE (injected mid-run):\ndid you send the ping?"
    # a delivered report carries its own sender heading — calling it a USER MESSAGE would
    # name the wrong sender
    assert replayed[1]["content"].startswith("REPORT (injected mid-run):\n")


def test_a_slash_command_replays_as_the_one_message_it_was_live():
    """Live, a user command is ONE message: the command plus its result. It is RECORDED as an
    injection plus an observation, and the replay used to emit two differently-worded
    messages — a prefix that cannot match the leg that wrote it.
    """
    from rsched.engine.control import command_message

    obs = {"kind": "user_command", "error": "no such util"}
    events = [
        {"type": "user_injection", "payload": {"text": "/util name=ghost", "command": True}},
        {"type": "observation", "payload": {**obs, "user_command": True}},
    ]
    replayed, _turn, _records = replay_messages(events)
    assert len(replayed) == 1
    assert replayed[0]["content"] == command_message("/util name=ghost",
                                                     {**obs, "user_command": True})
