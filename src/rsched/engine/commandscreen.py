"""Screen COMMAND TEXT for the credential values this instance stores, on the way INTO a
record (D317).

WHY IT EXISTS. `captured_output.read_capped` screens what a command PRINTS, at the one capture
seam every util, script and shell call passes. What a command IS was stored verbatim: a shell
observation's `command`, the `assistant_action` event that records the same action, the
`ungated_write` health row. A credential inline on a command line — HTTP basic auth
(`curl -u user:pass`), URL userinfo (`https://user:pass@host`), `--password=` — therefore
survived in retained run records while the same value was screened out of the output beside it.

WHICH SECRET SET, AND WHY NOT THE HANDED ONE. A util call is handed name → value pairs and
`read_capped` screens with those. The `shell` path is handed NO store secret at all
(`executor.do_shell`), so the handed set is empty by construction and could never screen a
shell command. The set used here is therefore the instance's own needle set
(`credneedles.collect`) — every credential the instance stores, read where the values already
are. That is a threat-model position, not a refactor: the engine process now holds those bytes
in memory for the length of a run. They never leave it — a needle is matched, never rendered;
only the NAME it is stored under reaches a record.

WHAT IT CANNOT CLOSE. Only a value this instance STORES is a needle. A credential typed into a
command line that the instance does not hold (a one-off password, a colleague's token, a value
a command computes or splits) is matched by nothing and stays verbatim in the record. The
8-character floor (`captured_output.REDACT_MIN_CHARS`) is shared with the output seam for the
same reason: masking every short word would corrupt records without protecting anything a guess
could not reach. Pattern-matching the shapes above was measured and rejected as the mechanism:
on the transcript where the leak was found, five obvious patterns scored zero.

THE RECORDED COPY ONLY. The screen never touches the text that EXECUTES — `do_shell` runs the
command the run wrote and screens the observation it returns. A redacted record is therefore not
replayable as a command, which is the point: it is a record, not a script.

THE NEEDLE SET IS READ ONCE per engine process and cached, because every landed action passes
this seam. A credential STORED while a run is already in flight is consequently not a needle
until the next run; `reset()` drops the cache (the suite's seam, and what a long-lived process
would call after a store write).
"""

from __future__ import annotations

import logging

from .. import credneedles

log = logging.getLogger("rsched.commandscreen")

#: The action fields that carry COMMAND TEXT: the `shell` command line, and the argument vector
#: of a `util` or `script` call — a credential passed inline reads the same in either.
COMMAND_FIELDS = ("command",)
ARGV_FIELDS = ("args",)

#: The process-wide cache, a holder rather than a rebound module name — one writer, and no
#: `global` statement for a reader of this module to follow.
_CACHE: dict[str, credneedles.Needles] = {}


def needles() -> credneedles.Needles:
    """The instance's needle set, collected once per process.

    A collection that RAISES must not cost the run its turn, so the failure is logged and an
    empty set is cached: screening then does nothing, which is exactly the behaviour that
    preceded this seam — stated here so a silent empty set is never mistaken for a clean one.
    """
    if "needles" not in _CACHE:
        try:
            _CACHE["needles"] = credneedles.collect()
        except Exception as exc:
            log.warning("command screening collected no needles: %s: %s",
                        type(exc).__name__, exc)
            _CACHE["needles"] = credneedles.Needles()
    return _CACHE["needles"]


def reset() -> None:
    """Drop the cached needle set — the next screen collects it again."""
    _CACHE.clear()


def screened(text: str) -> str:
    """`text` with every stored credential value replaced by its `[secret NAME redacted]`."""
    return needles().redact(text) if text else text


def screened_brief(action: dict, cap: int = 200) -> str:
    """The action's BRIEF, screened and capped — for a record that keeps a one-line rendering
    rather than the action (the `action_cancelled` event, the admin-leg audit line). For a
    `shell` action the brief IS the command, so an unscreened one is the same exposure.

    The screen runs BEFORE the cap, so a marker is never cut in half, and the brief itself is
    left alone (`actionschema.brief_value` also feeds reminder and rule MATCHING, which must go
    on seeing what the run actually wrote).
    """
    from .actionschema import brief_value

    return screened(brief_value(action))[:cap]


def screened_action(action: dict) -> dict:
    """A COPY of `action` whose command text is screened — what a record stores.

    The copy is shallow and only the screened fields are rebuilt, so an action that carries no
    command text is recorded byte-identically and the common turn pays one dict copy.
    """
    out = dict(action)
    for field in COMMAND_FIELDS:
        value = out.get(field)
        if isinstance(value, str) and value:
            out[field] = screened(value)
    for field in ARGV_FIELDS:
        value = out.get(field)
        if isinstance(value, list) and value:
            out[field] = [screened(a) if isinstance(a, str) else a for a in value]
    return out
