"""Work orders: the inter-routine referral channel — one routine addresses a durable message
to another, which reads it on its NEXT SCHEDULED RUN.

A work order NEVER starts a run — it only ever writes a file into the target's inbox, which
the target's next scheduled run drains. Keep it that way: the mechanism exists so a routine
that finds a problem outside its own remit can put the work where its OWNER will find it
WITHOUT seizing that owner's schedule.

Two artefacts per order, and they are deliberately separate:

- **The ledger** — `<routines_home>/.control/work-orders.jsonl`, append-only, one `W<n>` id
  per order (assigned here under the same advisory lock as the append, like `R<n>` bug ids).
  This is what makes a hand-off VISIBLE: the Items page reads it, so the user can see that
  routine A sent work to routine B and whether B picked it up.
- **The delivery** — a `msg-wo-<id>.json` file in the TARGET's `inbox/`, the same durable
  shape the trigger and one-shot managers write. The target's next run drains it at boot.

The ledger is append-only, so the lifecycle is recorded as EVENT rows folded by
`read_work_orders`: the order row itself, then a `delivered` row stamped when the target's
run actually drains the message. Closure is a work order back the other way carrying
`answers: "<id>"` — the receiving routine acting on it, or saying why it will not, is itself
a hand-off and belongs in the same ledger.

This is not `report_bug`. That channel is ungated, aims at the SCHEDULER, and is polled by
self-audit out of `.control/bug-reports.jsonl`; a work order is gated, aims at a ROUTINE, and
is DELIVERED into that routine's prompt. Keeping them apart is what stops `report_bug` from
being a defect log, a routing note and a retraction channel at once.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .ids import now_iso
from .paths import atomic_write_json, file_lock

WORK_ORDERS_FILE = "work-orders.jsonl"
WORK_ORDER_ID_RE = re.compile(r"^W(\d+)$")

#: Cap on the stored prose, mirroring the bug-report stream's caps.
TITLE_MAX = 300
DETAIL_MAX = 4000


def work_orders_path(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / WORK_ORDERS_FILE


def _append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def next_id(path: Path) -> str:
    """The next free `W<n>`: one past the highest id in the stream. The counter lives IN the
    data — no sidecar to drift out of sync with a restored or hand-edited file.
    """
    highest = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "W1"
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = WORK_ORDER_ID_RE.match(str(row.get("id") or "")) if isinstance(row, dict) else None
        if m:
            highest = max(highest, int(m.group(1)))
    return f"W{highest + 1}"


def message_text(item_id: str, sender: str, title: str, detail: str, answers: str = "") -> str:
    """The prose a delivered work order becomes in the target's prompt — one wording, used by
    the boot drain and the mid-run injection alike so a resumed prompt reads like a live one.
    """
    head = f"WORK ORDER {item_id} from routine `{sender}`"
    if answers:
        head += f" (answering {answers})"
    body = [head, "", title]
    if detail:
        body += ["", detail]
    return "\n".join(body)


def file_work_order(routines_home: Path, *, sender: str, run_id: str, target: str,
                    target_dir: Path, title: str, detail: str = "",
                    answers: str = "") -> tuple[str, Path] | None:
    """Record one work order and deliver it into `target_dir/inbox/`.

    Returns `(id, inbox_path)`, or None if the write failed. Unlike `report_bug`'s
    best-effort append, a failure here is REPORTED to the sending run: the hand-off is the
    action's whole purpose, and a run that believes it routed work when it did not will not
    route it again.
    """
    path = work_orders_path(routines_home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path.with_suffix(".lock")):
            item_id = next_id(path)
            _append(path, {"id": item_id, "ts": now_iso(), "from": sender, "run_id": run_id,
                           "to": target, "title": title[:TITLE_MAX],
                           "detail": detail[:DETAIL_MAX],
                           **({"answers": answers} if answers else {})})
        inbox_path = target_dir / "inbox" / f"msg-wo-{item_id}.json"
        atomic_write_json(inbox_path, {
            "text": message_text(item_id, sender, title[:TITLE_MAX], detail[:DETAIL_MAX],
                                 answers),
            "ts": now_iso(), "via": "work-order", "work_order": item_id, "from": sender})
    except OSError:
        return None
    return item_id, inbox_path


def stamp_delivered(routines_home: Path, msgs: list[dict], *, run_id: str) -> None:
    """Record that the target's run drained these messages. Called at every drain (boot and
    turn boundary) with the messages just consumed; non-work-order messages are ignored.
    Best-effort — a missing stamp costs visibility, never the delivery itself.
    """
    ids = [str(m["work_order"]) for m in msgs if m.get("work_order")]
    if not ids:
        return
    path = work_orders_path(routines_home)
    ts = now_iso()
    try:
        with file_lock(path.with_suffix(".lock")):
            for item_id in ids:
                _append(path, {"id": item_id, "event": "delivered", "ts": ts, "run_id": run_id})
    except OSError:
        return


def read_work_orders(path: Path) -> list[dict]:
    """The stream folded into one row per order, in filing order. A `delivered` event row is
    merged into its order as a `delivered: {ts, run_id}` key; an event with no matching order
    (a truncated or hand-trimmed file) is dropped rather than becoming a phantom item.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    orders: dict[str, dict] = {}
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
            if item_id in orders:
                orders[item_id]["delivered"] = {"ts": row.get("ts", ""),
                                                "run_id": row.get("run_id", "")}
        else:
            orders.setdefault(item_id, dict(row))
    return list(orders.values())
