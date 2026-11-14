"""The seven classes of thing that might need the operator's intervention (D289).

HIS WORDS, 2026-11-12, quoted whole in R3365: operator-facing text *"must focus on things that
might require my intervention. this includes (a) context for things i might need to decide
(b) unexpected events. they might be an indication that my intervention is required (c) any
form of outgoing or incoming communication, especially personal (d) reached goals (e) blocks
that the system cannot resolve itself (f) methodological or strategic pivots (g) confirmed
dead-ends"*. HIS ANSWER, 2026-11-14: *"Lead with the seven classes as a labelled block at the
top, narrative below it"*.

WHO DECIDES A ROW'S CLASS — the sub-question handed over with the decision, answered here and
MEASURED on this instance (2026-11-14) rather than chosen:

  (a) 529 of 623 question records carry explicit `options`             → DERIVED
  (b) 2,618 health rows across 36 `health_vocab.EVENTS` types          → DERIVED
  (c) a reserved outward util is gated and `unknownoutcome` knows an outward act, but no
      stream records that one HAPPENED                                 → MARKED
  (d) 146 `status.json` files carry goals with `met` and `evidence`    → DERIVED
  (e) `BLOCKED_EVENTS` → `operator_blocks.annotate`, plus a `partial` finish, which by its own
      contract means a blocker the run could not clear                 → DERIVED
  (f) no field, no event, no store — prose only                        → MARKED
  (g) no field, no event, no store — prose only                        → MARKED

So it is NEITHER of the two options as posed. The renderer DERIVES what the engine already
records and the engine asks a run to MARK only what it cannot derive, for two reasons that pull
in the same direction:

* Classifying a derived class from prose would replace a FACT with a GUESS. `operator_blocks`
  already knows which blocks only he can clear; a heuristic that disagreed with it would be
  wrong in a way no surface could show.
* Letting a run mark a derived class would make the label a run's OPINION OF ITS OWN WORK — on
  exactly the surfaces that exist because a run's prose does not say what needs a person. So a
  mark in a derived class is DROPPED, not merged: `_marked` says so in one line, and the test
  file holds the specimen (a run marking its own goal as reached).

WHAT A ROW CARRIES is set by his requirement 1, quoted in the same message: *"they must be
self-contained. they cannot have me hunting for content in other documents … worst case scenario
is content i need to know about being referenced only by an identifier."* So every row carries
`text` (the thing itself) and `detail` (the content he would otherwise have to go and find) —
an ask's options, a met goal's evidence, a partial finish's own summary.

ORDER IS HIS ORDER, (a) through (g). He wrote the list in that order and will read the block in
it; sorting by severity would be a second judgement nobody asked for.

This module is the CLASSIFICATION only, the way `operator_blocks` holds "who can clear it" and
`blocked_kinds` holds "in what unit": one judgement per module, no rendering. The labelled
block's layout is the next increment and reads this.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: his seven, in his order. `source` names where a row comes from, so a label can be checked.
CLASSES: tuple[dict, ...] = (
    {"id": "a", "label": "context for something to decide",
     "source": "questions/pending (an ask this run filed, with its options)"},
    {"id": "b", "label": "an unexpected event",
     "source": "health_vocab.EVENTS via .control/health-events.jsonl"},
    {"id": "c", "label": "outgoing or incoming communication",
     "source": "marked by the run — no stream records that a message went out"},
    {"id": "d", "label": "a reached goal",
     "source": "status.json goals[] with status=met, and its evidence"},
    {"id": "e", "label": "a block the system cannot resolve itself",
     "source": "a partial finish (its own contract), and operator_blocks.annotate"},
    {"id": "f", "label": "a methodological or strategic pivot",
     "source": "marked by the run — nothing records one"},
    {"id": "g", "label": "a confirmed dead-end",
     "source": "marked by the run — nothing records one"},
)

#: the four the engine already records: a mark in one of these is dropped (see the docstring).
DERIVED = ("a", "b", "d", "e")

#: the three it cannot derive: only here may a run say what class its finding is.
MARKED = ("c", "f", "g")

_ORDER = {c["id"]: i for i, c in enumerate(CLASSES)}

#: `q-<run ts>-<turn>` — how an ask is attributed to the run that filed it, with no new field.
_QID = re.compile(r"^q-(\d{8}-\d{6})-\d+$")

_DETAIL_CAP = 400


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _loads(line: str) -> dict:
    """One stream line, or {} — a half-written line is skipped, never fatal: this fold feeds
    operator-facing surfaces and `usage_stream` says the same of its own parser.
    """
    try:
        return json.loads(line)
    except Exception:
        return {}


def _row(cls: str, rid: str, text: str, detail: str = "") -> dict:
    return {"class": cls, "id": rid, "text": str(text)[:300],
            "detail": str(detail)[:_DETAIL_CAP].strip()}


def _goals(status: dict) -> list[dict]:
    """(d) — a goal the run's own record says is MET, with the evidence it recorded."""
    return [_row("d", str(g.get("id") or ""), g["text"], g.get("evidence") or "")
            for g in status.get("goals") or []
            if isinstance(g, dict) and g.get("status") == "met" and g.get("text")]


