"""Declared INFORMATIONAL exit codes — the `informational-exits:` header line, and the one
seam that reads it.

A non-zero exit is the failure signal for a callable kind (`observations.is_failure`), and for
almost every call that is right. A tool whose own contract documents a non-zero code as an
ANSWER breaks the assumption: `candidate_gate.py` documents exit 2 as "the gate has not
finished — a poll in progress is not a usage mistake" (F648), and a status poll of a detached
release gate returns it many times in a row, working exactly as designed. The repeat-failure
detector counted those polls and fired `fix-the-cause` twice in one run on healthy behaviour
(F711, evidence `self-audit:20261016-223226`). The cost is not the line it printed; it is that
a rule firing on healthy behaviour trains a run to dismiss it, which is the opposite of what
the rule is for.

So the tool that knows its own exit codes DECLARES them, the way a util already declares
`net:`, `fs:` and `secrets:` on its docstring header — one line, read from the SOURCE, never
by running anything:

    informational-exits: 2

The alternative considered and REJECTED was an engine-side heuristic: exempt a call whose exit
code is unchanged across consecutive identical invocations of a polling shape. It cannot work
here, because that is the shape of the detector's own core case — a genuinely broken call also
fails identically twice in a row, and `assist_predicates._repeated_failure` fires on exactly
"the same call has failed twice". The heuristic would silence the defect the detector exists to
catch, and silently: nothing in the trace would say why the line never came. A declaration is
explicit, is local to the tool that knows what its own codes mean, and leaves every undeclared
callee behaving precisely as before.

A declaration never silences a real failure. It suppresses the tally only when the declared
exit code is the ONLY thing wrong with the observation — asked by running the one classifier
again over the same observation with the exit zeroed, so an `error`, a refusal, a failed riding
script or one bad file in a batch still counts, whatever the exit code says. Only the exact
declared codes stop counting; every other non-zero still does.

Both callable kinds are covered, because both can document a non-zero exit: a library util
(`utils_lib.read_util`) and a routine's own `scripts/<name>.py` (`scripts.script_path`), which
is where the F711 case lives.
"""

from __future__ import annotations

import re

#: The header key. Deliberately NOT `exits:` — `candidate_gate.py` already carries a PROSE
#: `exit:` line ("0 release · 1 restore · 2 unfinished …") that no machine can read, and a
#: separate key keeps the human contract and the machine-read declaration from being mistaken
#: for one another. A tool may carry both; they are different statements.
INFO_EXITS_KEY = "informational-exits:"

#: One entry: a small exit code, optionally followed by a gloss (`2 (a poll in progress)`).
#: Anything else is IGNORED rather than guessed at — an unparseable entry declares nothing and
#: the exit keeps counting, which is the safe direction for a typo: the failure that is not
#: really one costs a line, the real failure that stopped counting costs the run.
_ENTRY_RE = re.compile(r"^(\d{1,3})\s*(?:[(\[\-—:].*)?$", re.DOTALL)


def declared_informational(src: str) -> frozenset[int]:
    """The exit codes a util's or script's docstring header declares as informational.

    Read off the DOCSTRING (`utils_header.parse_header`'s `doc`), the same surface `secrets:`
    and `net:` are read from — so the declaration is visible to the engine without executing
    the thing, which is the whole point: an exit code's meaning must be knowable before the
    call that returns it.
    """
    from ..utils_header import parse_header

    try:
        doc = str(parse_header(src).get("doc") or "")
    except (ValueError, TypeError):
        return frozenset()
    out: set[int] = set()
    for line in doc.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith(INFO_EXITS_KEY):
            continue
        for raw in stripped[len(INFO_EXITS_KEY):].split(","):
            m = _ENTRY_RE.match(raw.strip())
            if m and int(m.group(1)):        # 0 is not a failure to begin with
                out.add(int(m.group(1)))
    return frozenset(out)


def _callee_source(ctx, action: dict) -> str:
    """The SOURCE of the util or script this action called — "" when there is none to read.

    A `util` action's name can be the dispatcher's own verb (`list`, `search`), which is no
    util and carries no header; `read_util` answers None for it. Any unreadable source leaves
    the declaration empty, so the exit counts: this seam fails CLOSED by construction.

    Broad except on purpose: unlike a predicate, whose fires are inert when it raises, this is
    asked from the loop's own tally on EVERY observation (`loop._observe`) — so a source that
    cannot be read must cost the bookkeeping, never the turn.
    """
    from .. import scripts, utils_lib
    from .executor import script_home

    kind, name = str(action.get("kind") or ""), str(action.get("name") or "")
    if kind not in ("util", "script") or not name:
        return ""
    try:
        if kind == "util":
            return utils_lib.read_util(ctx.server.libraries_home, name) or ""
        home = script_home(ctx, name)
        return scripts.script_path(home, name).read_text(encoding="utf-8") if home else ""
    except Exception:       # see above: the tally yields, never the turn
        return ""


def failure_tally_key(ctx, action: dict, obs: dict) -> str:
    """The key this observation's failure is tallied under, or "" when it is not a failure to
    count.

    The engine's repeat-failure bookkeeping (`loop._observe`) and the predicate that reads that
    tally (`assist_predicates._repeated_failure`) both ask THIS question, so they can never
    disagree about what a declared exit code means — the way two readers of one classifier
    already cannot disagree about what a failure is.
    """
    from .assist_predicates import failure_key
    from .observations import is_failure

    if not is_failure(obs):
        return ""
    code = int(obs.get("exit") or 0)
    if (code and code in declared_informational(_callee_source(ctx, action))
            and not is_failure({**obs, "exit": 0})):
        return ""       # the declared code was the ONLY thing wrong with this observation
    return failure_key(action)
