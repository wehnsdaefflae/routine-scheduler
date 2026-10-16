"""The send gate: a message goes out in the operator's name only right after its channel's
outbox was read — and otherwise fails, inside the schema-retry cycle, naming the read to make.

The operator's order (2026-10-02, 2026-10-09): every outbound message is first verified not to
have gone out already, because he may have sent it himself or an earlier run did — for every
channel, including utils nobody has written yet. So the gate keys on his IDENTITY (the
credentials that act as him) rather than on a list of utils.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import finish
from helpers import server_for, set_capabilities
from rsched import utils_lib, utils_run
from rsched.engine import sendgate
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events
from rsched.outbox import as_operator_problems, normalize_outbox, read_library_outbox
from rsched.workflows.lint import lint_permission_text

SEED = Path(__file__).resolve().parents[1] / "library-seed" / "permissions"
TS = "20261009-140000"

MAIL_DOC = """---
effect:
  with: sends email from your own mailboxes in your name
  without: reads and drafts mail, but never sends it anywhere
  when: correspondence is part of the task at hand
tags: [communication, email, outbound, as-operator]
requires:
  utils: [gmail:send]
outbox:
  gmail:
    identity:
      secrets: [GMAIL_APP_PASSWORD]
    utils:
      gmail:
        never_sends: [list, read, draft]
        send_if: '--yes'
    check: '^util:gmail list .*--mailbox sent'
    run: gu gmail list --mailbox sent --query "to:<recipient>"
---
# permission: outbound-mail — send email from the operator's own mailboxes
"""


def _header(name: str, *, secrets: str = "(none)", calls: str = "(none)") -> str:
    return (f'"""{name} — t.\n\nusage: gu {name}\ncalls: {calls}\ntags: t\n'
            f'secrets: {secrets}\nnet: none\nfs: none\n"""\n')


def _loop(tmp_path, *, turn: int = 5):
    routine = tmp_path / "r"
    (routine / "scripts").mkdir(parents=True, exist_ok=True)
    lib = tmp_path / "lib"
    perms = lib / "permissions"
    perms.mkdir(parents=True, exist_ok=True)
    (perms / "outbound-mail.md").write_text(MAIL_DOC, encoding="utf-8")
    for name, secrets, calls in (("gmail", "GMAIL_APP_PASSWORD", "(none)"),
                                 ("mailer", "GMAIL_APP_PASSWORD", "(none)"),
                                 ("digest", "(none)", "gmail"),
                                 ("weather", "(none)", "(none)")):
        (lib / "utils" / name).mkdir(parents=True, exist_ok=True)
        (lib / "utils" / name / "main.py").write_text(_header(name, secrets=secrets,
                                                              calls=calls), encoding="utf-8")
    ctx = SimpleNamespace(turn=turn, routine=SimpleNamespace(dir=routine),
                          server=SimpleNamespace(libraries_home=lib, permissions_home=perms))
    loop = SimpleNamespace(ctx=ctx)
    sendgate.configure(loop)
    return loop


def _util(*args: str) -> dict:
    return {"kind": "util", "name": args[0], "args": list(args[1:])}


def _script(loop, name: str, body: str, *, secrets: str = "(none)", calls: str = "gmail"):
    (loop.ctx.routine.dir / "scripts" / f"{name}.py").write_text(
        _header(name, secrets=secrets, calls=calls) + body, encoding="utf-8")
    return {"kind": "script", "name": name}


SEND = _util("gmail", "send", "--to", "a@b.c", "--subject", "s", "--body", "b", "--yes")
CHECK = _util("gmail", "list", "--mailbox", "sent", "--query", "to:a@b.c")
OK = {"kind": "util", "exit": 0, "stdout": "[]"}


# --- the declaration ----------------------------------------------------------------------

def test_the_seed_declares_the_operators_identity_on_every_channel_he_speaks_on():
    channels = read_library_outbox(SEED)
    assert {c.name for c in channels} == {"gmail", "fau-mail", "signal", "telegram",
                                          "whatsapp", "zulip", "usenet"}
    assert all((c.secrets or c.paths) and c.utils for c in channels)
    for path in sorted(SEED.glob("*.md")):
        assert lint_permission_text(path.read_text(encoding="utf-8"), filename=path.name) == []


