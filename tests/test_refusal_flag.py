"""The operator's REFUSAL FLAG on a conversation reply (⚑, operator decision 2026-10-01): the
reply and everything after it are archived, the message that produced it is re-sent verbatim,
and the conversation's uncensored model becomes its main model — while a flag the server
refuses leaves the conversation exactly as it was.

Three layers: what opened a reply and where the cut goes (`rewind.reply_opening`, the
`rewind_transcript(before_reply=…)` cut), the route (GET says what a flag would do, POST does
it), and the engine recording the flag as the `operator` seam's `refusal` event when it
delivers the re-sent message.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN, finish, make_test_server, write_file
from rsched import conversations as conv_mod
from rsched.config import ModelConfig
from rsched.engine.rewind import reply_opening, rewind_transcript
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json, read_json
from rsched.web.app import create_app
from test_loop import _server

SEED = Path(__file__).resolve().parents[1] / "library-seed"
TS = "20261001-090000"
REPLY1_TS = "2026-10-01T09:01:00+00:00"
REPLY2_TS = "2026-10-01T09:05:00+00:00"
ASK = "Now make the letter angrier.\n\n[attached files — see]\n- attachments/draft.png"
# a refusal the automatic detector lets through (no marker opens it, and an ok finish is judged
# by the markers alone) — the reply an operator flags by hand
REFUSED = "That letter would read as harassment, so I would rather not write it."
RESUME_NOTE = {"type": "user_injection",
               "payload": {"text": "this run already ENDED (status ok) — …", "source": "engine",
                           "replay": False}}


def _turn(n: int, kind: str = "read_file", **payload) -> dict:
    return {"ts": f"t{n}", "type": "assistant_action", "turn": n,
            "usage": {"in": 9, "out": 1, "model": "dummy/m"},
            "payload": {"kind": kind, "say": "s", **payload}}


def _obs(n: int) -> dict:
    return {"ts": f"t{n}", "type": "observation", "turn": n,
            "payload": {"kind": "read_file", "content": "PLAN"}}


def _reply(turns: int, ts: str, summary: str) -> dict:
    return {"ts": ts, "type": "finish", "turns": turns,
            "payload": {"status": "ok", "summary": summary, "authored": True}}


def _said(text: str, **extra) -> dict:
    return {"ts": "t", "type": "user_injection", "payload": {"text": text, **extra}}


HEADER = {"type": "header", "run_id": f"c-flag:{TS}", "routine": "c-flag", "depth": 0}
# two replies: the first answers the conversation's first message (instruction.md), the
# second is the refusal — opened by ASK, delivered after the resume framing note
TWO_REPLIES = [
    HEADER,
    _turn(1, path="state/plan.md"), _obs(1),
    _turn(2, "finish", status="ok", summary="Here is the first draft."),
    _reply(2, REPLY1_TS, "Here is the first draft."),
    RESUME_NOTE,
    _said(ASK, attachments=["attachments/draft.png"]),
    _turn(3, "finish", status="ok", summary=REFUSED),
    _reply(3, REPLY2_TS, REFUSED),
]


# ---- what opened a reply, and where the cut goes ---------------------------------------------

def test_a_reply_is_read_back_to_the_message_that_opened_it():
    opening = reply_opening(TWO_REPLIES, 3, REPLY2_TS)
    assert opening is not None and not opening["first"]
    assert [e["payload"]["text"] for e in opening["messages"]] == [ASK]
    # everything through the previous reply's finish stays; the resume note its leg's boot
    # wrote goes with the message, or the next boot would stand a second one beside it
    assert opening["keep"] == TWO_REPLIES.index(RESUME_NOTE)
    assert opening["served_by"] == "dummy/m"
    assert reply_opening(TWO_REPLIES, 3, "another ts") is None     # the address is exact


def test_the_first_reply_is_cut_back_to_the_header():
    opening = reply_opening(TWO_REPLIES, 2, REPLY1_TS)
    assert opening is not None and opening["first"] and opening["messages"] == []
    assert opening["keep"] == 1                                     # the header alone


def test_commands_before_the_message_stay_and_a_mid_reply_message_opened_nothing():
    events = [*TWO_REPLIES[:6],
              _said("/read_file notes.md", command=True),
              {"ts": "t", "type": "observation", "payload": {"kind": "read_file",
                                                            "user_command": True}},
              _said("Summarise it."),
              _turn(3), _obs(3),
              _said("and keep it short"),                         # injected mid-reply
              _turn(4, "finish", status="ok", summary=REFUSED),
              _reply(4, REPLY2_TS, REFUSED)]
    opening = reply_opening(events, 4, REPLY2_TS)
    assert opening is not None
    assert [e["payload"]["text"] for e in opening["messages"]] == ["Summarise it."]
    assert opening["keep"] == 8          # the slash command and its result are kept


def test_a_reply_that_ran_no_turn_is_told_apart_by_its_own_timestamp():
    """A classifier refusal that exhausted the chain finishes WITHOUT a turn of its own, on
    the counter of the reply before it — exactly the reply the flag exists for."""
    events = [*TWO_REPLIES[:6], _said("Do it anyway."),
              {"ts": "t", "type": "error", "payload": {"where": "endpoint", "message": "x"}},
              _reply(2, REPLY2_TS, "Endpoint failure: m refused the turn")]
    opening = reply_opening(events, 2, REPLY2_TS)
    assert opening is not None
    assert [e["payload"]["text"] for e in opening["messages"]] == ["Do it anyway."]
    assert opening["keep"] == 5 and opening["served_by"] == ""
    assert reply_opening(events, 2, REPLY1_TS)["first"] is True    # the earlier one


def test_a_branch_reply_starts_after_its_inherited_history():
    """A branch's transcript is its parent's history up to the fork turn, with no finish
    after it (F325): its own first reply is opened by the branch's message, not the
    parent's."""
    events = [{**HEADER, "branched_from": {"slug": "c-parent", "turn": 1}},
              _said("parent's ask"), _turn(1), _obs(1),
              RESUME_NOTE, _said("the branch's own ask"),
              _turn(2, "finish", status="ok", summary=REFUSED), _reply(2, REPLY2_TS, REFUSED)]
    opening = reply_opening(events, 2, REPLY2_TS)
    assert opening is not None and not opening["first"]
    assert [e["payload"]["text"] for e in opening["messages"]] == ["the branch's own ask"]
    assert opening["keep"] == 4


