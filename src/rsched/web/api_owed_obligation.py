"""The operator's decision on an obligation that WAITS ON HIM (2026-11-19).

A routine run that leaves a goal unmet says who its next move belongs to — `b<n> waits on
operator: <his act>` (engine/accounting.py) — and the daily owed-work sweep turns each such
obligation into ONE `owed-obligation` card on the Decisions page (daemon/owed_watch.py). Before
this, those obligations sat in the routine's digest and on the blocked-work list as the
ROUTINE'S overdue work, which no run of it could ever clear: an approval of a video only he can
give, a payment only he can make, a host change no sandbox reaches.

Three decisions, executed here because the queue's one materialize route dispatches by kind
(web/api_pending.py, at its size bar):

- `met` — he says it is done. The obligation leaves the routine's file as settled by him.
- `drop` — no longer wanted. Same, recorded as dropped. These two ARE the person's later words
  that `obligations.py` requires to close an obligation, so neither needs a run.
- `keep` — still wanted, he will act. Nothing changes in the routine; the next card waits
  `owed_watch.KEPT_DAYS`.

The routine is told by an ordinary inbox message its next run drains, so the run that reads it
knows WHY the obligation it carried has gone.
"""

from __future__ import annotations

from fastapi import HTTPException

from ..daemon import owed_watch
from ..engine import inbox, obligations
from ..ids import now_iso
from ..paths import atomic_write_json, read_json

_WORDS = {"met": "the operator marked it DONE", "drop": "the operator DROPPED it — no longer "
          "wanted", "keep": "the operator says it is still wanted and the next move is his"}


def decide(server, rec: dict, body) -> dict:
    """Carry out one decision on an `owed-obligation` card (`body`: api_pending.Choice); the
    pending record is dropped by the caller once this returns.
    """
    choice = body.choice if body is not None else ""
    note = (body.note if body is not None else "").strip()[:500]
    f = rec.get("fields") or {}
    slug, ob_id, from_run = str(f.get("routine") or ""), str(f.get("id") or ""), \
        str(f.get("from_run") or "")
    home = server.routines_home
    routine_dir = home / slug
    if choice not in _WORDS:
        raise HTTPException(400, "an obligation is marked met, dropped, or kept")
    if choice == "keep":
        path = home / owed_watch.KEPT_FILE
        kept = read_json(path)
        kept = kept if isinstance(kept, dict) else {}
        kept[owed_watch.card_key(f)] = now_iso()[:10]
        atomic_write_json(path, kept)
    elif not obligations.settle(routine_dir, ob_id, from_run,
                                how="met" if choice == "met" else "dropped", note=note):
        raise HTTPException(409, f"{slug} no longer holds {ob_id} open — a run met or dropped "
                                 "it since the card was filed")
    if (routine_dir / "routine.yaml").is_file():
        said = f" His note: {note}" if note else ""
        inbox.file_message(routine_dir,
                           f"[obligation {ob_id}] “{f.get('text', '')}” — {_WORDS[choice]} on "
                           f"the Decisions page.{said}",
                           via="pending", name=f"pending-{rec['id']}")
    return {"obligation": ob_id, "routine": slug, "decision": choice}
