"""WHOSE exit status a `shell` observation reports — said when the command line hides it.

A run reads `exit 0` as "it passed", but `pytest | tail` exits with `tail`'s status,
`make test || true` exits 0 whatever `make` did, `pytest; echo done` with `echo`'s. When the
exit is not the first command's, the observation SAYS whose it is.

Deterministic over the stored command, so a resumed leg's replay renders the same words. A
small scanner (not a shell parser) respects quoting and `$(…)`: a false positive costs a
parenthetical. Nothing is said for a non-zero exit or under `pipefail`.
"""

from __future__ import annotations

#: The top-level separators this module reads, longest first so `||` is never read as `|`.
_OPERATORS = ("||", "&&", "|&", "|", ";", "&", "\n")


def _top_level(command: str) -> list[tuple[str, str]]:
    """The command split at top-level operators: `[(segment, operator_after), …]`.

    Single and double quotes, a backslash escape, `$(…)`/`(…)` nesting and `` `…` `` are
    skipped over, so an operator inside them never splits. The last segment's operator is "".
    """
    out: list[tuple[str, str]] = []
    buf: list[str] = []
    depth, quote, i = 0, "", 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(command):
                buf.append(command[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = ""
            buf.append(ch)
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
        elif ch == "\\" and i + 1 < len(command):
            buf.append(command[i:i + 2])
            i += 2
            continue
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        elif depth == 0 and not _redirect(command, i) and (
                op := next((o for o in _OPERATORS if command.startswith(o, i)), "")):
            out.append(("".join(buf).strip(), op))
            buf = []
            i += len(op)
            continue
        buf.append(ch)
        i += 1
    out.append(("".join(buf).strip(), ""))
    return [(seg, op) for seg, op in out if seg or op not in (";", "\n")]


def _redirect(command: str, i: int) -> bool:
    """Is the `&` at `i` part of a redirection (`2>&1`, `>&2`, `&>file`), not an operator?"""
    return command[i] == "&" and ((i > 0 and command[i - 1] in "<>")
                                  or command.startswith("&>", i))


def _program(segment: str) -> str:
    """The program a segment runs — its first word that is not a `VAR=value` assignment."""
    for word in segment.split():
        if "=" not in word.split("/")[0]:
            return word
    return segment.split(maxsplit=1)[0] if segment.split() else segment


def note(obs: dict) -> str:
    """The parenthetical a `shell` observation's header carries, or "".

    Said only for `exit 0`: that is the reading a run acts on as "it passed".
    """
    if obs.get("exit") != 0:
        return ""
    command = str(obs.get("command") or "")
    if "pipefail" in command or "PIPESTATUS" in command:
        return ""
    parts = _top_level(command)
    if len(parts) < 2 or not all(seg for seg, _ in parts):
        return ""
    last = _program(parts[-1][0])
    start = len(parts) - 1   # where the command line's FINAL pipeline begins
    while start > 0 and parts[start - 1][1] in ("|", "|&"):
        start -= 1
    if start < len(parts) - 1:
        subject = _program(parts[start][0])
        return (f" — the exit of `{last}`, the pipe's last stage, not of `{subject}`"
                if subject != last else "")
    op, subject = parts[-2][1], _program(parts[-2][0])
    if op == "||":
        return (f" — a `||` fallback follows `{subject}`, so this 0 does not say `{subject}` "
                "succeeded")
    if op == "&":
        return f" — `{subject}` ran in the background, so this 0 is not its status"
    if op in (";", "\n") and subject != last:
        return f" — the exit of `{last}`, the last command, not of `{subject}`"
    return ""
