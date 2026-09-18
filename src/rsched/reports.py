"""Reports: the one channel a run uses to say "something needs doing that isn't my task".

ONE action (`report`), ungated, held by every routine. What varies is whether the reporting
run can name an OWNER:

- **Unaddressed** — "something is wrong and I am not going to work out whose it is." The
  entry lands in `<routines_home>/.control/reports.jsonl` and self-audit's triage reads the
  stream each run. A run that hits friction mid-task should never have to consult the
  ownership table to say so.
- **Addressed** (`target`) — "I know whose this is." Same ledger row, plus delivery of a
  `msg-rep-<id>.json` into that routine's `inbox/`, which its NEXT SCHEDULED RUN drains.

An addressed report NEVER starts a run — it only writes a file the target picks up on its own
schedule. Keep it that way: seizing another routine's schedule to service your finding makes
its work worse, and the durable message loses nothing by waiting. (A TARGET may opt itself
into being woken by declaring a `report` trigger — docs/triggers.md; that is the receiving
routine's own config, never the sender's doing.)

Triage is therefore forwarding, not absorbing: self-audit answers an unaddressed report that
is not a scheduler defect by filing an addressed one carrying `answers`, so the hand-off is
recorded rather than performed by hand.

Every row gets a monotonic `R<n>` assigned here under the same advisory lock as the append —
two runs reporting at once cannot collide. `R` and not `B`: the user's own reviewer-backlog
items are written `B<n>` in prose, and the console's reference links would mislink them. The
id makes a report a first-class ITEM alongside findings and decisions (docs/items.md).

The ledger is append-only, so the lifecycle is recorded as EVENT rows folded by
`read_reports`: the report itself, then a `delivered` row stamped when an addressed target's
run actually drains the message.

A reply row may carry `closes: true` beside `answers` — the TERMINAL acknowledgment. Without
it every answer is itself a new open report and a closed exchange ratchets forever (each
"thanks, done" needing its own "thanks"); with it the reply settles its target AND is born
settled itself, asking nothing back. A closure is still delivered when addressed — the filer
learns the outcome — but the message says no reply is needed. Only a NEW report that names
the closure reopens the thread.

A report may also SUPERSEDE existing rows (`supersedes`): "these are now this thread." That is
the one operation routing and consolidation both needed and neither had (F492/D110). Handing a
triage row to its owner used to leave the original untargeted, so the next triage pass found it
again and routed it a second time — R1491/R1496/R1516/R1517/R1519/R1520 were each routed twice
in two days, once in R1525 and again in R1558. And the `problem-routing` rule's "add your
evidence to the OLDEST open one rather than opening another" named an operation that did not
exist. A `superseded` event row closes both: the original folds into the carrier and takes the
carrier's status from then on (docs/items.md), so it leaves triage once and settles when the
thread it joined settles.

That is also what makes the OPEN-THREAD CAP fair. A run may not open more than
`OPEN_THREAD_CAP` parallel threads to one owner (D110, operator 2026-08-31); the refusal names
the open ids, and `supersedes` is the way through — fold them into the one report that carries
them all. A cap without an append operation would just lose the finding.

The user's ONE write on this stream is RETRACTION (`retract_report`, D74): an addressed
report whose delivery still waits in the target's inbox can be withdrawn — the delivery file
is unlinked (the recipient never sees it) and a `retracted` event row records it. The report
row itself is never rewritten or edited: it is the RUN's utterance, and a correction is a new
message the user writes to the target's inbox in their own voice (docs/messages.md).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, file_lock
from .report_threads import OPEN_THREAD_CAP, ThreadCapError, open_threads, supersedable

REPORTS_FILE = "reports.jsonl"
REPORT_ID_RE = re.compile(r"^R(\d+)$")

TITLE_MAX = 300
DETAIL_MAX = 4000


def reports_path(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / REPORTS_FILE


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def next_id(path: Path) -> str:
    """The next free `R<n>`: one past the highest in the stream. The counter lives IN the data
    — no sidecar to drift out of sync with a restored or hand-edited file.
    """
    highest = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "R1"
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = REPORT_ID_RE.match(str(row.get("id") or "")) if isinstance(row, dict) else None
        if m:
            highest = max(highest, int(m.group(1)))
    return f"R{highest + 1}"


@dataclass(frozen=True)
class Disposal:
    """What a report DISPOSES OF: the one exchange it answers, the rows it settles, and whether
    it is itself terminal.

    The three travel together because they are one decision with three faces, and keeping them
    apart is what let them drift: `closes` was a property of `answers` alone, so a reply that
    settled several rows could only close one, and a report that asked nothing back could close
    nothing at all (F497 — measured on this ledger as rows aging at 11 and 12 days a day after
    being genuinely answered, and seven fold-carriers born open, four of them literally saying
    "no reply needed").

    - `answers` — the ONE report id this reply belongs to, the exchange record. Unchanged.
    - `settles` — every id this reply terminally disposes of (D134, operator-selected option C).
    - `closes` — this reply is itself born settled. Valid beside EITHER disposal.
    """

    answers: str = ""
    closes: bool = False
    settles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Normalize once, here, so every reader downstream compares like with like: the ledger
        # stores ids upper-cased and a caller passing "r123" must not mint a row nothing matches.
        object.__setattr__(self, "answers", str(self.answers or "").strip())
        object.__setattr__(self, "settles",
                           tuple(str(i).strip().upper() for i in self.settles if str(i).strip()))
        # `closes` is a property OF a disposal: with neither face there is nothing to complete.
        object.__setattr__(self, "closes",
                           bool(self.closes and (self.answers or self.settles)))

    @property
    def settled_ids(self) -> list[str]:
        """Every row id this reply disposes of, `answers` included — what the read model reads."""
        return [i for i in (self.answers.upper(), *self.settles) if i]


def message_text(item_id: str, sender: str, title: str, detail: str,
                 disposal: Disposal | None = None) -> str:
    """The prose a delivered report becomes in the target's prompt — one wording, used by the
    boot drain and the mid-run injection alike so a resumed prompt reads like a live one.

    Settled rows are NAMED in the head rather than left to the ledger: the recipient's own rows
    are what got disposed of, and a reader who cannot see which ones has to go and look them up.
    """
    disposal = disposal or Disposal()
    answers, closes = disposal.answers, disposal.closes
    head = f"REPORT {item_id} from routine `{sender}`"
    if answers and closes:
        head += f" (answering {answers} — closes the exchange, no reply needed)"
    elif answers:
        head += f" (answering {answers})"
    if disposal.settles:
        rows = ", ".join(disposal.settles)
        head += (f" (settles {rows} — no reply needed)" if closes and not answers
                 else f" (settles {rows})")
    body = [head, "", title]
    if detail:
        body += ["", detail]
    return "\n".join(body)


def file_report(routines_home: Path, *, routine: str, run_id: str, title: str, detail: str = "",
                target: str = "", target_dir: Path | None = None,
                disposal: Disposal | None = None,
                supersedes: tuple[str, ...] = ()) -> tuple[Path, str, list[str]] | None:
    """Append one report, and deliver it when it is addressed.

    Returns `(path, id, folded)` on success, or None if the write failed. `folded` is what was
    actually taken over — decided under the ledger lock, so the observation the run reads can
    never claim a row that a concurrent filing had already folded. An UNADDRESSED report is
    best-effort like the health log — a failed write must never abort the reporting run, whose
    real job is elsewhere. An ADDRESSED one is the caller's whole purpose, so the handler
    surfaces the failure instead of letting the run believe it routed work it did not.

    `disposal` carries what this report disposes of (see `Disposal`): the one exchange it
    answers, the rows it settles, and whether it is itself terminal. `closes` is recorded only
    beside one of those two disposals — `Disposal.__post_init__` enforces that for every caller,
    in-band or out, so the action validator and a direct call cannot disagree.

    `settles` is the many-rows terminal claim (D134): every id listed reads `settled` with this
    report as what settled it. It answers two shapes `answers` could not express, both measured
    on this ledger as F497 — a reply that genuinely disposes of SEVERAL rows could settle only
    one, leaving the rest to age with an empty `answered_by`; and a report that asks nothing back
    answers no single row, so it could not carry `closes` and was born open forever. Unlike
    `supersedes`, settling moves no work: folding makes this report the row's thread, settling
    declares the row finished.

    `supersedes` folds existing rows into this one: each gets a `superseded` event naming this
    report, and from then on it reads whatever this thread reads. Ids the ledger cannot fold
    (unknown, retracted, already in another thread) are dropped here rather than stamped —
    `supersedable` is what a caller uses to tell the run WHICH ones before it gets this far.

    Raises `ThreadCapError` when this would open an `OPEN_THREAD_CAP`-plus-first parallel
    thread to one owner. A reply, a settlement and a consolidation are all exempt: each one
    REDUCES the open-thread count, and capping the way out would punish it.
    """
    disposal = disposal or Disposal()
    answers, closes = disposal.answers, disposal.closes
    path = reports_path(routines_home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path.with_suffix(".lock")):
            rows = read_reports(path)
            if target and not answers and not disposal.settles and not supersedes:
                open_ids = open_threads(rows, routine=routine, target=target)
                if len(open_ids) >= OPEN_THREAD_CAP:
                    raise ThreadCapError(routine, target, open_ids)
            folded, _ = supersedable(rows, list(supersedes))
            item_id = next_id(path)
            _append(path, {"id": item_id, "ts": now_iso(), "routine": routine, "run_id": run_id,
                           "title": title[:TITLE_MAX], "detail": detail[:DETAIL_MAX],
                           **({"target": target} if target else {}),
                           **({"answers": answers} if answers else {}),
                           **({"closes": True} if closes else {}),
                           **({"supersedes": folded} if folded else {}),
                           **({"settles": list(disposal.settles)} if disposal.settles else {})})
            for old in folded:
                _append(path, {"id": old, "event": "superseded", "ts": now_iso(),
                               "by": item_id, **({"to": target} if target else {})})
        if target and target_dir is not None:
            atomic_write_json(target_dir / "inbox" / f"msg-rep-{item_id}.json", {
                "text": message_text(item_id, routine, title[:TITLE_MAX], detail[:DETAIL_MAX],
                                     disposal),
                "ts": now_iso(), "via": "report", "report": item_id, "from": routine,
                # A closure asks nothing, so it must not BUY the target a run: the report
                # trigger skips it (daemon/triggers) and it is read by the next run that
                # happens anyway. Delivery is unchanged — only the waking is.
                **({"closes": True} if closes else {})})
    except OSError:
        return None
    return path, item_id, folded


def stamp_delivered(routines_home: Path, msgs: list[dict], *, run_id: str) -> list[str]:
    """Record that the target's run drained these messages, and return the ids it now OWES a
    reply. Called at every drain (boot and turn boundary) with the messages just consumed;
    anything that is not a delivered report is ignored. Best-effort — a missing stamp costs
    visibility, never the delivery itself.

    A CLOSURE is delivered but owed nothing back: it says so in its own text, and asking the
    run to answer "thanks, done" is the ratchet `closes` exists to stop. So it is stamped like
    any other delivery and left out of what comes back.
    """
    ids = [str(m["report"]) for m in msgs if m.get("report")]
    owed = [str(m["report"]) for m in msgs if m.get("report") and not m.get("closes")]
    if not ids:
        return []
    ts = now_iso()
    path = reports_path(routines_home)
    try:
        with file_lock(path.with_suffix(".lock")):
            for item_id in ids:
                _append(path, {"id": item_id, "event": "delivered", "ts": ts, "run_id": run_id})
    except OSError:
        return owed
    return owed


def retract_report(routines_home: Path, report_id: str) -> dict:
    """Withdraw an addressed report the recipient has NOT yet consumed — the outbox's one
    write (D74). Unlinks the pending `msg-rep-*.json` from the target's inbox and appends a
    `retracted` event row under the same ledger lock as every append; the report row itself
    stays untouched. Returns the folded row as it was before retraction.

    Raises LookupError for an unknown id and ValueError for a row that cannot be retracted
    (unaddressed, already consumed, already retracted) — the web layer maps them to 404/409.
    """
    path = reports_path(routines_home)
    with file_lock(path.with_suffix(".lock")):
        row = next((r for r in read_reports(path) if str(r.get("id")) == report_id), None)
        if row is None:
            raise LookupError(f"no report {report_id!r}")
        if not row.get("target"):
            raise ValueError(f"{report_id} is unaddressed — there is no pending delivery "
                             "to retract")
        if row.get("retracted"):
            raise ValueError(f"{report_id} is already retracted")
        if row.get("delivered"):
            raise ValueError(f"{report_id} was already picked up by the target — a consumed "
                             "message cannot be retracted")
        inbox = Path(routines_home) / str(row["target"]) / "inbox"
        try:
            (inbox / f"msg-rep-{report_id}.json").unlink()
        except FileNotFoundError:
            # No delivered stamp, yet the file is gone: an existing inbox means a drain got
            # there first (the stamp lags the rename by an instant) — refuse. A target whose
            # inbox no longer exists can never consume it, so the retraction stands.
            if inbox.is_dir():
                raise ValueError(f"{report_id} was already picked up by the target — a "
                                 "consumed message cannot be retracted") from None
        _append(path, {"id": report_id, "event": "retracted", "ts": now_iso()})
    return row


def discard_undelivered_report(routines_home: Path, report_id: str) -> dict:
    """Operator-discard an addressed report that was NEVER delivered — an orphan: a row with a
    target but no `inbox/msg-rep-<id>.json`, appended straight to the stream so no run can ever
    drain it (the "addressed, never delivered" banner, readmodels/orphans.find_undelivered).
    Appends a `retracted` event under the ledger lock — reusing the existing fold vocabulary, so
    the row reads `dropped` and leaves both the banner and the backlog with no read-model change.

    The MIRROR of retract_report, guarded by the opposite precondition: retract withdraws a
    delivery that is STILL WAITING in the inbox (and refuses once the file is gone); discard
    clears a row whose delivery is genuinely ABSENT (and refuses while a pending file exists —
    that one is retract's to withdraw). Returns the folded row before discard. Raises LookupError
    for an unknown id and ValueError for a row that is not an undelivered orphan — the web layer
    maps them to 404/409.
    """
    path = reports_path(routines_home)
    with file_lock(path.with_suffix(".lock")):
        row = next((r for r in read_reports(path) if str(r.get("id")) == report_id), None)
        if row is None:
            raise LookupError(f"no report {report_id!r}")
        if not row.get("target"):
            raise ValueError(f"{report_id} is unaddressed — there is no delivery to discard")
        if row.get("retracted"):
            raise ValueError(f"{report_id} is already retracted")
        if row.get("delivered"):
            raise ValueError(f"{report_id} was delivered — it is not an undelivered orphan")
        inbox = Path(routines_home) / str(row["target"]) / "inbox"
        if (inbox / f"msg-rep-{report_id}.json").exists():
            raise ValueError(f"{report_id} has a delivery still waiting in {row['target']}'s "
                             "inbox — retract it instead of discarding")
        _append(path, {"id": report_id, "event": "retracted", "ts": now_iso()})
    return row


def read_reports(path: Path) -> list[dict]:
    """The stream folded into one row per report, in filing order. A `delivered`, `retracted`
    or `superseded` event row is merged into its report as a `delivered: {ts, run_id}` /
    `retracted: {ts}` / `superseded: {ts, by, to}` key; an event with no matching report (a
    truncated or hand-trimmed file) is dropped rather than becoming a phantom item.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    reports: dict[str, dict] = {}
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or not (item_id := str(row.get("id") or "")):
            continue
        if row.get("event") == "delivered":
            if item_id in reports:
                reports[item_id]["delivered"] = {"ts": row.get("ts", ""),
                                                 "run_id": row.get("run_id", "")}
        elif row.get("event") == "retracted":
            if item_id in reports:
                reports[item_id]["retracted"] = {"ts": row.get("ts", "")}
        elif row.get("event") == "superseded":
            # FIRST fold wins: a row belongs to exactly one thread, and re-stamping it would
            # let a later carrier steal a row an earlier one is already answering for.
            if item_id in reports and not reports[item_id].get("superseded"):
                reports[item_id]["superseded"] = {"ts": row.get("ts", ""),
                                                  "by": str(row.get("by") or ""),
                                                  "to": str(row.get("to") or "")}
        else:
            reports.setdefault(item_id, dict(row))
    return list(reports.values())
