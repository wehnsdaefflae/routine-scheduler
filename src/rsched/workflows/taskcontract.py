"""A contract names only the task (P2) — the checks no generation prompt can guarantee.

A routine's CONTRACT is what its documents hold it to: a library pattern's `DONE_WHEN`, a
recipe's `## Done when` and the steps both describe. Every finish accounts for each Done-when
line (`engine/accounting.py`), so a line naming the run's OWN RECORD — a LEDGER entry, a phase
write, a memory note, what the finish summary must say — buys a run turns of bookkeeping that
deliver nothing to anyone. Measured 2026-11-06 over 24 routines' last runs: twelve spent more
turns orienting on and recording themselves than on their task, and ~60% of the fleet's
Done-when lines named a record. The engine owns that record (the finish summary is filed by it,
the stage cursor is stamped when a run reads `stages/<name>.md`, a `note` side field files
notes at no turn cost), so a contract that also asks the run for it asks twice.

The CAUSE is the generator, so the rule lives first in the prompts that write these documents
(`PATTERN_CONTRACT` for `generate.FORMAT_SPEC`, `RECIPE_CONTRACT` for the materialization
prompts). What is checked here is the part a machine can be SURE of, with a fixed vocabulary:

- `process_only(outcome)` — a Done-when outcome EVERY clause of which names the run's own
  record (`PROCESS_OBJECTS`). Judged per clause because a product line often mentions a record
  in passing ("each ride's key is in state/processed_rides.json and LEDGER.md has one entry") —
  that line still names a task outcome, and its ledger clause is `ledger_instructions`' to
  catch. The vocabulary names the RUN's record only: `LEDGER` in capitals (the file's name) or
  a "ledger entry" — never a lower-case "the ledger", which in live recipes is as often a
  product record a routine keeps for its task (an evaluation ledger's rows) — the work log,
  `phase.json`, `.memory/` and notes, the finish summary or report, a cursor or high-water mark
  as such. A `state/` file is NOT in it: a
  watchlist, a manifest, a processed-ids file is the product or a cursor the task needs, and no
  word list can tell those apart from bookkeeping. The routine's OUTCOME contract line (the one
  naming `scripts/outcome.py`) is the task by definition and always counts as a task clause.
- `ledger_instructions(text)` — a sentence telling a run to WRITE to the ledger or rotate it:
  a ledger token beside a write verb. Reading what the engine filed is not an instruction to
  write, so a sentence with a ledger token and no write verb passes.

Both are lint ERRORS: `lint.py` fails a library pattern on them (so `generate` repairs or
refuses the draft), the materialization pipeline refuses a main/stage carrying them (one retry,
then the verbatim pattern, which the library lint has already held), and `recipelint` reports
them as FAIL rows that turn `rsched validate` red — every other recipe note is a phrasing
report, these are a contract defect the operator has called critical.
"""

from __future__ import annotations

import re

#: What the run's OWN record is called. Each entry `(label, pattern)`; the label names the
#: object in the finding. Case matters for `LEDGER`/`WORKLOG` (the file names) and nowhere else.
PROCESS_OBJECTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a LEDGER entry", re.compile(r"\bLEDGER\b|(?i:\bledger[- ]entr(?:y|ies)\b)")),
    ("a ledger rotation", re.compile(r"(?i)\brotat(?:e|ed|es|ing|ion)\b")),
    ("a work-log entry", re.compile(r"\bWORKLOG\b|(?i:\bwork[- ]?log entr(?:y|ies)\b)")),
    ("a phase write", re.compile(r"(?i)\bphase\.json\b")),
    ("a memory or notes write", re.compile(
        r"(?i)\.memory/|\bmemory (?:note|notes|entry|entries|index)\b|\bnotes\.md\b")),
    ("the finish summary's contents", re.compile(
        r"(?i)\b(?:finish|finishing|closing|final|run.s)\s+(?:summary|report)\b"
        r"|\bthe summary\b|\bsummary's\b|\*\*(?:process|work):?\*\*"
        r"|abschlusszusammenfassung")),
    ("a cursor advance", re.compile(r"(?i)\bcursor\b|\bhigh-water mark\b|\bwatermark\b")),
    ("the run's own record", re.compile(
        r"(?i)\brecords? the run\b|\b(?:the|this) run.s (?:record|row|entry)\b")),
)