def test_a_reply_no_message_opened_has_no_cut():
    events = [*TWO_REPLIES[:6], _turn(3), _obs(3),
              _turn(4, "finish", status="ok", summary="carried on"), _reply(4, REPLY2_TS, "x")]
    assert reply_opening(events, 4, REPLY2_TS)["keep"] is None


def test_the_cut_before_a_reply_archives_the_tail(tmp_path):
    run_dir = tmp_path / "runs" / TS
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in TWO_REPLIES), encoding="utf-8")
    (run_dir / "result.md").write_text(REFUSED + "\n", encoding="utf-8")
    info = rewind_transcript(run_dir, before_reply=(3, REPLY2_TS))
    assert info is not None and info["kept_through_turn"] == 2 and info["dropped_events"] == 4
    kept, _ = read_events(run_dir / "transcript.jsonl")
    assert kept == TWO_REPLIES[:5]                      # through the previous reply's finish
    archived = (run_dir / info["archive"]).read_text(encoding="utf-8")
    assert REFUSED in archived and "angrier" in archived  # moved aside, never destroyed
    # result.md is what the next leg's digest reads as the last result: the kept reply, now
    assert (run_dir / "result.md").read_text(encoding="utf-8") == "Here is the first draft.\n"
    assert rewind_transcript(run_dir, before_reply=(3, REPLY2_TS)) is None   # it is gone
    assert rewind_transcript(run_dir, before_reply=(2, REPLY1_TS)) is not None
    assert not (run_dir / "result.md").exists()           # no reply is left at all


