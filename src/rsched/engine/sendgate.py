"""The SEND GATE — a message goes out in the operator's name only right after its outbox was read.

The operator's order, twice (2026-10-02, 2026-10-09): each outbound message is first verified
not to have gone out already — he may have sent it himself, or an earlier run did — and the
send FAILS, with a reminder of what to read, when that check was not made right before. Advice
had been tried: a routine carried the correction for seven weeks marked "no mechanism", and a
run that means to check and forgets is exactly the failure a reminder does not fix.

So this is a gate, and it can be one because it is CHECKABLE: whether a channel's outbox was
read before a send is in the trace, which is what most rules are not (docs/rule-assists.md).
And it holds for utils nobody has written yet, because it keys on the operator's IDENTITY,
not on a list of utils: the library names the credentials that act as him on each channel
(`rsched/outbox.py`), and code can only hold one by declaring it — the sandbox injects and
mounts nothing else. For any util or script call, the gate asks which identities its declared
credentials reach, then:

- a util the channel LISTS is classified by its verb — `never_sends` passes, anything else
  (a verb nobody classified included) is a SEND;
- a util that reaches the identity and is NOT listed is REFUSED outright, directly or from a
  script: it can speak as him and nobody has written down how its outbox is read;
- a routine script is classified by the `gu` calls in its source — a call it cannot read the
  verb of, or a credential the script declares itself, makes it a SEND.

A send runs only if the channel's outbox was read (a successful call matching its `check`)
within `WINDOW` turns and after the channel's previous send. The refusal happens at the
validation seam (`completion.action_candidate`), inside the schema-retry cycle — it costs no
turn, and the corrected action is the read itself. `observed` stamps a read and consumes it on
an executed send (a failed send too: whether it left a message behind is what the next read
must say); `rebuild` replays both on a resumed leg. A slash command the operator types never
passes this seam.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .. import outbox as lib
from .actionschema import canon

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..outbox import Channel

#: How many turns may separate a channel's outbox read from the send it licenses.
WINDOW = 3

#: `["gu", "<util>"` and, when the next element is a string literal, its verb (group 3).
_GU_CALL_RE = re.compile(
    r"""\[\s*["']gu["']\s*,\s*["']([a-z0-9][a-z0-9-]*)["']\s*(?:,\s*(["'])([^"']*)\2)?""")


def configure(loop) -> None:
    """This layer's run state: the library's channels, and the ledger of fresh reads
    (`channel → turn`).
    """
    try:
        # `<libraries_home>/permissions` — what ServerConfig.permissions_home is, by construction
        loop.outbox = lib.read_library_outbox(Path(loop.ctx.server.libraries_home) / "permissions")
    except OSError:
        loop.outbox = []
    loop.outbox_checked = {}


def rebuild(loop, events: list[dict]) -> None:
    """Replay the reads and the sends from the transcript: each executed action is an
    `assistant_action` followed by its `observation`.
    """
    loop.outbox_checked = {}
    if not getattr(loop, "outbox", None):
        return
    action: dict | None = None
    turn = 0
    for ev in events:
        if ev.get("type") == "assistant_action":
            action, turn = dict(ev.get("payload") or {}), int(ev.get("turn") or 0)
        elif ev.get("type") == "observation" and action is not None:
            _record(loop, action, dict(ev.get("payload") or {}), turn)
            action = None


def refusal(loop, action: object) -> list[str]:
    """The validation-seam answer: [] to let the action through, else why it fails."""
    if not isinstance(action, dict) or not getattr(loop, "outbox", None):
        return []
    sends, denied = _verdict(loop, action)
    if denied:
        return [("REFUSED — nothing ran. " + " ".join(denied) + " A util holding the user's "
                 "identity runs only once the library lists it with the verbs that cannot send "
                 "and the call that reads its outbox. That is the library's change to make, "
                 "not this run's: report it, and do the work another way.")]
    turn = int(loop.ctx.turn or 0) + 1      # the turn this candidate would be
    owed = [(c, via) for c, via in sends
            if (stamped := loop.outbox_checked.get(c.name)) is None or turn - stamped > WINDOW]
    if not owed:
        return []
    lines = [f"- {c.name}{via}: first run `{c.run}` (any call matching /{c.check.pattern}/ "
             "counts)" for c, via in owed]
    return [("SEND REFUSED — nothing was sent. A message in the user's name goes out only right "
             "after that channel's outbox was read for it: the user may have sent it himself, "
             "or an earlier run did.\n" + "\n".join(lines) + "\n"
             "Read it, and if the message is there, record that it went out and do not send it "
             f"again. If it is not, send within {WINDOW} turns of the read — each send owes "
             "its own.")]


def observed(loop, action: dict, obs: dict) -> None:
    """Stamp a successful outbox read; consume the stamp on an executed send."""
    if getattr(loop, "outbox", None):
        _record(loop, action, obs, int(loop.ctx.turn or 0))


def _record(loop, action: dict, obs: dict, turn: int) -> None:
    from .hold import is_hold
    from .observations import is_failure

    if not obs or is_hold(obs) or obs.get("refused"):
        return                          # nothing ran
    if not is_failure(obs):
        rendered = canon(action)
        for channel in loop.outbox:
            if channel.check.search(rendered):
                loop.outbox_checked[channel.name] = turn
    for channel, _via in _verdict(loop, action)[0]:
        loop.outbox_checked.pop(channel.name, None)


def _verdict(loop, action: dict) -> tuple[list[tuple[Channel, str]], list[str]]:
    """(the channels this action SENDS through, each with how; why it may not run at all)."""
    from . import thenscript

    sends: list[tuple[Channel, str]] = []
    denied: list[str] = []
    for part in (action, thenscript.riding(action)):
        if not isinstance(part, dict):
            continue
        if part.get("kind") == "util" and part.get("name"):
            _util(loop, part, sends, denied)
        elif part.get("kind") == "script" and part.get("name"):
            _script(loop, str(part["name"]), sends, denied)
    return sends, denied


def _reach(loop, util: str) -> tuple[set[str], list[str]]:
    """The secrets and the stores a util's whole call tree declares."""
    from ..utils_run import util_needs

    try:
        needs = util_needs(loop.ctx.server.libraries_home, util)
    except (OSError, ValueError):
        return set(), []
    return set(needs.secrets), [path for _mode, path in needs.fs_paths]


