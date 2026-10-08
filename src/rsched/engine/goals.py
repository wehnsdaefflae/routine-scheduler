"""The run's GOALS — what a PERSON asked of this run, one checkable end state each (`b1`, `b2`, …).

One mechanism for routines and conversations. What differs between them is only WHEN a goal
must be met, never what a goal is or who may set one:

- a ROUTINE run's goals are seeded from the operator's job BRIEF (`brief.py`, goal `b1`) and grow
  when the operator says more mid-run; its main finish accounts for every open one, met or unmet
  (a briefed run answers for them instead of its recipe's `## Done when`);
- a CONVERSATION is one continuous run, so its goals outlive a reply: the run transcribes them
  from the first message and every later one, and a reply that declares itself FINAL must meet
  every goal still open (`finishgate`).

**The run is the SCRIBE, never the author.** "A run may not set its own goals" is the rule that
made routines look incompatible with this; it is kept, and it is what makes one mechanism
possible. Every goal carries `quote` — the person's own words it came from — and the engine
looks for those words in what the person actually wrote this run (the first message of a
conversation, the brief, every message and answer since; never a report, a machine channel or
the routine's own recipe). A goal whose words cannot be found is refused. Rewording or dropping
one needs words the person wrote AFTER the words it came from: an earlier sentence can never be
the reason a later request was withdrawn, so a goal cannot be quietly abandoned because it got
hard. Checking one off is the run's own claim and is checked like any `met` (`verifier.py`).

**The transcript is the record.** Every goal action's observation carries the whole ledger after
it (`ledger`), and so does the `stopping_update` of a finish that stamped verdicts (`goals`). The
live list is that last snapshot (`replay`), so a resume, a ⟲ rewind and a ⑂ branch all read the
goals the transcript they kept says — never a side file the transcript could contradict.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import inbox

STATUSES = ("open", "met", "dropped")
#: Open goals at once. A list nobody can hold in mind is not a goal list; the finish line caps
#: its outcomes the same way.
MAX_OPEN = 12
TEXT_MAX = 300
QUOTE_MAX = 300
#: The shortest stretch of the person's words a quote may be — below this nearly anything
#: "matches" and the check stops meaning anything.
QUOTE_MIN = 3
_ID_RE = re.compile(r"^b(\d+)$")
#: Quotation marks of every kind, dropped on both sides before a quote is looked for: the
#: person typed one kind, the model quoting them often another, and neither is a word.
_QUOTES = str.maketrans(dict.fromkeys("'\"‘’‚‛“”„‟«»"))
_ELLIPSIS = re.compile(r"\s*(?:…|\.\.\.)\s*")


def blank(gid: str, text: str, quote: str, *, source: str, heard: int, turn: int) -> dict:
    return {"id": gid, "text": text[:TEXT_MAX], "quote": quote[:QUOTE_MAX], "source": source,
            "heard": heard, "status": "open", "turn": turn,
            "evidence": "", "disputed": "", "remains": "", "dropped_by": ""}


def seed(brief: str) -> list[dict]:
    """The ledger a run starts with: the operator's brief as goal `b1`, or nothing."""
    return [blank("b1", brief, brief, source="brief", heard=0, turn=0)] if brief else []


def replay(events: list[dict], brief: str) -> list[dict]:
    """The ledger the transcript says: its last snapshot, else the seed."""
    ledger = None
    for ev in events:
        payload = ev.get("payload") or {}
        if ev.get("type") == "observation" and payload.get("kind") == "goal" \
                and isinstance(payload.get("ledger"), list):
            ledger = payload["ledger"]
        elif ev.get("type") == "stopping_update" and isinstance(payload.get("goals"), list):
            ledger = payload["goals"]
    return [dict(g) for g in ledger] if ledger is not None else seed(brief)


def open_goals(ledger: list[dict]) -> list[dict]:
    return [g for g in ledger if g.get("status") == "open"]


def owed(ledger: list[dict]) -> list[dict]:
    """The open goals in the shape a Done-when line has — what a finish accounts for and what
    the verifier reads a `met` claim against.
    """
    return [{"id": g["id"], "stage": "", "text": g["text"]} for g in open_goals(ledger)]


def next_id(ledger: list[dict]) -> str:
    """The next free `b<n>`. A dropped goal keeps its id: an accounting or a note that named it
    must never come to mean a different goal.
    """
    used = [int(m[1]) for g in ledger if (m := _ID_RE.match(str(g.get("id"))))]
    return f"b{max(used, default=0) + 1}"


def find(ledger: list[dict], gid: str) -> dict | None:
    return next((g for g in ledger if g.get("id") == gid), None)


