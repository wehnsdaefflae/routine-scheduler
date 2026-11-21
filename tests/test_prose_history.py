"""History in source prose, held by a shrink-only RATCHET like the size bar: dates, run ids and
version narration belong in the commit message and CHANGELOG, so a docstring or `#` comment under
`src/` may only lose them (CLAUDE.md Standards, "Writing standard").
"""

import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).with_name("prose_history.json")
UPDATE_COMMAND = "python tests/test_prose_history.py --update"
#: An ISO date (never a MIGRATION marker's expiry), a run id, a conversation id, version narration.
MARKER = re.compile(r"(?<!expires=)\b20\d\d-\d\d-\d\d\b|\b[a-z0-9][a-z0-9-]*:\d{8}-\d{6}\b"
                    r"|\bc-\d{8}-\d{6}\b|\b(?:before|since|until|after) 0\.\d+", re.IGNORECASE)


class RatchetRefusedError(Exception):
    """`--update` was asked to baseline new history or raise a ceiling."""


def prose(text: str) -> list[str]:
    """The docstrings and other bare string statements, plus every `#` comment — never a string
    literal the code uses."""
    strings = [n.value.value for n in ast.walk(ast.parse(text))
               if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
               and isinstance(n.value.value, str)]
    comments = [t.string for t in tokenize.generate_tokens(io.StringIO(text).readline)
                if t.type == tokenize.COMMENT]
    return strings + comments


def count(text: str) -> int:
    return sum(len(MARKER.findall(s)) for s in prose(text))


def measure(root: Path = ROOT) -> dict[str, int]:
    found = {}
    for path in sorted((root / "src").rglob("*.py")):
        if "__pycache__" not in path.parts and (n := count(path.read_text(encoding="utf-8"))):
            found[path.relative_to(root).as_posix()] = n
    return found


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        pytest.fail(f"{BASELINE_PATH.name} is missing — restore it from git; regenerating it "
                    f"from the tree would accept whatever history the tree holds today.")
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["markers"]


def violations(measured: dict[str, int], baseline: dict[str, int]) -> list[str]:
    found = []
    for rel in sorted(set(measured) | set(baseline)):
        now, ceiling = measured.get(rel, 0), baseline.get(rel, 0)
        if now > ceiling:
            found.append(f"{rel}: {now} history marker(s) against a ceiling of {ceiling} — move "
                         f"dates, run ids and version narration to the commit message/CHANGELOG.")
        elif now < ceiling:
            found.append(f"{rel}: down to {now} from {ceiling} — good; record it with "
                         f"`{UPDATE_COMMAND}`, or the slack is permission to re-grow.")
    return found


def tightened(measured: dict[str, int], baseline: dict[str, int]) -> dict[str, int]:
    for rel, now in sorted(measured.items()):
        if now > baseline.get(rel, 0):
            raise RatchetRefusedError(f"{rel}: {now} against {baseline.get(rel, 0)} — remove the "
                                      f"history; it is never baselined.")
    return {rel: now for rel, now in sorted(measured.items()) if now}


def test_src_prose_is_within_the_history_ratchet():
    found = violations(measure(), load_baseline())
    assert not found, "history in src prose:\n  " + "\n  ".join(found)


def test_markers_are_read_from_prose_only():
    text = ('"""Shipped 2026-11-21 (self-audit:20261121-003432)."""\n'
            "# since 0.400 this was c-20260712-222824\n"
            "STAMP = '2026-11-21'\n"
            "# MIGRATION(expires=2027-01-31): the converged shape\n")
    assert count(text) == 4


def test_new_or_grown_history_is_a_breach_and_a_shrink_must_be_recorded():
    assert violations({"src/a.py": 1}, {}) and violations({"src/a.py": 3}, {"src/a.py": 2})
    assert "down to 1" in violations({"src/a.py": 1}, {"src/a.py": 2})[0]
    assert "down to 0" in violations({}, {"src/gone.py": 2})[0]
    assert violations({"src/a.py": 2}, {"src/a.py": 2}) == []


def test_update_lowers_and_refuses_to_raise_or_baseline():
    assert tightened({"src/a.py": 1}, {"src/a.py": 2, "src/gone.py": 3}) == {"src/a.py": 1}
    for measured, baseline in (({"src/new.py": 1}, {}), ({"src/a.py": 3}, {"src/a.py": 2})):
        with pytest.raises(RatchetRefusedError):
            tightened(measured, baseline)


if __name__ == "__main__":
    if sys.argv[1:] != ["--update"]:
        sys.stderr.write(f"usage: {UPDATE_COMMAND}\n")
        raise SystemExit(2)
    try:
        markers = tightened(measure(), load_baseline())
    except RatchetRefusedError as refusal:
        sys.stderr.write(f"REFUSED: {refusal}\n")
        raise SystemExit(1) from None
    BASELINE_PATH.write_text(json.dumps({"markers": markers}, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"{BASELINE_PATH.name}: {sum(markers.values())} markers in "
                     f"{len(markers)} files\n")
