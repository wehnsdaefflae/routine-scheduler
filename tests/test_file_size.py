"""The ≤350-line house bar, enforced as a RATCHET — prose that nothing checked becomes a gate.

CLAUDE.md's Standards have said "One responsibility per file, ≤ ~350 lines. Split rather than
grow" for the life of the repo, and nothing ever read a file's length: `tests/test_quality.py`
runs ruff, mypy and vulture, none of which can see a line count. So the bar was advice, and the
tree drifted — the count and every ceiling are in `size_ratchet.json` beside this file.

Two shapes were rejected before this one:

* A FLAT check (`every file ≤ 350`) can never go green on this tree, and a gate that is red on
  arrival is a gate someone deletes.
* An ALLOWLIST of "known big files" excuses exactly the files it names — and the two the finding
  named (`engine/background.py`, `daemon/runner.py`) were written by the same routine that would
  maintain the allowlist. A standard you may exempt yourself from is not a standard.

A RATCHET is what this repo already does where a standard arrived after the code did: pyproject's
`mccabe max-complexity`, the `pylint max-args` family and coverage's `fail_under` each read
"ratchet to current worst; lower over time". This is that, per file, with three rules:

1. **A file at or under the bar may never cross it.** New drift stops today. The baseline cannot
   be extended to cover a newly-over-bar file — `--update` refuses, by construction.
2. **A file already over the bar may never grow.** Its current length is its ceiling. An addition
   belongs in a new file, which is what "split rather than grow" means.
3. **Shrinking is one-way.** A file that comes back under the bar loses its entry and can never
   be over it again; a file that merely shrinks tightens its ceiling to the new count. Without
   this, a file could shrink by 40 lines and silently re-grow by 40 later.

Rule 3 is why a shrink is itself a (loud, one-command) failure: a ceiling nobody tightens becomes
permission to re-grow, and that is the hole the whole ratchet exists to close.

WHAT IS GOVERNED: the code this repo ships and the tests that gate it — `src/**/*.py`,
`tests/**/*.py`, `static/**/*.js`, `static/**/*.css`. Deliberately outside:

* `library-seed/` and `util-seed/` — a workflow depiction and a PEP 723 util ARE one file each, by
  contract; they cannot be split and ruff excludes them for the same reason.
* `docs/**` and the Markdown surfaces — a document's length is an editorial question, not a
  responsibility boundary. `docs/architecture.md` is long because the architecture is.

A line is what a human counts (`str.splitlines`), which equals `wc -l` for every file here since
all end in a newline; a final unterminated line still counts as a line.
"""

import json
import sys
from pathlib import Path

import pytest

BAR = 350
ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).with_name("size_ratchet.json")
UPDATE_COMMAND = "python tests/test_file_size.py --update"

#: (directory, glob) pairs the bar governs — see the module docstring for what is outside it.
COVERED: tuple[tuple[str, str], ...] = (
    ("src", "*.py"),
    ("tests", "*.py"),
    ("static", "*.js"),
    ("static", "*.css"),
)
SKIP_PARTS = frozenset({"__pycache__", ".pytest_cache", "node_modules", ".venv"})


class RatchetRefusedError(Exception):
    """`--update` was asked to launder a violation. Ceilings only ever go down."""


def line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="surrogateescape").splitlines())


def measure(root: Path = ROOT) -> dict[str, int]:
    """Every governed file's length, keyed by its repo-relative POSIX path."""
    sizes: dict[str, int] = {}
    for sub, pattern in COVERED:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob(pattern)):
            rel = path.relative_to(root)
            if not path.is_file() or SKIP_PARTS.intersection(rel.parts):
                continue
            sizes[rel.as_posix()] = line_count(path)
    return sizes


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, int]:
    if not path.exists():
        # Regenerating it from the tree would baseline every current violation, so there is no
        # such command: the file is history and belongs in git.
        pytest.fail(f"{path.name} is missing — the size ratchet has no ceilings to enforce. "
                    f"Restore it from git; it cannot be regenerated from the tree, because that "
                    f"would silently accept whatever the tree looks like today.")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(rel): int(lines) for rel, lines in raw["over_the_bar"].items()}


def violations(measured: dict[str, int], baseline: dict[str, int]) -> list[str]:
    """One sentence per breach of the three rules — the number, the delta, and what to do."""
    found: list[str] = []
    for rel, lines in sorted(measured.items()):
        ceiling = baseline.get(rel)
        if ceiling is None:
            if lines > BAR:
                found.append(
                    f"{rel} CROSSED the bar: {lines} lines (> {BAR}). Split it along a "
                    f"responsibility it already has — the ratchet cannot be extended to cover a "
                    f"new file, and {UPDATE_COMMAND} refuses to try.")
        elif lines > ceiling:
            found.append(
                f"{rel} GREW: {lines} lines against a ceiling of {ceiling} (+{lines - ceiling}). "
                f"A file over the bar may only shrink — put the addition in its own file.")
        elif lines < ceiling and lines <= BAR:
            found.append(
                f"{rel} is back UNDER the bar at {lines} lines (ceiling was {ceiling}) — good. "
                f"Drop its entry with `{UPDATE_COMMAND}`; it can never be over the bar again.")
        elif lines < ceiling:
            found.append(
                f"{rel} SHRANK to {lines} lines (ceiling {ceiling}) — good. Tighten the ceiling "
                f"with `{UPDATE_COMMAND}`, or the {ceiling - lines} lines it lost become "
                f"permission to re-grow.")
    found.extend(
        f"{rel} has a ceiling of {baseline[rel]} and no longer exists (deleted, renamed or "
        f"moved out of the governed set). Run `{UPDATE_COMMAND}`."
        for rel in sorted(set(baseline) - set(measured)))
    return found


