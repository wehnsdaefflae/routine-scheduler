"""A report AS a hook, and the ledger row DERIVED from it — campaign step 4 of D152.

Steps 2 and 3 left two writers on purpose: `reports.jsonl` kept its own row construction and
this stream was a best-effort MIRROR of it. That is the duplication this module retires. From
here the hook is the SOURCE — built first, written first, and refused loudly when it cannot be
written — and every `reports.jsonl` row is a pure function of it, computed here.

**Why the ledger file survives at all.** It has readers outside this engine: the operator's own
backlog counts, `self-audit`'s folds, and anything that parses the stream by hand. A hard
cutover would invalidate those on the day it shipped (`state/hooks-design.md`, "wrap first,
replace last"), and the design's answer is this shape — one writer, one derivation, the old
file's bytes unchanged. `rebuild` is what makes that claim testable rather than asserted: fold
the hook stream and fold the ledger, and the two must agree row for row.

**The lifecycle is on the stream too.** A report is not just filed: it is delivered, retracted,
superseded, settled. Step 2 recorded only the filing, so the stream could not answer "what
happened to this hook" — the question a store that owns the mechanism has to answer. Each of
those events is now one more row about the same id, which is what an append-only stream means
by a later fact.
"""

from __future__ import annotations

from pathlib import Path

from ..ids import now_iso
from ..report_disposal import Disposal
from . import Hook, addressee, split_addressee
from . import store as hookstore

#: The lifecycle events a report row can carry, spelled exactly as the ledger spells them —
#: these ARE the ledger's event vocabulary, kept in one place now that the stream carries it
#: too. `superseded` is written by the filing that folds a row; the other two by the drain and
#: by the operator's withdrawal.
EVENTS = ("delivered", "retracted", "superseded")

#: Which of them END the thread. A `delivered` report is still owed an answer, so delivery is
#: not a disposition; a retraction and a fold both take the row out of what anyone owes.
TERMINAL_EVENTS = ("retracted", "superseded")


def report_hook(*, report_id: str, routine: str, run_id: str, title: str, detail: str = "",
                target: str = "", disposal: Disposal | None = None,
                supersedes: tuple[str, ...] = (), ts: str = "") -> Hook:
    """The hook a filed report IS: trigger `send`, delivery `next_prompt`, disposition `open`.

    The whole disposal travels in the payload — `answers`, `closes`, `settles`, `supersedes`.
    Step 2's mirror carried only the title, the detail and the target, which is precisely why
    it could not be the source: a stream missing what a report DISPOSED OF cannot reproduce the
    ledger, and a store that cannot reproduce the thing it replaced is a mirror whatever it is
    called.

    It arrives as the `Disposal` VALUE rather than its three faces splatted into keywords, for
    the reason that object exists: `answers`, `closes` and `settles` are one decision with
    three faces, and keeping them apart is what let them drift (F497). The normalization — ids
    upper-cased, `closes` refused without a disposal to complete — therefore happens once,
    before anything is written anywhere.

    A report's `options` are NOT here, and that is deliberate: the Decisions-page delivery is
    filed after the ledger row, by `api_questions` against the report's id, so nothing reaches
    this function carrying them. The design's second delivery is therefore the one piece of the
    table the stream still cannot answer — named in the campaign plan rather than faked with a
    parameter no caller passes.
    """
    disposal = disposal or Disposal()
    who = addressee("routine", target) if target else addressee("triage")
    payload: dict = {"title": title, "detail": detail}
    if disposal.answers:
        payload["answers"] = disposal.answers
    if disposal.closes:
        payload["closes"] = True
    if disposal.settles:
        payload["settles"] = list(disposal.settles)
    if supersedes:
        payload["supersedes"] = list(supersedes)
    return Hook(id=str(report_id), source="report", trigger={"kind": "send"}, addressee=who,
                payload=payload, delivery="next_prompt", disposition="open",
                origin={"routine": routine, "run_id": run_id}, ts=ts or now_iso())


