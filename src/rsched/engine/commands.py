"""User slash commands — the chat's way to run the SAME effect actions the model can.

A command is one line, `/<kind> …` (the composer autocompletes them; its help panel
documents them). Parsing produces an ordinary action dict that then rides the model
action's exact path — schema validate → validate_action (workflow tools ∩ capabilities)
→ executor.dispatch — at a turn boundary, WITHOUT costing a model turn (see
control.run_user_command). Loop-control kinds (spawn/subtask/wait/ask_user/finish/…)
are deliberately NOT commands: they steer the model's run; ask the assistant instead.

Grammar per kind (quotes via shlex where tokenized; <content…> tails are verbatim):
  /util <name> [arg …]                          args exactly as `gu <name>` takes them
  /read_file <path> [path …]
  /view_image <path> [what to look for…]
  /write_file <path> <content…>
  /edit_file <path> anchor="…" replacement="…"
  /llm <prompt…>
  /memory_read <name>
  /memory_write <name> about="…" <content…>
"""

from __future__ import annotations

import re
import shlex


class CommandError(ValueError):
    """A malformed / unknown command — the message becomes the teaching observation."""


# (kind, usage, one-line summary) — the help panel + autocomplete read this; the order is
# the display order. Every kind here is executor-dispatched (an EFFECT, not loop control).
COMMAND_HELP = (
    ("util", "/util <name> [arg …]",
     "run a global util with exactly the arguments `gu <name>` takes (add --json for "
     "structured output); `/util list` shows the catalog"),
    ("read_file", "/read_file <path> [path …]",
     "read one or more files (working dir or an allowed root)"),
    ("view_image", "/view_image <path> [what to look for…]",
     "show an image/PDF to the assistant (described via the vision util when the model "
     "can't view it directly)"),
    ("write_file", "/write_file <path> <content…>",
     "write a file — everything after the path is the content, verbatim"),
    ("edit_file", '/edit_file <path> anchor="…" replacement="…"',
     "replace an exact anchor string in a file, in place"),
    ("llm", "/llm <prompt…>",
     "one stateless LLM subcall on this conversation's tool-call model"),
    ("memory_read", "/memory_read <name>",
     "read one persistent memory note"),
    ("memory_write", '/memory_write <name> about="…" <content…>',
     "write a persistent memory note (`about` becomes its index line)"),
)


def _split(tail: str, usage: str) -> list[str]:
    try:
        tokens = shlex.split(tail)
    except ValueError as exc:
        raise CommandError(f"unbalanced quotes ({exc}) — usage: {usage}") from exc
    if not tokens:
        raise CommandError(f"usage: {usage}")
    return tokens


def _util(tail: str) -> dict:
    tokens = _split(tail, "/util <name> [arg …]")
    return {"name": tokens[0], "args": tokens[1:]}


def _read_file(tail: str) -> dict:
    tokens = _split(tail, "/read_file <path> [path …]")
    return {"path": tokens[0]} if len(tokens) == 1 else {"paths": tokens}


def _view_image(tail: str) -> dict:
    parts = tail.split(maxsplit=1)
    if not parts:
        raise CommandError("usage: /view_image <path> [what to look for…]")
    out = {"path": parts[0]}
    if len(parts) > 1:
        out["prompt"] = parts[1]
    return out


def _write_file(tail: str) -> dict:
    parts = tail.split(maxsplit=1)
    if len(parts) < 2:
        raise CommandError("usage: /write_file <path> <content…>")
    return {"path": parts[0], "content": parts[1]}


def _edit_file(tail: str) -> dict:
    usage = '/edit_file <path> anchor="…" replacement="…"'
    tokens = _split(tail, usage)
    pairs = dict(t.split("=", 1) for t in tokens[1:] if "=" in t)
    if "anchor" not in pairs or "replacement" not in pairs:
        raise CommandError(f"usage: {usage}")
    return {"path": tokens[0], "anchor": pairs["anchor"], "replacement": pairs["replacement"]}


def _llm(tail: str) -> dict:
    if not tail:
        raise CommandError("usage: /llm <prompt…>")
    return {"prompt": tail}


def _memory_read(tail: str) -> dict:
    tokens = _split(tail, "/memory_read <name>")
    return {"name": tokens[0]}


_ABOUT = re.compile(r"""\babout=(?:"([^"]*)"|'([^']*)')\s*""")


def _memory_write(tail: str) -> dict:
    usage = '/memory_write <name> about="…" <content…>'
    parts = tail.split(maxsplit=1)
    if len(parts) < 2:
        raise CommandError(f"usage: {usage}")
    name, rest = parts
    about = ""
    if m := _ABOUT.search(rest):
        about = m.group(1) or m.group(2) or ""
        rest = (rest[:m.start()] + rest[m.end():]).strip()
    if not rest:
        raise CommandError(f"usage: {usage}")
    return {"name": name, "content": rest, **({"about": about} if about else {})}


_PARSERS = {
    "util": _util,
    "read_file": _read_file,
    "view_image": _view_image,
    "write_file": _write_file,
    "edit_file": _edit_file,
    "llm": _llm,
    "memory_read": _memory_read,
    "memory_write": _memory_write,
}


def parse_command(text: str) -> dict:
    """One command line → an ordinary action dict (validated downstream exactly like a
    model action). Raises CommandError with the usage line on any malformed input.
    """
    line = text.strip()
    if not line.startswith("/"):
        raise CommandError("not a command — commands start with /<kind>")
    head, _, tail = line[1:].partition(" ")
    kind = head.strip().lower()
    parser = _PARSERS.get(kind)
    if parser is None:
        known = ", ".join(f"/{k}" for k, _u, _s in COMMAND_HELP)
        raise CommandError(
            f"unknown command /{kind} — available: {known}. Loop-control actions "
            "(spawn, subtask, wait, ask_user, finish, …) steer the assistant's run: "
            "ask it in plain words instead.")
    return {"say": f"user command: /{kind}", "kind": kind, **parser(tail.strip())}


def command_catalog(policy, utils: list[dict]) -> dict:
    """The help/autocomplete payload: the command kinds this conversation's capability
    surface actually allows (the engine still enforces at execution) + the util catalog.
    """
    kinds = [{"kind": kind, "usage": usage, "summary": summary}
             for kind, usage, summary in COMMAND_HELP
             if policy is None or policy.allows_kind(kind)]
    return {"kinds": kinds,
            "utils": [{"name": u["name"], "summary": u.get("summary") or "",
                       "usage": u.get("usage") or ""} for u in utils]}
