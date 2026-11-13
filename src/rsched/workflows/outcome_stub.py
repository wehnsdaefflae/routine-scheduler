"""The `scripts/outcome.py` a new routine is BORN with, derived from its `produces` lines.

`create_routine` REQUIRES `produces` before anything is created (engine/outcome_words.py), so
the outcome CONTRACT always existed and the script that measures it never did: the routine's
first run recorded `{"error": "the routine declares an outcome but has no scripts/outcome.py"}`
and its own settings page called it broken on the day it was made.
`drawerrr-irreversible-actions` (created 2026-11-10) flagged itself immediately —
"recipe:outcome — it declares an outcome but has no scripts/outcome.py to measure it — every
run records an outcome error instead of a reading".

The operator's answer, verbatim (2026-11-12, D286): *"At creation: scaffold a
`scripts/outcome.py` stub derived from the `produces` lines, so a new routine measures
something from its first run (the reporter's preference)"*. He was given the alternative of
REFUSING creation until the author supplies a real script and did not choose it.

DERIVED, never guessed. A `produces` line has a defined form, so the contract parsed from it
(`OutcomeConfig`) names every measure's id, the words the person used for it, its kind and
which way is better — everything the script needs to print a reading the engine's own reader
accepts (`engine/outcome.parse`: exit 0, one JSON object on the last line, a number for every
declared measure; anything else is an `error`, never a zero).

WHY THE STUB REPORTS 0 FOR A MEASURE ITS RECORD DOES NOT CARRY, which is the one place this
file departs from the convention every hand-written results script here follows ("a measure the
records cannot support is LEFT OUT, never estimated"). Leaving one out makes the engine drop
the WHOLE reading as an error — exactly the state D286 exists to end — so a stub that honoured
that convention at birth would measure nothing and change nothing. For a `count` 0 is the
truth at birth (the routine has produced nothing yet); for a `level` it is a placeholder, and
the stub says so in its own prose and in its TODO rather than letting a reader mistake it for a
measurement. The honesty is carried by the TODO, not by the absence of a number.

The stub is a routine script like any other (rsched/scripts.py): its docstring header is the
only surface the engine machine-reads, so it declares `net: none` and `secrets: none` and
carries a `--selftest`, and it is stdlib-only with no PEP 723 block — a results script that
needed a venv built would hang a run's last turn on an install.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..config.outcome import OutcomeConfig

SCRIPT = "outcome"

#: The record the stub counts from until a stage writes the routine's real ones. One file, one
#: shape (`{<measure id>: <number>}`), so a stage can start feeding the series before anybody
#: edits the script — which is what makes a stub worth scaffolding rather than a comment.
RECORD_REL = "state/outcome.json"

#: Substituted by `str.replace`, never `%` or `.format`: the template is Python source full of
#: braces, and doubling every one of them to survive a format call is how a generated file
#: acquires a syntax error nobody reads until a run's last turn.
_TEMPLATE = '''\
"""outcome — the numbers this routine produces for @@PRINCIPAL@@, from its own records.

usage: outcome
       outcome --selftest
tags: outcome
net: none
secrets: none

What this routine exists to produce, as routine.yaml's `outcome:` declares it:
@@TABLE@@
THIS IS A STUB. It was scaffolded when the routine was created, from the `produces` lines it
was created with, so the first run records a READING instead of an outcome error (the engine
runs this script after every run's last turn and keeps only what it printed). It counts from
`@@RECORD@@` — one JSON object of measure ids to numbers — which no stage writes yet.

TODO, one per measure, for whoever makes this real: count each number from the routine's OWN
dated records and delete the line that reads it out of the placeholder file.
@@TODO@@
A measure `@@RECORD@@` does not carry is reported as 0 and named on stderr. For a `count` that
is the truth at birth; for a `level` it is a PLACEHOLDER, not a measurement — the engine
records a reading only when every declared measure is a number, so leaving one out would drop
the whole reading and record an error instead, which is the state this stub exists to end.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "@@RECORD@@"

#: measure id -> what it counts, in the words the routine was created with.
MEASURES = @@MEASURES@@


def read(path: Path) -> dict:
    """The routine's own record — {} when it is absent or unreadable, never an exception: a
    missing record is a routine that has not produced yet, not a failed measurement.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def measure(record: dict, log=None) -> dict:
    """One number per declared measure, in declaration order."""
    out: dict = {}
    for mid in MEASURES:
        value = record.get(mid)
        if isinstance(value, bool) or not isinstance(value, int | float):
            if value is not None and log:
                print(f"{mid}: {value!r} is not a number — reported as 0", file=log)
            out[mid] = 0
        else:
            out[mid] = value
    return out


def selftest() -> int:
    first = next(iter(MEASURES))
    assert set(measure({})) == set(MEASURES), measure({})
    assert all(v == 0 for v in measure({}).values()), measure({})
    assert measure({first: 4})[first] == 4
    assert measure({first: 1.5})[first] == 1.5
    assert measure({first: "lots"})[first] == 0       # a word is never coerced into the series
    assert measure({first: True})[first] == 0         # nor is a bool, which int() would take
    print("selftest ok")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    print(json.dumps(measure(read(RECORD), log=sys.stderr)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _oneline(text: str) -> str:
    """The person's own words on one line — what a label IS, wherever it is rendered."""
    return " ".join(str(text).split())


def _prose(text: str) -> str:
    """One line of the person's words, safe inside the GENERATED file's docstring.

    A backslash is escaped and a triple quote defused because this text is compiled as Python
    source: `produces` is the person's prose, and a label holding `\\d` would make the
    scaffolded script raise a SyntaxWarning at import — which this repo's own suite turns into
    an error — while one holding a triple quote would end the docstring early. The value the
    script REPORTS is never passed through here (`_oneline` is), so no label gains a visible
    escape.
    """
    return _oneline(text).replace("\\", "\\\\").replace('"""', "'''")


def _table(cfg: OutcomeConfig) -> str:
    """The declared measures as the stub's docstring lists them."""
    width = max(len(m.id) for m in cfg.measures)
    return "".join(f"  {m.id.ljust(width)}  {m.kind}, {m.better} is better — "
                   f"{_prose(m.label)}\n" for m in cfg.measures)


def _todo(cfg: OutcomeConfig) -> str:
    """The TODO block — what a REAL measurement of each number would read."""
    return "".join(f"  - `{m.id}`: {_prose(m.label)} — count it from the records this routine "
                   f"keeps, not from `{RECORD_REL}`.\n" for m in cfg.measures)


def render(cfg: OutcomeConfig) -> str:
    """The stub's source for the contract `cfg`, ready to write as `scripts/outcome.py`."""
    # `_oneline`, not `_prose`: this becomes a JSON literal, which escapes itself — passing it
    # through the docstring-safe form would put a visible `\\` in the person's own words.
    measures = {m.id: _oneline(m.label) for m in cfg.measures}
    return (_TEMPLATE
            .replace("@@PRINCIPAL@@", _prose(cfg.principal))
            .replace("@@TABLE@@", _table(cfg))
            .replace("@@TODO@@", _todo(cfg))
            .replace("@@MEASURES@@", json.dumps(measures, ensure_ascii=False, indent=4))
            .replace("@@RECORD@@", RECORD_REL))


def write(routine_dir: Path, cfg: OutcomeConfig) -> Path:
    """Write the stub into a freshly scaffolded routine and return its path."""
    path = Path(routine_dir) / "scripts" / f"{SCRIPT}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(cfg), encoding="utf-8")
    return path
