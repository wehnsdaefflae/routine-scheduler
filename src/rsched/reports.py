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
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, file_lock

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


def message_text(item_id: str, sender: str, title: str, detail: str, answers: str = "",
                 closes: bool = False) -> str:
    """The prose a delivered report becomes in the target's prompt — one wording, used by the
    boot drain and the mid-run injection alike so a resumed prompt reads like a live one.
    """
    head = f"REPORT {item_id} from routine `{sender}`"
    if answers and closes:
        head += f" (answering {answers} — closes the exchange, no reply needed)"
    elif answers:
        head += f" (answering {answers})"
    body = [head, "", title]
    if detail:
        body += ["", detail]
    return "\n".join(body)


def file_report(routines_home: Path, *, routine: str, run_id: str, title: str, detail: str = "",
                target: str = "", target_dir: Path | None = None,
                answers: str = "", closes: bool = False) -> tuple[Path, str] | None:
    """Append one report, and deliver it when it is addressed.

    Returns `(path, id)` on success, or None if the write failed. An UNADDRESSED report is
    best-effort like the health log — a failed write must never abort the reporting run, whose
    real job is elsewhere. An ADDRESSED one is the caller's whole purpose, so the handler
    surfaces the failure instead of letting the run believe it routed work it did not.

    `closes` is recorded only beside `answers` (validate_action enforces the pairing for the
    action; this guard keeps out-of-band callers equally honest): a closure row is the
    exchange's terminal acknowledgment, born settled.
    """
    closes = bool(closes and answers)
    path = reports_path(routines_home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path.with_suffix(".lock")):
            item_id = next_id(path)
            _append(path, {"id": item_id, "ts": now_iso(), "routine": routine, "run_id": run_id,
                           "title": title[:TITLE_MAX], "detail": detail[:DETAIL_MAX],
                           **({"target": target} if target else {}),
                           **({"answers": answers} if answers else {}),
                           **({"closes": True} if closes else {})})
        if target and target_dir is not None:
            atomic_write_json(target_dir / "inbox" / f"msg-rep-{item_id}.json", {
                "text": message_text(item_id, routine, title[:TITLE_MAX], detail[:DETAIL_MAX],
                                     answers, closes),
                "ts": now_iso(), "via": "report", "report": item_id, "from": routine})
    except OSError:
        return None
    return path, item_id


def stamp_delivered(routines_home: Path, msgs: list[dict], *, run_id: str) -> None:
    """Record that the target's run drained these messages. Called at every drain (boot and
    turn boundary) with the messages just consumed; anything that is not a delivered report is
    ignored. Best-effort — a missing stamp costs visibility, never the delivery itself.
    """
    ids = [str(m["report"]) for m in msgs if m.get("report")]
    if not ids:
        return
    ts = now_iso()
    path = reports_path(routines_home)
    try:
        with file_lock(path.with_suffix(".lock")):
            for item_id in ids:
                _append(path, {"id": item_id, "event": "delivered", "ts": ts, "run_id": run_id})
    except OSError:
        return


def read_reports(path: Path) -> list[dict]:
    """The stream folded into one row per report, in filing order. A `delivered` event row is
    merged into its report as a `delivered: {ts, run_id}` key; an event with no matching report
    (a truncated or hand-trimmed file) is dropped rather than becoming a phantom item.
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
        else:
            reports.setdefault(item_id, dict(row))
    return list(reports.values())
