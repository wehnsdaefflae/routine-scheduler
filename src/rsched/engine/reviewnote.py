"""The review leg's WORDS — the note that opens it and the line it leaves on the run's result.

Prompt surface (docs/prompt-anatomy.md pins it), kept apart from the driver (engine/reviewleg.py)
so the wording reads in one place. The keeping standard is Hermes Agent's, each clause answering
a measured failure: only what this run SHOWED; a general rule plus one clause of why, one lesson
once; an expiry on what will date; no negative claim about a tool from one failure; no unresolved
failure as a method.

"Nothing to keep" is stated as a complete answer on purpose: a leg pressed to always write
something produces the bookkeeping the overhead measurement exists to catch.
"""

from __future__ import annotations


def note(status: str, reasons: list[str], *, kinds: list[str], turns: int, unread: list[str],
         revisions: bool, reminders: bool) -> str:
    """The ENGINE NOTE that opens the leg."""
    why = "; ".join(reasons)
    text = (
        f"REVIEW LEG. This run has FINISHED (status {status}); its result stands and nothing "
        f"you do now changes it. Before it closes, look back over this run once — because {why} "
        "— for what a LATER run of this routine should know and would otherwise learn the hard "
        "way again: a trap that cost turns, a route that finally worked, a constraint nobody "
        f"wrote down. You hold only {', '.join(kinds)}, for at most {turns} turns, and none of "
        "it counts against the task. Keep it in your notes, and keep it well:\n"
        "- only what this run SHOWED — a result or an error you can point to; never a guess, "
        "and never one request turned into a standing rule;\n"
        "- as a general rule with one clause of why — no dates, run ids or ticket numbers; the "
        "same lesson twice is ONE rule;\n"
        "- in the note that already covers it — memory_read it first (a note you have not read "
        "in this leg cannot be overwritten) and correct it in place, never by appending an "
        "'update:' line;\n"
        "- with `stale_after` set when it states what will go out of date — a price, a deadline, "
        "a version, someone's availability — so a later run checks it instead of trusting it;\n"
        "- never that a tool or source CANNOT do something because it failed once here — that "
        "hardens into a refusal later runs cite against themselves — and never an unresolved "
        "failure written up as a working method.")
    if unread:
        text += ("\nNo run has read these notes in many runs: " + ", ".join(unread[:12])
                 + ". Where one is covered by another note, fold it in: read both, write the "
                 "note that keeps what it held, then delete it. Otherwise leave it.")
    if reminders:
        text += ("\nA caution about one specific action of yours belongs in `remind`, not in "
                 "a note.")
    if revisions:
        text += ("\nIf your RECIPE cost this run turns and your summary did not already say so, "
                 "end this leg's finish summary with one `RECIPE REVISION: <file> — <change>` "
                 "line per revision, each a general rule with its reason.")
    return text + ("\nNothing worth keeping is a complete answer: then finish at once, status ok, "
                   "summary 'nothing to keep'.")


def result_line(rec: dict) -> str:
    """What the run's summary gains — the engine's record of the leg, one line."""
    parts = [f"{rec['turns']} turn{'s' if rec['turns'] != 1 else ''}"]
    if rec.get("wrote"):
        parts.append("notes written: " + ", ".join(dict.fromkeys(rec["wrote"])))
    if rec.get("deleted"):
        parts.append("folded and deleted: " + ", ".join(dict.fromkeys(rec["deleted"])))
    if not rec.get("wrote") and not rec.get("deleted"):
        parts.append("nothing kept")
    if stopped := rec.get("stopped"):
        parts.append(f"stopped: {stopped}")
    return f"\n[Review leg ({'; '.join(rec['reasons'])}): {'; '.join(parts)}.]"
