"""A push holds back while the operator is AT the console — D280.

Tier 2 (Web Push) exists for an operator who is AWAY. While he is here the ask is already on
his Decisions page and on the header badge, so pushing his phone for it is the noise that
trains a person to ignore the channel.

DEFERRED, never dropped — which is the half an implementation gets wrong, because the obvious
shortcut (mark it notified, skip the send) loses the notification forever. So the three cases
the order names are pinned here: no push while present, exactly one push once the window
lapses, and none at all if he settled it meanwhile. Plus the two a reader would ask about: a
WITHDRAWAL is never held (it clears an alert already on his phone), and every unreadable
presence shape reads as ABSENT, because the conservative direction is pushing.

Its own module rather than more lines in `test_push.py`: that file was 277 lines and these
tests took it over the 350-line house bar, and presence is a subject of its own.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from helpers import tmp_server
from rsched.config import ServerConfig
from rsched.engine import inbox
from rsched.paths import atomic_write_json
from rsched.web import push

SUB_A = {"endpoint": "https://push.example/a", "keys": {"p256dh": "x", "auth": "y"}}


def _server(tmp_path) -> ServerConfig:
    # push state lands next to the config, exactly as tests/test_push.py builds it
    return tmp_server(tmp_path, create=False, source=tmp_path / "config.yaml",
                      libraries_home=tmp_path / "library")


def _answer(routine_dir: Path, qid: str) -> None:
    """Settle a decision the way the console does — an `inbox/answer-<qid>.json` file IS the
    durable answered marker (`decisions_read`'s own principle: question state is derived,
    never stored twice).
    """
    (routine_dir / "inbox").mkdir(parents=True, exist_ok=True)
    (routine_dir / "inbox" / f"answer-{qid}.json").write_text(json.dumps({"text": "yes"}),
                                                              encoding="utf-8")


def _catch(monkeypatch) -> list[dict]:
    """Every payload the sender would have put on the wire. No network, like test_push.py."""
    sent: list[dict] = []
    monkeypatch.setattr(push, "_send_one",
                        lambda _srv, _sub, payload: sent.append(payload) or True)
    return sent


def test_no_push_while_he_is_at_the_console(make_routine, tmp_path, monkeypatch):
    server = _server(tmp_path)
    d = make_routine(slug="asker")
    inbox.file_question(d, "q-present", "Ship it?", ["yes", "no"], "20260712-070000")
    push.add_subscription(server, SUB_A)
    sent = _catch(monkeypatch)
    push.note_presence(server)

    assert push.notify_new_decisions(server) == 0
    assert sent == []


def test_one_push_once_the_presence_window_lapses(make_routine, tmp_path, monkeypatch):
    """The ask is HELD, not lost: it stays out of the notified set while he is here, so the
    first pass after the window pushes it exactly once — and not again after that.
    """
    server = _server(tmp_path)
    d = make_routine(slug="asker")
    inbox.file_question(d, "q-lapse", "Ship it?", ["yes", "no"], "20260712-070000")
    push.add_subscription(server, SUB_A)
    sent = _catch(monkeypatch)

    push.note_presence(server, now=time.time() - push.PRESENCE_S - 1)   # he left

    assert push.notify_new_decisions(server) == 1
    assert sent[0]["tag"] == "rsched-q-lapse"
    assert push.notify_new_decisions(server) == 0, "exactly once, like any other ask"


def test_nothing_is_pushed_if_he_settled_it_while_he_was_here(make_routine, tmp_path,
                                                              monkeypatch):
    """The case the deferral is FOR: he answers it at the console during the hold, so the push
    that was held is never sent — and no withdrawal either, because nothing reached his phone.
    """
    server = _server(tmp_path)
    d = make_routine(slug="asker")
    inbox.file_question(d, "q-settled", "Ship it?", ["yes", "no"], "20260712-070000")
    push.add_subscription(server, SUB_A)
    sent = _catch(monkeypatch)
    push.note_presence(server)
    assert push.notify_new_decisions(server) == 0        # held while he is here

    _answer(d, "q-settled")
    push.note_presence(server, now=time.time() - push.PRESENCE_S - 1)   # then he leaves

    assert push.notify_new_decisions(server) == 0
    assert sent == []


def test_a_withdrawal_is_never_held_by_presence(make_routine, tmp_path, monkeypatch):
    """A retraction clears a notification already ON his phone. Holding that back would leave
    a stale alert for an ask he has answered — the same defect from the other side.
    """
    server = _server(tmp_path)
    d = make_routine(slug="asker")
    inbox.file_question(d, "q-away", "Ship it?", ["yes", "no"], "20260712-070000")
    push.add_subscription(server, SUB_A)
    sent = _catch(monkeypatch)
    assert push.notify_new_decisions(server) == 1        # pushed while he was away
    sent.clear()

    push.note_presence(server)                           # now he is at the console
    _answer(d, "q-away")

    assert push.notify_new_decisions(server) == 1
    assert sent[0] == {"tag": "rsched-q-away", "close": True}


def test_presence_is_unreadable_as_absence_not_as_presence(tmp_path):
    """Every unreadable shape reads as ABSENT, because the conservative direction is PUSHING:
    a notification he did not need costs a glance, one that never came costs a decision
    sitting unanswered for a day. A stamp from the FUTURE (a clock that jumped forward) is one
    of those shapes — otherwise it would silence every push until the clock caught up.
    """
    server = _server(tmp_path)
    assert push.at_console(server) is False              # no file at all

    for shape in ({}, {"at": None}, {"at": "soon"}, {"at": time.time() + 10_000}):
        atomic_write_json(push.push_dir(server) / "push-presence.json", shape)
        assert push.at_console(server) is False, shape

    push.note_presence(server)
    assert push.at_console(server) is True


def test_the_console_beats_only_in_the_foreground():
    """`document.hidden` is true for a background tab, and a browser left open on another
    desktop is not a person looking at this page. Only the client knows that, which is why
    this is a POST of its own rather than a flag on the `/api/status` poll every reader makes.
    """
    notify_js = (Path(__file__).resolve().parents[1] / "static" / "notify.js").read_text(
        encoding="utf-8")

    assert "/api/push/present" in notify_js
    assert "if (document.hidden) return;" in notify_js
    assert "visibilitychange" in notify_js