# ---- the route --------------------------------------------------------------------------------

@pytest.fixture
def flagged(tmp_path):
    """A finished two-reply conversation with an uncensored model configured, behind a live
    app whose runner records resumes instead of spawning an engine."""
    lib = tmp_path / "library"
    for tree in ("workflows", "rules", "permissions"):
        shutil.copytree(SEED / tree, lib / tree)
    server = make_test_server(
        tmp_path, conversations_home=str(tmp_path / "conversations"), libraries_home=str(lib),
        models={"m": {"endpoint": "dummy", "model": "m"},
                "unc": {"endpoint": "dummy", "model": "unc"},
                "other": {"endpoint": "dummy", "model": "other"}})
    conv_dir = conv_mod.create_conversation(
        server, slug="c-flag", first_message="Draft the letter."
        + conv_mod.attachment_note(["attachments/old.pdf"]))
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    raw["models"] = {"main": "m", "tool_call": "m", "uncensored": "unc"}
    (conv_dir / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    run_dir = conv_dir / "runs" / TS
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in TWO_REPLIES), encoding="utf-8")
    atomic_write_json(run_dir / "status.json",
                      {"run_id": f"c-flag:{TS}", "state": "finished", "turn": 3})
    app = create_app(server, with_scheduler=False)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        resumed: list[tuple[str, str, str]] = []

        async def fake_resume(cfg, ts, *, reason="resume"):
            resumed.append((cfg.slug, ts, reason))
            return f"{cfg.slug}:{ts}"

        c.app.state.runner.resume = fake_resume
        yield c, server, conv_dir, run_dir, resumed


URL = f"/api/runs/c-flag:{TS}/flag-refusal"


def _state(conv_dir: Path, run_dir: Path) -> tuple:
    """Everything a flag writes, so a refused one can be shown to have written nothing."""
    return ((run_dir / "transcript.jsonl").read_bytes(),
            (conv_dir / "routine.yaml").read_bytes(),
            sorted(p.name for p in (conv_dir / "inbox").iterdir()),
            sorted(p.name for p in run_dir.iterdir()))


def test_the_gate_reads_what_a_flag_would_do(flagged):
    c, _, _, _, _ = flagged
    r = c.get(URL, params={"turn": 3, "ts": REPLY2_TS})
    assert r.status_code == 200, r.text
    assert r.json() == {"turn": 3, "ts": REPLY2_TS, "message": ASK, "refused_by": "m",
                        "model": "unc"}


def test_a_flag_redoes_the_reply_on_the_uncensored_model(flagged):
    c, _, conv_dir, run_dir, resumed = flagged
    r = c.post(URL, json={"turn": 3, "ts": REPLY2_TS})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == "unc" and body["kept_through_turn"] == 2
    # the transcript ends where the previous reply ended; the rest is archived beside it
    kept, _ = read_events(run_dir / "transcript.jsonl")
    assert kept == TWO_REPLIES[:5]
    assert REFUSED in (run_dir / body["archive"]).read_text(encoding="utf-8")
    # the uncensored model carries the conversation from here; the other roles are untouched
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    assert raw["models"] == {"main": "unc", "tool_call": "m", "uncensored": "unc"}
    # the message is re-sent verbatim, on the composer's own channel, carrying the flag
    (msg,) = (conv_dir / "inbox").glob("msg-*.json")
    rec = read_json(msg)
    assert rec["text"] == ASK and rec["via"] == "conversation"
    assert rec["attachments"] == ["attachments/draft.png"]
    assert rec["refusal_flag"] == {"turn": 3, "model": "m", "takeover_model": "unc",
                                   "message": REFUSED, "archive": body["archive"]}
    assert resumed == [("c-flag", TS, "refusal-flag")]


