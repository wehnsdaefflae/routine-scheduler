"""Declared INFORMATIONAL exit codes — the `informational-exits:` header line, and the one
seam that reads it.

A non-zero exit is a callable's failure signal (`observations.is_failure`), but some tools
document a non-zero code as an ANSWER (e.g. "poll in progress"), and tallying those as repeat
failures fires `fix-the-cause` on healthy behaviour — which trains runs to dismiss the rule.
So the tool DECLARES them on its docstring header, read from the SOURCE like `net:`/`secrets:`:

    informational-exits: 2

No engine-side "same exit on identical polls" heuristic: that is exactly the shape of a
genuinely broken call failing twice, so it would silence the detector's core case.

A declaration never silences a real failure: the tally is skipped only when the declared code
is the ONLY thing wrong (the classifier is re-asked with the exit zeroed). Both library utils
and a routine's own `scripts/<name>.py` are covered.
"""

from __future__ import annotations

import re

#: The header key — deliberately not `exits:`, which tools use for human-readable prose.
INFO_EXITS_KEY = "informational-exits:"

#: One entry: a small exit code, optionally glossed (`2 (a poll in progress)`). Anything else
#: declares nothing, so the exit keeps counting — the safe direction for a typo.
_ENTRY_RE = re.compile(r"^(\d{1,3})\s*(?:[(\[\-—:].*)?$", re.DOTALL)


def declared_informational(src: str) -> frozenset[int]:
    """The exit codes a util's or script's docstring header declares as informational.

    Read off the DOCSTRING (`utils_header.parse_header`'s `doc`), never by executing anything.
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

    An unreadable source (including a dispatcher verb like `list`) declares nothing, so the
    exit counts — fail CLOSED. Broad except: asked on every observation, it must never cost
    the turn.
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
    except Exception:       # the tally yields, never the turn
        return ""


def failure_tally_key(ctx, action: dict, obs: dict) -> str:
    """The key this observation's failure is tallied under, or "" when it is not a failure to
    count.

    The one question both the tally (`loop._observe`) and its reader
    (`assist_predicates._repeated_failure`) ask, so they cannot disagree.
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