#: The routine's outcome contract (`engine/outcome_words.done_when_line`) — a task line always.
_OUTCOME = re.compile(r"scripts/outcome\.py")
#: Where one deliverable may end and the next begin. English and German (one live recipe is
#: written in German, and its contract is held to the same rule).
_CLAUSE = re.compile(r";|,?\s+(?:and|or|und|oder|but|then)\s+|,\s+|\s+—\s+|:\s+")
_PAREN = re.compile(r"\([^()]*\)")
#: A segment is a clause of its OWN — a second deliverable — only when it opens like a subject
#: and carries a finite verb ("every audit pass ran over every repo"). Anything else continues
#: its neighbour: "the finish report names every pass, what shipped, and every failure" is ONE
#: deliverable (the report) whose object is a list, and a list item is never a task clause.
_SUBJECT = re.compile(
    r"(?i)^(?:the|a|an|every|each|all|any|no|its|this|that|his|her|their|one|exactly|both"
    r"|der|die|das|jede[rsn]?|ein(?:e|en)?|`|state/|[\w.-]+\.(?:md|json|jsonl|ya?ml))\b")
_FINITE = re.compile(
    r"(?i)\b(?:is|are|was|were|has|have|had|got|gets|ran|runs|exists?|holds?|stands?|lands?"
    r"|landed|went|goes|carries|names|leads|says|reads|matches|reflects?|passe[sd]|reached"
    r"|ist|sind|liegt|liegen|steht|stehen|hat|haben|wurde|wurden)\b")
#: A segment this short is a dangling verb or qualifier of its neighbour ("and stated").
_MIN_CLAUSE_WORDS = 3


def _segments(outcome: str) -> list[str]:
    out: list[str] = []
    for part in _CLAUSE.split(_PAREN.sub(" ", outcome)):
        part = part.strip(" .,`*")
        if not part:
            continue
        if out and len(part.split()) < _MIN_CLAUSE_WORDS:
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


def _object(text: str) -> str | None:
    """The run's-record object a piece of text names, or None (the outcome line is a task)."""
    if _OUTCOME.search(text):
        return None
    return next((label for label, rx in PROCESS_OBJECTS if rx.search(text)), None)


def clauses(outcome: str) -> list[tuple[str, str | None]]:
    """`(clause, record it names or None)` per deliverable of one Done-when outcome."""
    out: list[tuple[str, str | None]] = []
    for seg in _segments(outcome):
        label = _object(seg)
        own = not out or label is not None or (_SUBJECT.search(seg) and _FINITE.search(seg))
        if own:
            out.append((seg, label))
        else:
            prev, prev_label = out[-1]
            out[-1] = (f"{prev}, {seg}", prev_label)
    return out


def process_only(outcome: str) -> str | None:
    """The record a Done-when outcome names when EVERY clause of it is the run's own record,
    else None. The label is the first clause's, for the finding's wording.
    """
    found = clauses(outcome)
    if not found or any(label is None for _, label in found):
        return None
    return found[0][1]


def names_a_record(outcome: str) -> str | None:
    """The first run's-record object ANY clause of the outcome names, else None."""
    return next((label for _, label in clauses(outcome) if label), None)


_LEDGER = PROCESS_OBJECTS[0][1]
_NOT_THE_RUN_LEDGER = re.compile(r"(?i)\bledger(?:\.py|\s+(?:tool|script|register|file of))\b")
_WRITE = re.compile(
    r"(?i)\b(?:append\w*|writ(?:e|es|ten|ing)|wrote|add(?:s|ed|ing)?|record(?:s|ed|ing)?"
    r"|log(?:s|ged|ging)?|rotat\w*|archiv\w*|trim\w*|prun\w*|updat\w*)\b"
    r"|\b(?:one|exactly one|a single|this run's|the run's)\s+(?:\w+\s+)?"
    r"(?:ledger(?:\.md)?\s+)?entr(?:y|ies)\b")