def _partial(status: dict, run_dir: Path) -> list[dict]:
    """(e) — a `partial` finish. Not a heuristic: `partial` is DEFINED as "something you cannot
    unblock yourself stopped work you could otherwise have done", so the status IS the claim.
    """
    if status.get("outcome") != "partial":
        return []
    summary = ""
    result = run_dir / "result.md"
    if result.is_file():
        try:
            summary = result.read_text(encoding="utf-8")
        except Exception:
            summary = ""
    return [_row("e", str(status.get("run_id") or run_dir.name),
                 "the run could not finish what it was asked to do", summary)]


def _asks(run_dir: Path) -> list[dict]:
    """(a) — an ask THIS run filed and nobody has answered, with its options inline.

    Only `questions/pending`: an answered ask is settled, and showing it would be the opposite
    of actionable. A BLOCKING ask is also (e) — he must decide AND the run cannot proceed — so
    it yields two rows; his list is not a partition.
    """
    ts = run_dir.name
    pending = run_dir.parent.parent / "questions" / "pending"
    if not pending.is_dir():
        return []
    out = []
    for path in sorted(pending.glob("*.json")):
        rec = _read_json(path)
        qid = str(rec.get("qid") or "")
        m = _QID.match(qid)
        if not m or m.group(1) != ts or not rec.get("question"):
            continue
        options = [str(o) for o in (rec.get("options") or [])]
        detail = " · ".join(options) if options else str(rec.get("default") or "")
        out.append(_row("a", qid, rec["question"], detail))
        if rec.get("class") == "blocking" or rec.get("blocks_work"):
            out.append(_row("e", qid, f"blocked on an answer: {rec['question']}", detail))
    return out


def _marked(status: dict) -> list[dict]:
    """(c), (f), (g) — what the run itself declared, and ONLY in a class nothing records.

    A mark in a DERIVED class is dropped rather than merged: the label would otherwise be the
    run's opinion of its own work, on surfaces that exist because its prose does not say what
    needs a person.
    """
    out = []
    for entry in status.get("attention") or []:
        if not isinstance(entry, dict):
            continue
        cls = str(entry.get("class") or "")
        text = entry.get("text") or ""
        if cls in MARKED and text:
            out.append(_row(cls, str(entry.get("id") or cls), text, entry.get("detail") or ""))
    return out


def _events(status: dict, home: Path | None) -> list[dict]:
    """(b) — the health events THIS run filed, which is the engine's own record of something
    unexpected having happened.

    Joined on `run_id` (`<slug>:<ts>`, carried by 1,651 of the stream's 2,119 id-bearing rows
    on 2026-11-14) rather than on the routine, because the question each surface asks is about
    ONE run. `home` is passed in so a caller folding many runs reads the stream once; without
    it the class is simply absent, never guessed from prose.
    """
    run_id = str(status.get("run_id") or "")
    if home is None or not run_id:
        return []
    path = Path(home) / ".control" / "health-events.jsonl"
    if not path.is_file():
        return []
    seen: dict[str, dict] = {}
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if run_id not in line:
                    continue
                rec = _loads(line)
                if not rec or str(rec.get("run_id") or "") != run_id or not rec.get("event"):
                    continue
                event = str(rec["event"])
                # ONE ROW PER EVENT, counted — measured on this instance 2026-11-14: 266 of
                # 846 of these rows were the same event filed again inside one run, and one
                # run produced 30 rows. A labelled block that led with thirty lines, nine of
                # them the same word, would be LONGER and less actionable than the narrative
                # it leads — the opposite of what he asked for. The count keeps the magnitude
                # (`blocked_kinds` makes the same distinction between a count and a span).
                if event in seen:
                    seen[event]["occurrences"] += 1
                    continue
                row = _row("b", f"{run_id}:{event}", event, rec.get("detail") or "")
                row["occurrences"] = 1
                seen[event] = row
    except Exception:
        pass
    return list(seen.values())


def classify_run(run_dir: str | Path, *, home: str | Path | None = None) -> list[dict]:
    """Every attention row this ONE run produced, in his order (a)…(g).

    Never raises: a missing, unreadable or half-written status reads as nothing to report. This
    feeds operator-facing surfaces, and a fold that can break one is worse than a fold that
    occasionally says less.
    """
    d = Path(run_dir)
    status = _read_json(d / "status.json")
    if not status:
        return []
    rows = (_goals(status) + _partial(status, d) + _asks(d) + _marked(status)
            + _events(status, Path(home) if home else None))
    return sorted(rows, key=lambda r: (_ORDER.get(r["class"], 99), r["id"]))