def _util(loop, part: dict, sends: list, denied: list) -> None:
    name = str(part["name"])
    secrets, paths = _reach(loop, name)
    args = part.get("args") or []
    verb = str(args[0]) if args and isinstance(args[0], str) else ""
    for channel in loop.outbox:
        if not (creds := channel.reached(secrets, paths)):
            continue
        cls = channel.utils.get(name)
        if cls is None:
            denied.append(_undeclared(name, channel, creds))
        elif verb not in cls.never_sends and (cls.send_if is None
                                              or cls.send_if.search(canon(part))):
            sends.append((channel, ""))


def _script(loop, name: str, sends: list, denied: list) -> None:
    from ..scripts import script_path
    from ..utils_header import parse_header

    try:
        src = script_path(loop.ctx.routine.dir, name).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return
    header = parse_header(src)
    calls: dict[str, list[str | None]] = {}
    for util, _q, verb in _GU_CALL_RE.findall(src):
        calls.setdefault(util, []).append(verb or None)    # None: a verb the code builds
    own = [path for _mode, path in header["fs_paths"]]
    for channel in loop.outbox:
        if creds := channel.reached(set(header["secrets"]), own):
            sends.append((channel, f" (script {name!r} holds {', '.join(creds)} itself)"))
            continue
        for util in header["calls"]:
            if not (creds := channel.reached(*_reach(loop, util))):
                continue
            cls = channel.utils.get(util)
            invoked = calls.get(util)
            if cls is None:
                denied.append(_undeclared(util, channel, creds) + f" Script {name!r} calls it.")
            elif not invoked:
                # it holds the identity (`calls:` hands over the callee's credentials) and
                # shows no call the gate can classify — so it could use them any way at all
                sends.append((channel, (f" (script {name!r} holds {', '.join(creds)} through "
                                        f"`calls: {util}` but never runs `gu {util}` where the "
                                        "gate can read it — drop it from the calls: line if "
                                        "the script does not use it)")))
                break
            elif any(v is None or v not in cls.never_sends for v in invoked):
                sends.append((channel, (f" (script {name!r} runs `{util}` with a verb that "
                                        "is not declared to stay silent)")))
                break


def _undeclared(util: str, channel: Channel, creds: list[str]) -> str:
    return (f"Util {util!r} can act as the user on {channel.name} — it holds "
            f"{', '.join(creds)} — and the {channel.doc} permission does not list it.")