_NEGATED = re.compile(r"(?i)\b(?:never|not|do not|don't|must not)\b[^.]{0,60}?\bledger")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*`\"(])")
_ITEM = re.compile(r"^(?:[-*+]|\d+[.)]|#+)\s")


def _sentences(text: str) -> list[tuple[int, str]]:
    """`(first line, sentence)` — a paragraph's wrapped lines are joined so a wrap cannot hide
    a sentence, and every list item or heading starts a sentence of its own.
    """
    out: list[tuple[int, str]] = []
    start = 0
    buf: list[str] = []
    for n, raw in enumerate([*(text or "").splitlines(), ""], start=1):
        line = raw.strip()
        if buf and (not line or _ITEM.match(line)):
            out.extend((start, s) for s in _SENTENCE.split(" ".join(buf)))
            buf = []
        if line:
            start = start if buf else n
            buf.append(line)
    return out


def ledger_instructions(text: str) -> list[tuple[int, str]]:
    """`(line, sentence)` per sentence of `text` that tells a run to write to or rotate the
    ledger: a ledger token beside a write verb, not negated, and not a product tool that merely
    shares the word.
    """
    return [(n, s) for n, s in _sentences(text)
            if _LEDGER.search(_NOT_THE_RUN_LEDGER.sub(" ", s)) and _WRITE.search(s)
            and not _NEGATED.search(s)]


def done_when_findings(lines: list[tuple[str, str]]) -> list[str]:
    """`[(id, outcome)]` → one sentence per process-only line, naming it and its record."""
    return [f"Done when {lid}: names only {label}, not a task outcome — the engine keeps the "
            f"run's record; say what the WORK produced or established ({text[:70]!r})"
            for lid, text in lines if (label := process_only(text))]


def ledger_findings(text: str) -> list[str]:
    """One sentence per ledger-write instruction in `text`."""
    return [f"line {n}: tells the run to write or rotate the ledger, which the engine writes "
            f"from the finish summary — drop the instruction ({s[:70]!r})"
            for n, s in ledger_instructions(text)]


_DONE_LINE = re.compile(r"^d(\d+) · [a-z_][a-z0-9_]* — (\S.*)$")
#: The dummy module a pattern imports its state helpers from (`routine.state`).
_STATE_MODULE = "routine.state"


def pattern_problems(source: str, meta: dict, filename: str) -> list[str]:
    """A library pattern's contract: DONE_WHEN in shape, every line a TASK outcome, and no
    ledger helper or ledger-write instruction anywhere in the file.

    Stricter than a recipe on one point: a pattern is the SKELETON every recipe decomposed from
    it starts from, so a DONE_WHEN line naming the run's record in ANY clause is an error here
    (a recipe's mixed line is a note — it may carry the person's own words). A harness (tagged
    `meta`) serves a person in the loop and owes no DONE_WHEN; it still owes no bookkeeping.
    """
    out = [f"{filename}: imports `ledger` from {_STATE_MODULE} — the engine writes the run's "
           "ledger from its finish summary; drop the import and every call to it"
           for name in _state_imports(source) if name == "ledger"]
    out += [f"{filename}: {f}" for f in ledger_findings(source)]
    if "meta" in (meta.get("tags") or []):
        return out
    lines = [str(x) for x in meta.get("done_when") or []]
    if not lines:
        return [*out, f"{filename}: missing DONE_WHEN (what one finished run leaves behind)"]
    parsed = [(line, _DONE_LINE.match(line)) for line in lines]
    out += [f'{filename}: DONE_WHEN line {line[:50]!r} is not "d<n> · <step> — <outcome>"'
            for line, m in parsed if not m]
    ids = [int(m[1]) for _, m in parsed if m]
    if ids != list(range(1, len(ids) + 1)):
        out.append(f"{filename}: DONE_WHEN ids run d1, d2, … in order")
    out += [f"{filename}: DONE_WHEN d{m[1]} names {label}, not a task outcome — the engine "
            f"keeps the run's record; say what the WORK produced or established"
            for _, m in parsed if m and (label := names_a_record(m[2]))]
    return out


def _state_imports(source: str) -> list[str]:
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [alias.name for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == _STATE_MODULE
            for alias in node.names]


_CORE = """\
The run's PROCESS RECORD is the ENGINE's, never the run's: the engine files each run's account \
from its finish summary into LEDGER.md (sealed — no run writes or rotates it), marks the run's \
stage when it reads a stage module, and files the `note` side field to the run's notes at no \
turn cost. So:"""

#: Spliced into `generate.FORMAT_SPEC` — the pattern author's form of the rule.
PATTERN_CONTRACT = _CORE + """
- No step whose job is recording the run: no record/log/bookkeeping step, no ledger write, no \
ledger rotation, no phase write between steps, no orient step that reads the whole ledger (the \
state digest already carries what the last run left).
- A state/ file holds the PRODUCT, or a cross-run cursor the TASK needs (what was already seen, \
the lifecycle phase). A state update a run repeats the same way every run is a `script` call, \
never a field-by-field edit.
- Import `phase` only when PHASES has more than one entry, and set it only at a cross-run \
lifecycle milestone.
- Every DONE_WHEN line names a TASK outcome — what the work produced or established. Never a \
ledger entry, a state/phase/memory/notes write, a cursor advance as such, or what the finish \
summary must contain."""

#: Spliced into the materialization prompts — the recipe author's form of the rule.
RECIPE_CONTRACT = _CORE + """
- No stage, and no step of one, whose job is recording the run: never tell a run to append to \
or rotate LEDGER.md, keep a work log, or write state/phase.json to mark which stage it is in. \
No orient step that reads the whole ledger — the state digest carries what the last run left.
- A run writes state/ only for the PRODUCT, or for a cross-run cursor the TASK needs (what was \
already seen, a lifecycle phase that changes across runs). A state update the run repeats the \
same way every run is a call to one of the routine's own scripts, never a field-by-field edit.
- Every `## Done when` line names a TASK outcome — what the work produced or established. Never \
a ledger entry, a state/phase/memory/notes write, a cursor advance as such, or what the finish \
summary must contain: the run accounts for every line at its finish, so a line about its own \
record buys bookkeeping and delivers nothing."""
