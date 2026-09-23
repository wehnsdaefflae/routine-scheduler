"""Recipe hygiene — the cruft and self-contradiction a hand-edited recipe accumulates (F516).

`lint.py` gates what the LIBRARY holds. This reads the other half: a routine's OWN
materialized recipe — `main.md` plus `stages/*.md` — after runs have been editing it.
A recipe is revised in place, one anchor at a time, by the routine itself (a revise leg)
and by routine-improver; nothing in the write path ever reads the finished document back.
R1710 is what that costs: a run spent eleven turns editing its own recipe, was asked twice
by the operator whether cruft remained, re-read the whole file and found three pieces.

What is checked is what a machine can be SURE of — a reference with nothing behind it, a
module nothing routes to, a step the routine's capabilities cannot perform, a block an
edit left standing beside its own replacement. Semantic contradiction past that is
routine-improver's periodic lens, which already hunts it with a model and a whole run's
budget; a regex has no business guessing at it.

Every row is a REPORT and never a verdict. A recipe is a hand-tuned document written by
the person and the run that own it: a check that failed `rsched validate` over a phrasing
would be switched off within a week, and the defects here are all cheap to read and
sometimes deliberate. So `cmd_validate` prints these and leaves the exit code alone.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import frontmatter

from ..grants import GATED_KINDS
from ..readmodels.statemap import STAGES_DIR

#: Below this many characters a paragraph is a heading, a list stub or a one-line rule —
#: too short for near-identity to mean anything but coincidence.
MIN_BLOCK = 100
#: How alike two blocks of one file must be to read as an edit that left the old one standing.
#: Measured over the 35 live recipes: no pair reaches it, so today the check is a floor and
#: not a source of noise.
RESTATED_RATIO = 0.85

_STAGE_REF = re.compile(r"stages/([A-Za-z0-9._-]+)\.md")
_SCRIPT_REF = re.compile(r"scripts/([A-Za-z0-9_-]+)\.py")
#: An action kind a recipe names the way recipes name kinds — in backticks. Bare words are
#: prose ("the filter script", "a shell-driven agent") and matching them is pure noise.
_KIND_REF = re.compile(r"`([a-z_]+)`")


def recipe_files(routine_dir: Path) -> dict[str, str]:
    """`{"main.md": body, "stages/<x>.md": body, …}` — the recipe, frontmatter stripped.

    Frontmatter is provenance (the pattern a recipe was materialized from) and carries an
    ALPHABETICAL module list, so leaving it in would make every stage look routed-to.
    """
    out: dict[str, str] = {}
    for path in [routine_dir / "main.md", *sorted((routine_dir / STAGES_DIR).glob("*.md"))]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = path.name if path.parent == routine_dir else f"{STAGES_DIR}/{path.name}"
        out[rel] = frontmatter.loads(text).content
    return out


def recipe_notes(routine_dir: Path, capabilities: dict | None = None) -> list[str]:
    """What this routine's recipe says that its own files and config do not bear out.

    One `<file>: <what is wrong> — <what settles it>` line per finding, empty for a clean
    recipe (which is most of them). Never raises and never asks a question it cannot
    answer from the routine dir alone: `rsched validate` walks every routine, and a check
    that needed the library or the network would make the command an outage away from red.
    """
    files = recipe_files(routine_dir)
    if "main.md" not in files:
        return []
    return [*_stage_routing(files),
            *_missing_scripts(routine_dir, files),
            *_ungranted_kinds(files, capabilities),
            *_restated_blocks(files)]


def _stage_routing(files: dict[str, str]) -> list[str]:
    """Both directions of the stage map: a module nothing routes to, a route to no module.

    A stage is ROUTED-TO when any OTHER recipe file names it — `main.md`'s run-flow list is
    the usual way, but a sibling stage handing over directly (routine-improver's lenses)
    counts the same. Word-ish boundaries keep a short stem from matching inside a longer one.
    """
    stems = [rel.removeprefix(f"{STAGES_DIR}/").removesuffix(".md")
             for rel in files if rel != "main.md"]
    notes: list[str] = []
    for rel, body in files.items():
        notes.extend(f"{rel}: routes to `{STAGES_DIR}/{ref}.md`, which does not exist — "
                     f"write the module or drop the step"
                     for ref in sorted(set(_STAGE_REF.findall(body))) if ref not in stems)
    for stem in stems:
        elsewhere = "\n".join(body for rel, body in files.items()
                              if rel != f"{STAGES_DIR}/{stem}.md")
        if not re.search(rf"(?<![a-z0-9-]){re.escape(stem.lower())}(?![a-z0-9-])",
                         elsewhere.lower()):
            notes.append(f"{STAGES_DIR}/{stem}.md: no other recipe file names this stage — "
                         f"nothing routes to it, so no run will read it")
    return notes


def _missing_scripts(routine_dir: Path, files: dict[str, str]) -> list[str]:
    """A step calling `scripts/<name>.py` the routine does not have.

    Only asked of a routine that HAS a `scripts/` dir: one without it has no script tooling
    at all, and a `scripts/…` path in its prose belongs to some project it works inside.
    """
    if not (routine_dir / "scripts").is_dir():
        return []
    notes: list[str] = []
    for rel, body in files.items():
        notes.extend(f"{rel}: names `scripts/{name}.py`, which this routine does not have — "
                     f"write it, or drop the step that calls it"
                     for name in sorted(set(_SCRIPT_REF.findall(body)))
                     if not (routine_dir / "scripts" / f"{name}.py").is_file())
    return notes


def _ungranted_kinds(files: dict[str, str], capabilities: dict | None) -> list[str]:
    """A step naming a gated action kind the routine's capabilities do not enable.

    The two halves are owned apart — the user edits `routine.yaml`, the routine and its
    improver edit the recipe — so a capability switched off leaves the step that used it
    standing, and the run reads an instruction it will be denied the moment it follows it.
    """
    held = set((capabilities or {}).get("actions") or [])
    notes: list[str] = []
    for rel, body in files.items():
        notes.extend(f"{rel}: names the `{kind}` action, which this routine's capabilities do "
                     f"not enable — enable it, or drop the step"
                     for kind in sorted({k for k in _KIND_REF.findall(body)
                                         if k in GATED_KINDS and k not in held}))
    return notes


def _blocks(body: str) -> list[tuple[int, str]]:
    """`(line number, whitespace-normalized text)` per paragraph worth comparing."""
    out: list[tuple[int, str]] = []
    start = 0
    buf: list[str] = []
    for n, line in enumerate(body.splitlines(), start=1):
        if line.strip():
            if not buf:
                start = n
            buf.append(line)
            continue
        if buf:
            out.append((start, " ".join(" ".join(buf).split())))
            buf = []
    if buf:
        out.append((start, " ".join(" ".join(buf).split())))
    return [(n, text) for n, text in out if len(text) >= MIN_BLOCK]


def _restated_blocks(files: dict[str, str]) -> list[str]:
    """Two blocks of ONE file that say nearly the same thing — an edit that inserted its
    replacement and left the original standing, which is how a document comes to contradict
    itself without any line of it being wrong.

    Within one file only. Across files near-identity is the normal shape: `main.md` names
    what each stage does and the stage then says it again in full, and reporting that pair
    would bury the real ones.
    """
    notes: list[str] = []
    for rel, body in files.items():
        blocks = _blocks(body)
        reported: set[int] = set()
        for i, (line_a, a) in enumerate(blocks):
            if i in reported:
                continue
            for j in range(i + 1, len(blocks)):
                line_b, b = blocks[j]
                if j in reported or abs(len(a) - len(b)) > max(40, 0.3 * len(a)):
                    continue
                matcher = difflib.SequenceMatcher(None, a, b)
                if matcher.quick_ratio() < RESTATED_RATIO or matcher.ratio() < RESTATED_RATIO:
                    continue
                reported.update({i, j})
                notes.append(f"{rel}: the blocks at lines {line_a} and {line_b} say nearly the "
                             f"same thing — keep one: {a[:70]!r}…")
                break
    return notes