def tightened(measured: dict[str, int], baseline: dict[str, int]) -> dict[str, int]:
    """The next baseline: every ceiling lowered to what the tree now is, nothing raised, nothing
    added. It REFUSES rather than launder a violation — that refusal is what makes the ratchet
    more than a record of whatever the tree happens to look like."""
    for rel, lines in sorted(measured.items()):
        ceiling = baseline.get(rel)
        if ceiling is None and lines > BAR:
            raise RatchetRefusedError(
                f"{rel} is {lines} lines and has no ceiling. A file that crossed the bar is a "
                f"file to split, not to baseline.")
        if ceiling is not None and lines > ceiling:
            raise RatchetRefusedError(
                f"{rel} is {lines} lines against a ceiling of {ceiling}. Growth is never "
                f"baselined; shrink the file or move the addition out.")
    return {rel: min(ceiling, measured[rel])
            for rel, ceiling in sorted(baseline.items())
            if rel in measured and measured[rel] > BAR}


# ---- the gate ----------------------------------------------------------------------------

def test_the_tree_is_within_the_size_ratchet():
    found = violations(measure(), load_baseline())
    assert not found, (
        f"the ≤{BAR}-line house bar (CLAUDE.md Standards), {len(found)} breach(es):\n  "
        + "\n  ".join(found))


def test_the_baseline_declares_the_bar_this_gate_enforces():
    """Two numbers for one bar is how a gate starts enforcing something nobody decided."""
    raw = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert raw["bar"] == BAR


def test_every_ceiling_is_over_the_bar():
    """An entry at or under the bar would be a file exempted from rule 1 for good."""
    baseline = load_baseline()
    assert [rel for rel, lines in baseline.items() if lines <= BAR] == []


def test_this_gate_is_itself_under_the_bar():
    assert line_count(Path(__file__)) <= BAR


# ---- the three rules, on synthetic trees -------------------------------------------------

def test_a_file_crossing_the_bar_is_a_breach():
    found = violations({"src/new.py": BAR + 1}, {})
    assert len(found) == 1
    assert "CROSSED" in found[0]
    assert violations({"src/new.py": BAR}, {}) == []


def test_an_over_bar_file_may_not_grow():
    found = violations({"src/big.py": 501}, {"src/big.py": 500})
    assert len(found) == 1
    assert "GREW" in found[0] and "+1" in found[0]
    assert violations({"src/big.py": 500}, {"src/big.py": 500}) == []


def test_a_shrunk_file_must_tighten_its_ceiling():
    found = violations({"src/big.py": 499}, {"src/big.py": 500})
    assert len(found) == 1
    assert "SHRANK" in found[0] and UPDATE_COMMAND in found[0]


def test_a_file_back_under_the_bar_must_lose_its_entry():
    found = violations({"src/big.py": BAR}, {"src/big.py": 500})
    assert len(found) == 1
    assert "back UNDER" in found[0]


def test_a_ceiling_for_a_vanished_file_is_a_breach():
    found = violations({}, {"src/gone.py": 500})
    assert len(found) == 1
    assert "no longer exists" in found[0]


# ---- the updater cannot launder a violation ----------------------------------------------

def test_update_refuses_to_baseline_a_file_that_crossed_the_bar():
    with pytest.raises(RatchetRefusedError, match="file to split"):
        tightened({"src/new.py": BAR + 1}, {})


def test_update_refuses_to_raise_a_ceiling():
    with pytest.raises(RatchetRefusedError, match="never baselined"):
        tightened({"src/big.py": 501}, {"src/big.py": 500})


def test_update_lowers_ceilings_drops_what_came_under_and_forgets_what_is_gone():
    out = tightened({"src/a.py": 400, "src/b.py": BAR, "src/c.py": 10},
                    {"src/a.py": 500, "src/b.py": 500, "src/gone.py": 500})
    assert out == {"src/a.py": 400}


if __name__ == "__main__":
    if sys.argv[1:] != ["--update"]:
        sys.stderr.write(f"usage: {UPDATE_COMMAND}\n")
        raise SystemExit(2)
    document = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    try:
        document["over_the_bar"] = tightened(measure(), load_baseline())
    except RatchetRefusedError as refusal:
        sys.stderr.write(f"REFUSED: {refusal}\n")
        raise SystemExit(1) from None
    BASELINE_PATH.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"{BASELINE_PATH.name}: {len(document['over_the_bar'])} files over the "
                     f"{BAR}-line bar\n")
