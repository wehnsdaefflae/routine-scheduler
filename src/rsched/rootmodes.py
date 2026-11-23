"""The ROOT COMMAND CHECK's three modes and the warning options that change them.

A routine (or conversation) whose run holds a machine logging in with root rights has its calls
rated by the decision model before they run (engine/rootcheck.py). Its `root_check` says which:
`remote` — the `remote` util's exec/submit/push/pull to such a machine, the default — `any` —
every tool call the run makes — or `off`.

Every warning offers approve and decline, and — for each OTHER mode — "approve, and switch to it
for this run" or "… from now on". The engine applies a switch to the run; the WEB writes a
"from now on" into routine.yaml at click time (web/root_check_answer.py), like an access
request's forever-decision. Both read the options through `parse`, so the words a person
clicks and the change that lands cannot drift apart.
"""

from __future__ import annotations

from typing import Literal

RootCheck = Literal["remote", "any", "off"]
MODES: tuple[RootCheck, ...] = ("remote", "any", "off")
DEFAULT: RootCheck = "remote"
#: What each mode checks, as the warning and the settings say it.
LABELS: dict[str, str] = {"remote": "remote commands only", "any": "every tool call",
                          "off": "nothing (check off)"}
SCOPES = {"run": "for this run", "general": "from now on"}
APPROVE, DECLINE = "approve", "decline"
#: The decision type a warning is filed under (engine/interact.py settles it like an approval).
QTYPE = "command-approval"


def option(mode: str, scope: str) -> str:
    return f"approve, then check {LABELS[mode]} {SCOPES[scope]}"


def options(current: str) -> list[str]:
    """What a warning raised under `current` offers: the decision, then each switch."""
    return [APPROVE, DECLINE, *(option(m, s) for m in MODES if m != current for s in SCOPES)]


def parse(text: str) -> tuple[str, str] | None:
    """`(mode, scope)` when `text` is one of the switch options, else None."""
    wanted = text.strip().lower()
    return next(((m, s) for m in MODES for s in SCOPES if option(m, s) == wanted), None)
