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
    suffix = path.suffix.lower()
    if suffix not in _PARSERS:
        return None
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