def event_hook(*, report_id: str, event: str, ts: str = "", run_id: str = "", by: str = "",
               to: str = "") -> Hook:
    """One lifecycle fact about a report, as a hook row: delivered, retracted or superseded.

    The id is the REPORT's — a later row about the same id is the later fact about it, never a
    second item. `disposition` is where the fact lands on the mechanism's own vocabulary:
    `settled` for the two events that end a thread, `open` for a delivery, which only says the
    target picked the freight up and still owes an answer.
    """
    event = str(event or "")
    payload: dict = {"event": event}
    if run_id:
        payload["run_id"] = run_id
    if by:
        payload["by"] = by
    if to:
        payload["to"] = to
    return Hook(id=str(report_id), source="report", trigger={"kind": "send"},
                addressee=addressee("triage"), payload=payload, delivery="next_prompt",
                disposition="settled" if event in TERMINAL_EVENTS else "open",
                origin={"run_id": run_id} if run_id else {}, ts=ts or now_iso())


def _target_of(hook: Hook) -> str:
    """The addressed routine, or `""` for triage. The inverse of the addressee minted above —
    one spelling in, one spelling out, so the projection can never invent a target the hook
    does not name.
    """
    kind, name = split_addressee(hook.addressee)
    return name if kind == "routine" else ""


def ledger_row(hook: Hook) -> dict:
    """The `reports.jsonl` row for a FILED report, derived from its hook.

    Key order matters: the file is append-only and read by eye as often as by code, so the
    projection emits the same order the ledger has always had. Optional keys stay absent rather
    than empty — a row saying `"target": ""` would read as addressed-to-nobody, which is not
    the same thing as unaddressed.
    """
    p = dict(hook.payload)
    row: dict = {"id": hook.id, "ts": hook.ts,
                 "routine": str(hook.origin.get("routine") or ""),
                 "run_id": str(hook.origin.get("run_id") or ""),
                 "title": str(p.get("title") or ""), "detail": str(p.get("detail") or "")}
    if target := _target_of(hook):
        row["target"] = target
    if answers := str(p.get("answers") or ""):
        row["answers"] = answers
    if p.get("closes"):
        row["closes"] = True
    if supersedes := list(p.get("supersedes") or ()):
        row["supersedes"] = supersedes
    if settles := list(p.get("settles") or ()):
        row["settles"] = list(settles)
    return row


def event_row(hook: Hook) -> dict:
    """The `reports.jsonl` EVENT row derived from a lifecycle hook — `{id, event, ts, …}`."""
    p = dict(hook.payload)
    row: dict = {"id": hook.id, "event": str(p.get("event") or ""), "ts": hook.ts}
    if run_id := str(p.get("run_id") or ""):
        row["run_id"] = run_id
    if by := str(p.get("by") or ""):
        row["by"] = by
    if to := str(p.get("to") or ""):
        row["to"] = to
    return row


def rows_for(hook: Hook) -> dict:
    """The ledger row for ANY report hook — a filing or a lifecycle fact. One entry point, so a
    caller never has to ask which kind it is holding.
    """
    return event_row(hook) if hook.payload.get("event") else ledger_row(hook)


def filed(routines_home: Path, **kw) -> dict:
    """Write the hook for a FILING and return the ledger row to append — the one order that
    makes the hook the source: the stream first, the projection after, and an I/O failure
    raising out of here so the caller writes no ledger row it cannot account for.
    """
    hook = report_hook(**kw)
    hookstore.write(routines_home, hook)
    return ledger_row(hook)


def event(routines_home: Path, **kw) -> dict:
    """The same order for a LIFECYCLE fact (delivered, retracted, superseded)."""
    hook = event_hook(**kw)
    hookstore.write(routines_home, hook)
    return event_row(hook)


def stream_rows(routines_home: Path) -> list[dict]:
    """Every `reports.jsonl` row the hook stream implies, in stream order.

    This is the derivation the campaign's claim rests on: if the ledger is a projection, then
    folding THIS and folding the file must give the same reports. `rebuild` does exactly that,
    and a test compares the two over the live write paths.
    """
    return [rows_for(h) for h in hookstore.read_hooks(routines_home) if h.source == "report"]


def rebuild(routines_home: Path) -> list[dict]:
    """The report ledger, rebuilt from the hook stream alone and folded by the ledger's OWN
    fold — never a second implementation of it, which is how two readings of one stream come to
    disagree.
    """
    from ..reports import fold_rows

    return fold_rows(stream_rows(routines_home))
