"""The hook store — `<routines_home>/.control/hooks.jsonl`, one append-only stream.

**One writer, and it is this module.** The three mechanisms that exist today (reports,
consequence reminders, run injections) keep their own stores and their own readers at this
step of the campaign and MIRROR into this one, through the three `record_*` functions below.
Nothing reads this stream to make a decision yet — which is exactly why the mirror is safe to
land first: a defect here cannot change what any routine sees, while the stream it builds is
what the later steps need to exist before they can be built on it.

**Why mirror rather than cut over.** `reports.jsonl` and the reminders store both have readers
OUTSIDE this engine — the console's Decisions page and self-audit's own scripts fold the report
ledger; the reminders store carries per-routine fire tallies whose history is the evidence that
tunes a pattern. A hard cutover would invalidate the operator's own backlog counts on the day
it shipped. Two writers for one campaign is the cheaper mistake, and the campaign's last step
is what removes the duplication (`state/hooks-design.md`, "Store: wrap first, replace last").

**Best-effort, always.** A mirror write that fails must never abort the run whose real work is
elsewhere — the same discipline the health stream and an unaddressed report already use. Every
function here returns the id it wrote or `""`, and swallows `OSError`. That is also why the id
is minted WITHOUT a lock-and-scan over the whole stream: the row's identity is the originating
mechanism's own id (`R<n>`, `rem-<ts>-<n>`, or the run and turn an injection arrived at), which
is already unique and already the name every other surface knows it by. A counter of our own
would be a second identity for one thing.
"""

from __future__ import annotations

from pathlib import Path

from ..ids import now_iso
from ..paths import append_jsonl, read_jsonl
from . import Hook, addressee, from_record

HOOKS_FILE = "hooks.jsonl"


def hooks_path(routines_home: Path) -> Path:
    """The stream's path. Beside `reports.jsonl` in `.control`, because a hook is instance-wide
    freight exactly as a report is — not per-routine state.
    """
    return Path(routines_home) / ".control" / HOOKS_FILE


def append(routines_home: Path, hook: Hook) -> str:
    """Append ONE hook row; return its id, or "" when the write failed.

    No lock: `append_jsonl` writes the row in a single `write(2)` on an O_APPEND descriptor,
    which Linux appends whole, and ids are minted by the originating mechanism rather than
    from the stream — so there is no read-modify-write to serialize and nothing a concurrent
    writer can corrupt. The report ledger locks because it MINTS `R<n>` by scanning; this
    stream has no such counter.
    """
    try:
        row = hook.as_record()
        if not row.get("ts"):
            row["ts"] = now_iso()
        append_jsonl(hooks_path(routines_home), row)
    except OSError:
        return ""
    return hook.id


def read_hooks(routines_home: Path) -> list[Hook]:
    """Every row in the stream, in order, as `Hook`s. A missing stream reads as empty.

    Append-only, so a later row about the same id is a later FACT about it, not a correction:
    folding is each reader's own business and deliberately not done here.
    """
    return [from_record(row) for row in read_jsonl(hooks_path(routines_home))]


def record_report(routines_home: Path, *, report_id: str, routine: str, run_id: str,
                  title: str, detail: str = "", target: str = "",
                  options: tuple[str, ...] = (), disposition: str = "open") -> str:
    """Mirror a filed report as a hook: `trigger: send`, delivery `next_prompt`.

    A report carrying `options` is TWO deliveries of one hook (the target's next prompt and the
    operator's Decisions page) and the table in `hooks/__init__` says so; the row records the
    additional one in `payload.options`, which is what makes it visible as a decision without
    inventing a second addressee. An unaddressed report is `triage` — the routine that has not
    been named yet, not the absence of an addressee.
    """
    who = addressee("routine", target) if target else addressee("triage")
    return append(routines_home, Hook(
        id=str(report_id), source="report", trigger={"kind": "send"}, addressee=who,
        payload={"title": title, "detail": detail,
                 **({"options": list(options)} if options else {})},
        delivery="next_prompt", disposition=disposition,
        origin={"routine": routine, "run_id": run_id}, ts=now_iso()))


def record_reminder(routines_home: Path, *, reminder_id: str, routine: str, run_id: str,
                    regex: str, description: str, kind: str, scope: str,
                    target: str = "", disposition: str = "keep") -> str:
    """Mirror a written consequence reminder as a hook: `trigger: match`.

    The delivery is the hook's own question, not a field the reminder store keeps: an `action`
    kind HOLDS the action (the one delivery that changes control flow), every other kind rides
    the observation at the turn boundary. Deriving it here rather than storing it twice is what
    keeps the two stores from being able to disagree.

    `target` empty means the routine set it on ITSELF — addressed `self`, because "my own
    future runs" is a real addressee and spelling it as the routine's own slug would make a
    self-hook indistinguishable from one a sibling set on it.
    """
    from . import DEFAULT_TRIGGER_KIND, HOLDING_KINDS

    kind = str(kind or DEFAULT_TRIGGER_KIND)
    who = addressee("routine", target) if target else addressee("self")
    return append(routines_home, Hook(
        id=str(reminder_id), source="reminder",
        trigger={"kind": "match", "match": kind, "regex": regex},
        addressee=who, payload={"description": description, "scope": scope},
        delivery="hold_before_action" if kind in HOLDING_KINDS else "turn_boundary",
        disposition=disposition, origin={"routine": routine, "run_id": run_id}, ts=now_iso()))


def record_runtree(routines_home: Path, *, hook_id: str, routine: str, run_id: str,
                   turn: int, addressee: str, title: str, delivered: bool) -> str:
    """Mirror a RUN-TREE hook as a hook row: `trigger: send`, addressee `parent` or
    `child:<label>`, delivery `turn_boundary` (F695 as a case of D152, `engine/runtree.py`).

    `disposition` is `settled` when the freight was filed and `open` when the write failed —
    the one thing a reader cannot reconstruct from the two runs' transcripts, since a run-tree
    hook deliberately gets no `R<n>` row to carry it (a parent and a child are both gone in
    minutes, and an item nobody can close is the backlog pathology the ledger exists to show).
    """
    return append(routines_home, Hook(
        id=str(hook_id), source="runtree",
        trigger={"kind": "send"}, addressee=str(addressee),
        payload={"title": title}, delivery="turn_boundary",
        disposition="settled" if delivered else "open",
        origin={"routine": routine, "run_id": run_id, "turn": turn}, ts=now_iso()))


def record_injection(routines_home: Path, *, run_id: str, turn: int, via: str,
                     text: str, routine: str = "") -> str:
    """Mirror a mid-run injection as a hook: `trigger: send`, addressee `run:<run_id>`,
    delivery `turn_boundary` — consumed once, which is the disposition `settled` records.

    The id is `inj-<run_id>-t<turn>`: an injection has no id of its own anywhere in the system,
    and the run and turn it was DELIVERED at are what identifies it — the same key the
    transcript's `user_injection` event is found by. Idempotent by construction, so a replayed
    leg re-delivering the same message cannot double-count it.

    `via` is kept because it is the one thing a reader needs that the text does not say: who
    spoke. An escalation rung's directive and the operator typing in the run view arrive on the
    same seam and are not the same hook (`engine/inbox.VIAS`).
    """
    return append(routines_home, Hook(
        id=f"inj-{run_id}-t{turn}", source="injection",
        trigger={"kind": "send", "via": via},
        addressee=addressee("run", run_id), payload={"text": text},
        delivery="turn_boundary", disposition="settled",
        origin={"routine": routine, "run_id": run_id, "turn": turn}, ts=now_iso()))