def test_flagging_the_first_reply_resends_the_first_message(flagged):
    c, _, conv_dir, run_dir, _ = flagged
    r = c.post(URL, json={"turn": 2, "ts": REPLY1_TS})
    assert r.status_code == 200, r.text
    kept, _ = read_events(run_dir / "transcript.jsonl")
    assert kept == [HEADER]                          # the very start of the conversation
    (msg,) = (conv_dir / "inbox").glob("msg-*.json")
    rec = read_json(msg)
    first = (conv_dir / "instruction.md").read_text(encoding="utf-8").rstrip()
    assert rec["text"] == first and rec["text"].startswith("Draft the letter.")
    assert rec["attachments"] == ["attachments/old.pdf"]   # read back from its block


def test_with_no_uncensored_model_the_pick_becomes_uncensored_and_main(flagged):
    c, _, conv_dir, _, _ = flagged
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    raw["models"] = {"main": "m", "tool_call": "m"}
    (conv_dir / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert c.get(URL, params={"turn": 3, "ts": REPLY2_TS}).json()["model"] is None
    r = c.post(URL, json={"turn": 3, "ts": REPLY2_TS, "model": "other"})
    assert r.status_code == 200, r.text
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    assert raw["models"] == {"main": "other", "tool_call": "m", "uncensored": "other"}


def test_a_switch_left_pending_by_the_reply_cannot_undo_the_takeover(flagged):
    c, _, _, run_dir, _ = flagged
    atomic_write_json(run_dir / "control.json",
                      {"switch_model": {"main": "m", "ts": "late"}, "pause": False})
    assert c.post(URL, json={"turn": 3, "ts": REPLY2_TS}).status_code == 200
    ctrl = read_json(run_dir / "control.json")
    assert ctrl["switch_model"] is None and ctrl["pause"] is False


def _no_uncensored(conv_dir: Path) -> None:
    raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
    raw["models"] = {"main": "m"}
    (conv_dir / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")


def _tiny(server) -> None:
    server.models["tiny"] = ModelConfig(name="tiny", endpoint="dummy", model="t",
                                        context_tokens=16_384, max_tokens=16_384)


@pytest.mark.parametrize(("setup", "body", "status", "says"), [
    (None, {"turn": 3, "ts": "not this reply"}, 404, "no reply at turn 3"),
    (None, {"turn": 9, "ts": REPLY2_TS}, 404, "no reply at turn 9"),
    (None, {"turn": 3, "ts": REPLY2_TS, "model": "other"}, 400, "uncensored model is 'unc'"),
    ("no-uncensored", {"turn": 3, "ts": REPLY2_TS}, 400, "pick the model"),
    ("no-uncensored", {"turn": 3, "ts": REPLY2_TS, "model": "ghost"}, 400, "catalog model"),
    ("tiny", {"turn": 3, "ts": REPLY2_TS, "model": "tiny"}, 400, "cannot run a single turn"),
    ("stale-uncensored", {"turn": 3, "ts": REPLY2_TS}, 400, "models.uncensored"),
    ("mid-reply", {"turn": 3, "ts": REPLY2_TS}, 409, "mid-reply"),
    ("busy", {"turn": 3, "ts": REPLY2_TS}, 409, "nothing was changed"),
])
def test_a_refused_flag_leaves_the_conversation_untouched(flagged, setup, body, status, says):
    c, server, conv_dir, run_dir, resumed = flagged
    if setup in ("no-uncensored", "tiny"):
        _no_uncensored(conv_dir)
        if setup == "tiny":
            _tiny(server)
    elif setup == "stale-uncensored":
        raw = yaml.safe_load((conv_dir / "routine.yaml").read_text())
        raw["models"]["uncensored"] = "retired"
        (conv_dir / "routine.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    elif setup == "mid-reply":
        atomic_write_json(run_dir / "status.json", {"state": "running", "turn": 3})
    elif setup == "busy":
        c.app.state.runner.draining = True
    before = _state(conv_dir, run_dir)
    r = c.post(URL, json=body)
    assert r.status_code == status, r.text
    assert says in r.json()["detail"]
    assert _state(conv_dir, run_dir) == before and resumed == []


def test_a_reply_no_message_opened_is_refused_untouched(flagged):
    c, _, conv_dir, run_dir, resumed = flagged
    events = [*TWO_REPLIES[:6], _turn(3), _obs(3),
              _turn(4, "finish", status="ok", summary=REFUSED), _reply(4, REPLY2_TS, REFUSED)]
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    before = _state(conv_dir, run_dir)
    r = c.post(URL, json={"turn": 4, "ts": REPLY2_TS})
    assert r.status_code == 400 and "nothing to re-send" in r.json()["detail"]
    assert _state(conv_dir, run_dir) == before and resumed == []


def test_only_a_conversation_reply_can_be_flagged(flagged, make_routine):
    c, _, _, _, resumed = flagged
    run_dir = make_routine(slug="plainr") / "runs" / TS   # under the routines home
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in TWO_REPLIES), encoding="utf-8")
    atomic_write_json(run_dir / "status.json", {"state": "finished", "turn": 3})
    r = c.post(f"/api/runs/plainr:{TS}/flag-refusal", json={"turn": 3, "ts": REPLY2_TS})
    assert r.status_code == 400 and "conversation only" in r.json()["detail"]
    assert len(read_events(run_dir / "transcript.jsonl")[0]) == len(TWO_REPLIES)
    assert resumed == []


# ---- the engine records the flag where it happened ---------------------------------------------

def test_the_engine_records_the_flag_before_the_re_sent_message(make_routine, scripted):
    """The engine is the transcript's only writer: the flag rides the re-sent message, and the
    delivering leg writes it as a `refusal` event of the `operator` seam right before the
    message — a record for the reader that the model never reads."""
    from rsched.engine import inbox

    d = make_routine(slug="flagr")
    scripted([write_file("state/plan.md", content="plan"), finish(summary="first draft")])
    run_routine(d, _server(d), run_ts=TS)
    inbox.file_message(d, "make it angrier", via="conversation")
    scripted([finish(summary=REFUSED)])
    run_routine(d, _server(d), run_ts=TS, resume_from=TS)
    run_dir = d / "runs" / TS
    events, _ = read_events(run_dir / "transcript.jsonl")
    flagged_reply = [e for e in events if e["type"] == "finish"][-1]

    cut = rewind_transcript(run_dir, before_reply=(flagged_reply["turns"],
                                                   flagged_reply["ts"]))
    assert cut is not None
    flag = {"turn": flagged_reply["turns"], "model": "scripted/test-model",
            "takeover_model": "unc", "message": REFUSED, "archive": cut["archive"]}
    inbox.file_message(d, "make it angrier", via="conversation",
                       extra={"refusal_flag": flag})
    ep = scripted([finish(summary="Here is the angrier letter.")])
    status, _ = run_routine(d, _server(d), run_ts=TS, resume_from=TS)
    assert status == "ok"

    events, _ = read_events(run_dir / "transcript.jsonl")
    at = next(i for i, e in enumerate(events) if e["type"] == "refusal")
    assert events[at]["payload"] == {"where": "operator", **flag}
    assert events[at + 1]["type"] == "user_injection"
    assert events[at + 1]["payload"]["text"] == "make it angrier"
    assert [e["type"] for e in events].count("refusal") == 1
    # the model reads the message, not the record — and nothing of the discarded reply
    prompt = ep.calls[0]["messages"]
    assert any(m["role"] == "user" and m["content"].endswith("make it angrier")
               for m in prompt[1:])
    assert not any(REFUSED in str(m["content"]) for m in prompt)