@pytest.mark.parametrize(("raw", "fragment"), [
    ({"gmail": {"identity": {}, "utils": {"gmail": {}}, "check": "c", "run": "r"}},
     "identity must name"),
    ({"gmail": {"identity": {"secrets": ["X"]}, "utils": {}, "check": "c", "run": "r"}},
     "utils must map"),
    ({"gmail": {"identity": {"secrets": ["X"]}, "utils": {"gmail": {}}, "check": "(",
                "run": "r"}}, "not a valid regex"),
    ({"gmail": {"identity": {"secrets": ["X"]}, "utils": {"gmail": {}}, "check": "c"}},
     "a channel is exactly"),
    ({"gmail": {"identity": {"secrets": ["X"]}, "utils": {"gmail": {"never_sends": ["send"]}},
                "check": "c", "run": "r"}}, "would pass the gate unchecked"),
    (["gmail"], "MAPPING"),
])
def test_a_malformed_channel_is_reported(raw, fragment):
    _got, problems = normalize_outbox(raw, reserved=["gmail:send"])
    assert problems and any(fragment in p for p in problems), problems


def test_a_doc_speaking_as_the_operator_must_place_every_send_in_a_channel():
    meta = {"tags": ["outbound", "as-operator"], "requires": {"utils": ["gmail:send", "x:send"]},
            "outbox": {"gmail": {"identity": {"secrets": ["G"]}, "utils": {"gmail": {}},
                                 "check": "c", "run": "r"}}}
    assert [p for p in as_operator_problems(meta) if "'x:send'" in p]
    assert not [p for p in as_operator_problems(meta) if "'gmail:send'" in p]
    assert as_operator_problems({**meta, "tags": ["outbound"]}) == []


# --- what counts as a send ------------------------------------------------------------------

def test_a_send_with_no_read_of_its_outbox_is_refused_with_the_read_to_make(tmp_path):
    loop = _loop(tmp_path)
    refusal = sendgate.refusal(loop, SEND)
    assert refusal and refusal[0].startswith("SEND REFUSED — nothing was sent.")
    assert 'gu gmail list --mailbox sent --query "to:<recipient>"' in refusal[0]
    assert "may have sent it himself" in refusal[0]
    # a dry run sends nothing; a declared read or a util without his identity is no send
    assert sendgate.refusal(loop, _util("gmail", "send", "--to", "a@b.c")) == []
    assert sendgate.refusal(loop, CHECK) == []
    assert sendgate.refusal(loop, _util("gmail", "draft", "--to", "a@b.c")) == []
    assert sendgate.refusal(loop, _util("weather", "today")) == []
    assert sendgate.refusal(loop, {"kind": "write_file", "path": "a"}) == []


def test_an_unclassified_verb_is_a_send(tmp_path):
    """A verb nobody declared silent — a new one included — is gated until it is."""
    loop = _loop(tmp_path)
    refusal = sendgate.refusal(loop, _util("gmail", "forward", "--uid", "3", "--yes"))
    assert refusal and "SEND REFUSED" in refusal[0]


def test_a_util_holding_his_identity_that_no_permission_lists_is_refused(tmp_path):
    """A util created tomorrow cannot speak as him: it holds the credential, so it is refused
    until the library lists it — directly, through another util's calls, or from a script."""
    loop = _loop(tmp_path)
    for action in (_util("mailer", "go"), _util("digest", "build")):
        refusal = sendgate.refusal(loop, action)
        assert refusal and refusal[0].startswith("REFUSED — nothing ran.")
        assert "GMAIL_APP_PASSWORD" in refusal[0] and "outbound-mail" in refusal[0]
    script = _script(loop, "via", 'run(["gu", "mailer", "go"])\n', calls="mailer")
    refusal = sendgate.refusal(loop, script)
    assert refusal and "Script 'via' calls it." in refusal[0]


def test_a_script_is_classified_by_the_calls_it_makes(tmp_path):
    loop = _loop(tmp_path)
    reads = _script(loop, "poll", 'run(["gu", "gmail", "list", "--mailbox", "inbox"])\n')
    assert sendgate.refusal(loop, reads) == []
    sends = _script(loop, "deliver", 'run(["gu", "gmail", "send", "--to", x, "--yes"])\n')
    assert "is not declared to stay silent" in sendgate.refusal(loop, sends)[0]
    built = _script(loop, "wrap", 'run(["gu", "gmail", *args])\n')      # a verb it builds
    assert sendgate.refusal(loop, built)
    unused = _script(loop, "build", "print('no mail here')\n")           # holds it, never says
    assert "drop it from the calls: line" in sendgate.refusal(loop, unused)[0]
    own = _script(loop, "smtp", "smtplib.SMTP(...)\n", secrets="GMAIL_APP_PASSWORD",
                  calls="(none)")
    assert "holds GMAIL_APP_PASSWORD itself" in sendgate.refusal(loop, own)[0]
    # riding a write as its then_script is the same script
    assert sendgate.refusal(loop, {"kind": "write_file", "path": "a", "content": "x",
                                   "then_script": ["deliver"]})


