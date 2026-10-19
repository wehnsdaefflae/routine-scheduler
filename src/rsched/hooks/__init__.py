"""HOOKS — the one mechanism behind reports, consequence reminders and run injections.

The operator's order (2026-10-18): *"implement hooks first as a single mechanism unifying
a), b) and c)"* — a) reports/messages, b) consequence reminders, c) run injections — *"and
make sure f695 is a special case of that as well"* (run↔sub-run and sibling communication).
D152 option C is the ledger row; `state/hooks-design.md` in scheduler-builder is the design.

A hook is **five independent fields**. Every mechanism this instance already has is ONE
configuration of them, and no mechanism adds a field — that is the test that this is really
one mechanism rather than three stores with a shared name:

| mechanism | trigger | addressee | delivery | disposition |
|---|---|---|---|---|
| report | `send` | `routine:<slug>` / `triage` | `next_prompt` | open → settled |
| report + `options` | `send` | routine + operator | `next_prompt`, `decisions_page` | → answered |
| reminder, `action` | `match` action | `self` / a routine | `hold_before_action` | kept/muted |
| reminder, result/prose | `match` those | same | `turn_boundary` | same |
| reminder, `turn:<moment>` | `moment` | same | `turn_boundary` | same |
| operator injection | `send` | `run:<run_id>` | `turn_boundary` | consumed once |

Two rules fall out of the table and they are the whole of F695:

1. **The run tree is the only addressing scheme.** A child is addressed as `child:<label>`
   relative to its parent, a sibling by the parent re-addressing. No run reaches outside its
   own subtree except by `routine:<slug>` — which is a report, and already exists.
2. **`hold_before_action` is the only delivery moment that can change what runs.** Every
   other moment rides an observation or a prompt section, so adding addressees can never
   alter control flow. That is why the reminder half is the risky one and the rest is not.

## What this package is, at this step of the campaign

The RECORD and its STORE, written by the paths that already exist. Nothing here changes what
any routine sees: `reports.jsonl`, the reminders store and the inbox remain the delivery
mechanisms, and every current reader of them is untouched. What the store adds is the one
place where "what hooks exist in this instance" is answerable across all three mechanisms —
the question none of the three stores can answer today, and the thing the later steps (F695's
new addressees, then retiring the duplicated paths) are built on.

The vocabularies below live HERE rather than in `reminders.py` for the same reason: a trigger
and a disposition are hook concepts that the reminder store happens to use, not reminder
concepts the rest of the system borrows. `rsched.reminders` imports them back under the names
its own callers already know, so nothing downstream moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: WHAT MAKES A HOOK FIRE.
#: - `send`     — once, now: a message. A report, an injection, a hand-back.
#: - `match`    — a pattern over something the run produced (see `TRIGGER_KINDS`).
#: - `moment`   — a special turn of the run (see `TURN_MOMENTS`).
#: - `schedule` — a time. Nothing uses this yet; the field costs nothing and naming it keeps
#:                the next mechanism from being a fourth store.
TRIGGERS = ("send", "match", "moment", "schedule")

#: The special turns a `moment` trigger can name: `first` (the opening turn of a run),
#: `finish`, `question` (an ask_user), `answer` (a user reply arriving), `error` (an
#: observation reporting failure).
TURN_MOMENTS = ("first", "finish", "question", "answer", "error")

#: What a `match` trigger is tested AGAINST. `action` is the original and the default: the
#: canonical one-line rendering of an action (`engine/actionschema.canon`), tested BEFORE the
#: action executes, so a match can still prevent the consequence. The others describe
#: something that has already happened, so they inform at no turn cost:
#:
#: - `result`   — the OBSERVATION that came back.
#: - `prose`    — the turn's own `say`: what the run SAID it was doing, which is where a drift
#:                ("I will force push") is visible before the call that does it.
#: - `turn:*`   — the `TURN_MOMENTS`, carried in this same vocabulary because the reminder
#:                store has always spelled them as kinds and its records say so on disk.
#:
#: A record whose kind is unreadable reads as `action`: a store must never be able to break a
#: run, and `action` is the one kind every pre-D152 record meant.
TRIGGER_KINDS = ("action", "result", "prose", *(f"turn:{m}" for m in TURN_MOMENTS))
DEFAULT_TRIGGER_KIND = "action"

#: The kinds tested BEFORE the action runs, so they can HOLD it. Rule 2 above: this tuple is
#: the complete list of ways a hook can change control flow.
HOLDING_KINDS = ("action",)

#: WHO A HOOK IS FOR. `routine:<slug>` reaches another routine's next run; `run:<run_id>` a
#: live run; `child:<label>` and `parent` address the run tree (F695, rule 1); `operator` is
#: the Decisions page; `triage` is "I cannot name the owner", which self-audit routes.
ADDRESSEE_KINDS = ("routine", "run", "child", "parent", "operator", "triage", "self")

#: WHEN IT ARRIVES. `next_prompt` = the addressee's next run reads it (a report).
#: `turn_boundary` = it rides a live run's turn boundary (an injection, a non-holding
#: reminder). `hold_before_action` = the action is held and the model decides again — the one
#: delivery that changes control flow. `decisions_page` = it becomes a question for the
#: operator (a report carrying `options`).
DELIVERIES = ("next_prompt", "turn_boundary", "hold_before_action", "decisions_page")

#: WHAT WAS DECIDED ABOUT IT. The message-shaped half: `open` until something settles it.
THREAD_DISPOSITIONS = ("open", "settled")
#: The held half — what the TARGET of a hook decides, met on every fire: `keep` (nothing
#: decided), `mute` (run-scoped), `remove` (deleted, and remembered so it cannot be re-set).
#: The recipient disposes, never the sender: one rule for reports (D182) and for cross-routine
#: reminders alike.
TARGET_DISPOSITIONS = ("keep", "mute", "remove")
DISPOSITIONS = (*THREAD_DISPOSITIONS, *TARGET_DISPOSITIONS)
DEFAULT_DISPOSITION = "keep"

#: Which of today's mechanisms wrote the row. Kept on the record because the campaign's later
#: steps retire these paths one at a time: a row has to say which path it came from for
#: "is anything still writing the old way" to be answerable at all.
SOURCES = ("report", "reminder", "injection", "runtree")


def addressee(kind: str, name: str = "") -> str:
    """The addressee as one string — `routine:self-audit`, `run:x:20261019-010000`, `parent`.

    One spelling, minted here, because the addressee is the only part of a hook that another
    run has to PARSE, and a second spelling of it is how an addressing scheme gains a special
    case (rule 1). A kind that takes no name renders bare.
    """
    kind = str(kind or "").strip()
    name = str(name or "").strip()
    if kind not in ADDRESSEE_KINDS:
        raise ValueError(f"unknown hook addressee kind {kind!r} — one of {list(ADDRESSEE_KINDS)}")
    return f"{kind}:{name}" if name else kind


@dataclass(frozen=True)
class Hook:
    """One hook, as the store holds it: the five fields, plus who made it and when.

    Frozen, like `reminders.Reminder` and for the same reason: a record read out of a store is
    evidence about what happened, and a reader that can edit it in place is how two callers
    come to disagree about what the row said.
    """

    id: str
    trigger: dict = field(default_factory=dict)
    addressee: str = ""
    payload: dict = field(default_factory=dict)
    delivery: str = ""
    disposition: str = DEFAULT_DISPOSITION
    origin: dict = field(default_factory=dict)
    source: str = ""
    ts: str = ""

    def as_record(self) -> dict:
        """The on-disk shape — the five fields in the design's own order, then provenance."""
        return {"id": self.id, "ts": self.ts, "source": self.source,
                "trigger": dict(self.trigger), "addressee": self.addressee,
                "payload": dict(self.payload), "delivery": self.delivery,
                "disposition": self.disposition, "origin": dict(self.origin)}

    def holds(self) -> bool:
        """Can this hook change what runs? True only for `hold_before_action` (rule 2)."""
        return self.delivery == "hold_before_action"


def from_record(row: dict) -> Hook:
    """A `Hook` from one stored row, leniently: an unknown trigger kind, delivery or
    disposition is kept as written rather than rejected.

    The store is append-only and shared by three mechanisms that are being migrated one at a
    time, so a row written by a NEWER build must still read. A reader that raises on an
    unexpected value would turn a forward-compatible store into an outage.
    """
    row = row if isinstance(row, dict) else {}
    return Hook(id=str(row.get("id") or ""),
                trigger=dict(row.get("trigger") or {}),
                addressee=str(row.get("addressee") or ""),
                payload=dict(row.get("payload") or {}),
                delivery=str(row.get("delivery") or ""),
                disposition=str(row.get("disposition") or DEFAULT_DISPOSITION),
                origin=dict(row.get("origin") or {}),
                source=str(row.get("source") or ""),
                ts=str(row.get("ts") or ""))
