"""Where a run's TRANSCRIPT may be CUT, and the one truncation that does it.

Three callers cut a transcript and none may write a second truncation: the D69 rewind (⟲,
keep THROUGH a turn), conversation branching (copy up to the same turn boundary, F325) and
the operator's refusal flag (⚑, keep everything BEFORE the message that opened a reply,
operator decision 2026-10-01). The boundaries are facts read off the transcript, so they live
beside the truncation that acts on them; replaying what a cut leaves is `history.py`'s job.
"""

from __future__ import annotations

import json
from pathlib import Path


def cut_index_for_turn(events: list[dict], turn: int) -> int | None:
    """The event index that closes `turn` — its assistant_action, plus the observation that
    answered it when there is one. None when the turn is not in the transcript.

    The one definition of a clean TURN BOUNDARY in a transcript, shared by the D69 rewind
    (which truncates there) and conversation branching (which copies up to there, F325). Both
    need a prefix that replays into paired messages; cutting mid-turn leaves an assistant action
    with no result, which `replay_messages` would hand the model as a dangling turn.
    """
    for i, ev in enumerate(events):
        if ev.get("type") == "assistant_action" and ev.get("turn") == turn:
            if i + 1 < len(events) and events[i + 1].get("type") == "observation":
                return i + 1
            return i
    return None


def _user_message(ev: dict) -> bool:
    """Prose from the person's side of a conversation — never an ENGINE NOTE, a slash command
    (an action, not prose) or a report a sibling routine addressed here.
    """
    p = ev.get("payload") or {}
    return (ev.get("type") == "user_injection" and bool(str(p.get("text") or "").strip())
            and p.get("source") != "engine" and not p.get("command") and not p.get("report"))


def _reauthored_note(ev: dict) -> bool:
    """A note `boot` writes afresh on every leg (`replay: false`): the resume framing, the
    orphaned-children list. A cut just before a leg's message takes these with it, or the
    next leg's boot would set a second copy beside them.
    """
    p = ev.get("payload") or {}
    return (ev.get("type") == "user_injection" and p.get("source") == "engine"
            and p.get("replay") is False)


def _leg_start(events: list[dict], fin: int) -> tuple[int, bool]:
    """Where the leg that ended at `fin` began, and whether it is the conversation's FIRST:
    just past the previous reply's finish, or past a branch's inherited history (its header's
    `branched_from` turn, F325) — whichever is later — else just past the header.
    """
    bounds = [i for i in range(fin) if events[i].get("type") == "finish"]
    head = events[0] if events and events[0].get("type") == "header" else {}
    fork = (head.get("branched_from") or {}).get("turn")
    if isinstance(fork, int) and (cut := cut_index_for_turn(events, fork)) is not None \
            and cut < fin:
        bounds.append(cut)
    return (max(bounds) + 1, False) if bounds else (1 if head else 0, True)


def reply_opening(events: list[dict], turn: int, ts: str) -> dict | None:
    """The reply whose finish ran on `turn` at `ts`, read back to the message that OPENED it —
    for the operator's refusal flag (docs/conversations.md), which discards a reply and
    re-sends that message to another model. None when no finish carries both. `turn` alone
    cannot name a reply: one that ran no turn of its own (an endpoint failure, a classifier
    refusal — exactly what gets flagged) finishes on the counter of the reply before it.

    - `messages`: what the user sent between the leg's start and its first turn, in order —
      a message injected mid-reply came after the reply began, so it opened nothing.
    - `first`: no earlier reply precedes it, so its message is the conversation's first —
      instruction.md, which no event carries.
    - `keep`: how many events stay — everything before the first message, less the notes the
      boot that delivered it re-authors; for a first reply the header alone. None when no
      message opened a later reply (a leg resumed without one).
    - `served_by`: the model its last turn's usage attributes it to ("" with no turn).
    """
    fin = next((i for i, e in enumerate(events) if e.get("type") == "finish"
                and e.get("turns") == turn and str(e.get("ts") or "") == ts), None)
    if fin is None:
        return None
    start, first = _leg_start(events, fin)
    turns = [i for i in range(start, fin) if events[i].get("type") == "assistant_action"]
    opened = [i for i in range(start, turns[0] if turns else fin) if _user_message(events[i])]
    keep: int | None = start if first else None
    if opened and not first:
        keep = opened[0]
        while keep > start and _reauthored_note(events[keep - 1]):
            keep -= 1
    usage = events[turns[-1]].get("usage") if turns else None
    return {"finish": events[fin], "messages": [events[i] for i in opened], "first": first,
            "keep": keep,
            "served_by": str(usage.get("model") or "") if isinstance(usage, dict) else ""}


def rewind_transcript(run_dir: Path, keep_through_turn: int | None = None, *,
                      before_reply: tuple[int, str] | None = None) -> dict | None:
    """D69: rewind a conversation so a dead/derailed run can be RE-OPENED and continued from a
    chosen point instead of being lost. ONE truncation, two cut points:

    - `keep_through_turn` keeps every event up to and INCLUDING that turn's assistant_action
      and the observation that answered it (`cut_index_for_turn`), dropping every later turn
      and any trailing finish/error — the per-reply ⟲.
    - `before_reply=(turn, ts)` keeps everything BEFORE the message that opened the reply
      whose finish ran on `turn` at `ts` (`reply_opening`): the message, the reply and all
      after it go. The operator's refusal flag cuts here because it re-sends that message —
      for a conversation's FIRST reply that is the very start, the header alone.

    The discarded tail is moved to a timestamped `rewind-<ts>.jsonl` sibling — auditable and
    reversible by hand — result.md goes back to the last reply kept, and a `runner.resume` on
    the same run dir replays what is kept, live, with a fresh budget window. Returns a summary
    (kept/dropped counts, the last kept turn, the archive name), or None when there is no such
    cut point or nothing after it.
    """
    from ..ids import now_iso
    from ..paths import atomic_write
    from .transcript import read_events

    tpath = run_dir / "transcript.jsonl"
    events, _ = read_events(tpath, 0)
    keep: int | None
    if before_reply is not None:
        opening = reply_opening(events, *before_reply)
        keep = opening["keep"] if opening else None
    else:
        cut = None if keep_through_turn is None else cut_index_for_turn(events, keep_through_turn)
        keep = None if cut is None else cut + 1
    if keep is None or keep >= len(events):
        return None   # no such cut point, or nothing after it to drop
    kept, dropped = events[:keep], events[keep:]
    archive = f"rewind-{now_iso().replace(':', '').replace('-', '')}.jsonl"
    atomic_write(run_dir / archive,
                 "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in dropped))
    atomic_write(tpath,
                 "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept))
    # result.md is the run's LAST REPLY, which the next leg's state digest reads back as
    # "Last run result": left alone, a reply this cut discarded would still speak from it.
    reply = next((e for e in reversed(kept) if e.get("type") == "finish"), None)
    if reply is not None:
        atomic_write(run_dir / "result.md",
                     str((reply.get("payload") or {}).get("summary") or "") + "\n")
    else:
        (run_dir / "result.md").unlink(missing_ok=True)
    last = max((e["turn"] for e in kept if e.get("type") == "assistant_action"
                and isinstance(e.get("turn"), int)), default=0)
    return {"kept_events": len(kept), "dropped_events": len(dropped),
            "kept_through_turn": last, "archive": archive}