# --- reads license sends, once -------------------------------------------------------------

def test_a_fresh_read_licenses_one_send_and_the_send_consumes_it(tmp_path):
    loop = _loop(tmp_path, turn=4)
    sendgate.observed(loop, CHECK, OK)           # turn 4: the outbox is read
    assert loop.outbox_checked == {"gmail": 4}
    assert sendgate.refusal(loop, SEND) == []     # turn 5 would be the send
    loop.ctx.turn = 5
    sendgate.observed(loop, SEND, OK)
    assert loop.outbox_checked == {}              # each send owes its own read
    assert sendgate.refusal(loop, SEND)


def test_a_read_too_long_ago_or_that_failed_licenses_nothing(tmp_path):
    loop = _loop(tmp_path, turn=2)
    sendgate.observed(loop, CHECK, OK)
    loop.ctx.turn = 2 + sendgate.WINDOW           # the next candidate is WINDOW+1 turns later
    assert sendgate.refusal(loop, SEND)
    loop = _loop(tmp_path, turn=4)
    sendgate.observed(loop, CHECK, {"kind": "util", "exit": 1})
    assert sendgate.refusal(loop, SEND)


def test_a_held_send_ran_nothing_and_a_failed_one_still_owes_a_read(tmp_path):
    loop = _loop(tmp_path, turn=4)
    sendgate.observed(loop, CHECK, OK)
    sendgate.observed(loop, SEND, {"kind": "assist_hold", "action": "util:gmail send"})
    assert loop.outbox_checked == {"gmail": 4}
    # whether a failed send left a message behind is exactly what the next read must say
    sendgate.observed(loop, SEND, {"kind": "util", "exit": 1})
    assert loop.outbox_checked == {}


def test_a_resumed_leg_replays_the_reads_and_the_sends(tmp_path):
    loop = _loop(tmp_path, turn=7)
    events = [
        {"type": "assistant_action", "turn": 5, "payload": CHECK},
        {"type": "observation", "turn": 5, "payload": OK},
        {"type": "assistant_action", "turn": 6, "payload": SEND},
        {"type": "observation", "turn": 6, "payload": OK},
        {"type": "assistant_action", "turn": 7, "payload": CHECK},
        {"type": "observation", "turn": 7, "payload": OK},
    ]
    sendgate.rebuild(loop, events)
    assert loop.outbox_checked == {"gmail": 7}
    sendgate.rebuild(loop, events[:4])
    assert loop.outbox_checked == {}


# --- end to end: refused at validation, licensed by the read --------------------------------

def test_a_run_is_refused_its_send_until_it_reads_the_outbox(make_routine, scripted,
                                                              monkeypatch):
    d = make_routine(slug="mailer")
    server = server_for(d)
    perms = server.libraries_home / "permissions"
    perms.mkdir(parents=True)
    (perms / "outbound-mail.md").write_text(MAIL_DOC, encoding="utf-8")
    set_capabilities(d, utils=["gmail:send"])
    ran: list[str] = []
    monkeypatch.setattr(utils_run, "run_util", lambda _home, name, args, **_kw:
                        (ran.append(" ".join([name, *args])) or (0, "[]", "")))
    monkeypatch.setattr(utils_lib, "exists", lambda _home, _name: True)
    monkeypatch.setattr(utils_run, "util_needs", lambda _home, name: utils_run.UtilNeeds(
        {"GMAIL_APP_PASSWORD"}, False, set(), True, (), (name,)))
    ep = scripted([{**SEND, "say": "sending"}, {**CHECK, "say": "reading Sent"},
                   {**SEND, "say": "sending now"}, finish()])
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    # the first send never ran: it was refused inside the retry cycle, the read took its turn
    assert [r.split()[1] for r in ran] == ["list", "send"]
    seen = " ".join(str(m.get("content")) for call in ep.calls for m in call["messages"])
    assert "SEND REFUSED — nothing was sent." in seen
    events, _ = read_events(run_dir / "transcript.jsonl")
    acts = [e["payload"]["args"][0] for e in events
            if e["type"] == "assistant_action" and e["payload"].get("kind") == "util"]
    assert acts == ["list", "send"]