# ── the person's words ─────────────────────────────────────────────────────────────────────────

def _person_spoke(ev: dict) -> str:
    """The text of an event in which the PERSON spoke — "" for anything else: an engine note, a
    slash command (an action, not prose), a report or any other machine channel.
    """
    p = ev.get("payload") or {}
    text = str(p.get("text") or "").strip()
    if ev.get("type") == "answer":
        return text
    if ev.get("type") != "user_injection" or not text:
        return ""
    if p.get("source") == "engine" or p.get("command") or p.get("report"):
        return ""
    return text if inbox.user_authored(str(p.get("via") or "")) else ""


def person_texts(events: list[dict], *, opening: str = "", brief: str = "") -> list[str]:
    """Everything the person wrote to this run, in the order it arrived. `opening` is a
    conversation's first message (its instruction); a routine's instruction is its recipe's,
    not something a person asked of THIS run, so it is never passed. The brief comes first: the
    runner wrote it before the engine started.
    """
    out = [t for t in (brief, opening) if t]
    out += [t for ev in events if (t := _person_spoke(ev))]
    return out


def _norm(text: str) -> str:
    return " ".join(str(text).translate(_QUOTES).casefold().split())


def fragments(quote: str) -> list[str]:
    """The pieces a quote is made of: a model joins excerpts with an ellipsis and wraps them in
    quotation marks, and neither is part of what the person wrote.
    """
    parts = _ELLIPSIS.split(_norm(quote))
    return [f for p in parts if len(f := p.strip(" \"'.,;:!?")) >= QUOTE_MIN]


def heard_at(quote: str, texts: list[str], *, after: int = -1) -> int | None:
    """The index of the first person text later than `after` that holds every fragment of
    `quote`, or None — the whole grounding check.
    """
    parts = fragments(quote)
    if not parts:
        return None
    for i, text in enumerate(texts):
        if i > after and all(p in _norm(text) for p in parts):
            return i
    return None


# ── the prompt ─────────────────────────────────────────────────────────────────────────────────

_MARK = {"open": "○", "met": "✓", "dropped": "✗"}


def line(goal: dict) -> str:
    out = f"  {_MARK.get(goal['status'], '·')} [{goal['id']}] {goal['text']}"
    if goal["status"] == "open" and goal.get("remains"):
        out += f" · last left unmet: {goal['remains']}"
    if goal["status"] == "dropped" and goal.get("dropped_by"):
        out += f" · dropped on their words “{goal['dropped_by']}”"
    return out


def listing(ledger: list[dict]) -> str:
    return "\n".join(line(g) for g in ledger) or "  (no goals yet)"


def digest_section(ledger: list[dict], *, conversation: bool) -> str:
    """What the state digest says about the goals at boot — "" for a routine run nobody asked
    anything of, so the fleet's prompts stay what they were.
    """
    if not ledger:
        if not conversation:
            return ""
        return ("GOALS: none yet. When the person asks for work that this one reply will not "
                "settle, transcribe what they asked for with `goal` (verb=set) before you start: "
                "each goal one checkable end state, carrying the words of theirs it came from.")
    shown = [g for g in ledger if g["status"] == "open"] or ledger[-3:]
    rest = len(ledger) - len(shown)
    head = ("GOALS (what the person asked for in this conversation — you keep the list with "
            "`goal`; a reply with `final: true` meets every open one):" if conversation else
            "GOALS (what a person asked of THIS run — the operator's brief and anything they "
            "asked for since; a briefed run answers for these instead of its recipe's `## Done "
            "when`):")
    out = [head, *(line(g) for g in shown)]
    if rest > 0:
        out.append(f"  (+{rest} met or dropped earlier — `goal` verb=list shows them)")
    return "\n".join(out)


def stamp(ledger: list[dict], verdicts: dict[str, tuple[str, str]], *,
          disputes: dict[str, str] | None = None) -> bool:
    """Write a standing finish's verdicts onto the open goals; True when anything changed. A
    `met` closes the goal with its evidence (and the verifier's unresolved objection, if any);
    an `unmet` leaves it open with what remains, for the next leg and the person to read.
    """
    changed = False
    for g in open_goals(ledger):
        got = verdicts.get(g["id"])
        if got is None:
            continue
        verdict, note = got
        if verdict == "met":
            g.update(status="met", evidence=note, disputed=(disputes or {}).get(g["id"], ""))
            changed = True
        elif verdict == "unmet":
            g["remains"] = note
            changed = True
    return changed


def transcript_events(run_dir: Path) -> list[dict]:
    from .transcript import read_events
    return read_events(Path(run_dir) / "transcript.jsonl", 0)[0]
