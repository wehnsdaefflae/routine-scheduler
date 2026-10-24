"""The PARSE GATE — a structured file may not stop parsing because of an edit (F460).

`edit_file` and `write_file` apply what the model hands them byte-faithfully, and nothing
checked the RESULT against the file's own format. On 2026-09-07 a degraded fallback model
(miz-grant-steward:20260907-054543, turn 75, reached by failover after the primary hit its
weekly limit) emitted a schema-VALID `edit_file` whose 11,848-character replacement was
half plausible JSON and half an echoed observation repeated with a stray CJK marker. The
edit applied, a 59 KB `state/shared-state.json` stopped parsing at line 771, and the routine
worked from a broken store until a human diagnosed it (R1342 → R1344). The same run broke
the same file a second time a few turns later.

The class is "a degraded model's output is trusted because it is well-FORMED" — the D87
storm guard counts only schema-INVALID replies, and a corrupted `replacement` is not one.
This closes the deterministic half: if the file parsed BEFORE and does not parse AFTER, the
write is refused and the file is untouched, so the run reads a diagnosable error on the turn
it made the mistake instead of the next reader finding rubble. The residue — an edit that
parses and is WRONG — is not checkable here and stays with F460 / the F363 distillate design.

Deliberately narrow:

- Only formats whose whole file is one document: `.json`, `.yaml`/`.yml`, `.toml`. `.jsonl`
  is excluded on purpose — a partial line is legal in an append-structured ledger.
- Only when the file PARSED BEFORE. Repairing an already-broken file is exactly what an edit
  is for, and a gate that refused the repair would be worse than the defect.
- Structured `write_file` content is exempt by construction (the engine serializes it), and
  so is `append` (the result is not one document).
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from pathlib import Path

import yaml

#: suffix → (parse callable, human name). One document per file, so the whole text is checked.
_PARSERS: dict[str, tuple[Callable[[str], object], str]] = {
    ".json": (json.loads, "JSON"),
    ".yaml": (yaml.safe_load, "YAML"),
    ".yml": (yaml.safe_load, "YAML"),
    ".toml": (tomllib.loads, "TOML"),
}


#: Suffixes whose file is ONE RECORD PER LINE. The parse gate skips them (a partial line is
#: legal mid-append), but the line boundary is then the file's entire structure — so an append
#: that does not end in a newline does not add a record, it EXTENDS the previous one.
_RECORD_PER_LINE = frozenset({".jsonl", ".ndjson"})


def checks(path: Path) -> bool:
    """Is this a format the gate checks? A caller reads the old text only when it is."""
    return path.suffix.lower() in _PARSERS


def record_per_line(path: Path) -> bool:
    """Is every LINE of this file one record? Then an appended record needs its separator.

    R2446/R2452, measured: three rows appended as strings with no trailing newline ran
    together — two of three releases landed on one line, `json.loads` died mid-file, and the
    routine that reads that hand-off could not tell "nothing shipped" from "the row was
    eaten". The engine serializes a STRUCTURED append and terminates it; a STRING body was
    applied byte-faithfully, which for this one family of formats means silent corruption.
    """
    return path.suffix.lower() in _RECORD_PER_LINE


def _problem(text: str, suffix: str) -> str | None:
    """The parse error for this text in that format, or None when it parses."""
    parse, _name = _PARSERS[suffix]
    try:
        parse(text)
    except (ValueError, yaml.YAMLError) as exc:
        return " ".join(str(exc).split())[:300]
    return None


def check_after(path: Path, before: str, after: str) -> str | None:
    """Why this write must be refused, or None to let it through.

    `before`/`after` are the file's whole text either side of the change. The message names
    the parser's own complaint (line and column, where it gives one) and what to do next,
    because the run has to be able to act on it in the same turn it made the mistake.
    """
    if not checks(path):
        return None
    suffix = path.suffix.lower()
    if _problem(before, suffix) is not None:
        return None                      # already broken — an edit may be the repair
    problem = _problem(after, suffix)
    if problem is None:
        return None
    _parse, name = _PARSERS[suffix]
    return (f"REFUSED — the result would not be valid {name}: {problem}. The file is "
            "UNCHANGED on disk. Re-read the region with read_file and fix the replacement; "
            "if your reply was truncated or garbled, write the smaller edit you actually "
            "meant rather than re-sending the same text.")


def unparsed(path: Path, text: str) -> dict[str, str]:
    """`{"unparsed": why}` when a body that should parse does not — else `{}` (F588).

    write_file reports the length of the payload it received, and Python's `write()` is
    all-or-nothing: bytes-written EQUALS that length whenever the write succeeded. So the
    numbers in the observation cannot distinguish a complete body from one the model's
    generation stopped inside — "report the bytes written" is, for a local file, the same
    number twice. The BODY can tell them apart, for a format whose whole point is to parse.

    Deliberately a NOTE, not a refusal. `check_after` above refuses what it can prove was
    good before; everything it exempts on purpose — a NEW file, a file already broken, an
    appended record — is exactly where a truncated generation lands, and a run that reads
    "this file does not parse" on the turn it wrote it can act on it in that turn.

    A whole-document format is judged ON DISK after the write, so an append to one and a
    repair that did not repair are both caught, and the verdict is a measurement of what
    landed rather than of what was handed in. A record-per-line file is judged on the body
    just written: each line stands alone, so re-reading a whole ledger per append buys
    nothing.
    """
    suffix = path.suffix.lower()
    if suffix in _RECORD_PER_LINE:
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip() and (bad := _problem(line, ".json")) is not None:
                return {"unparsed": f"has a record that does not parse as JSON (line {n} of "
                                    f"what was just written): {bad}"}
        return {}
    if suffix not in _PARSERS:
        return {}
    try:
        whole = path.read_text(encoding="utf-8")
    except OSError:                      # unreadable right after writing it: nothing to say
        return {}
    problem = _problem(whole, suffix)
    _parse, name = _PARSERS[suffix]
    return {"unparsed": f"does not parse as {name}: {problem}"} if problem else {}
