"""Prompt-size management: deterministic compaction, LLM-driven history archival, and
transcript replay for resume.

Compaction shrinks only the in-prompt conversation — the transcript on disk keeps
everything. `compact_to_history` reorganizes the elided middle into navigable markdown
files under runs/<ts>/history/ that the model reads back on demand; `maybe_compact` is
the deterministic one-line-digest fallback when the LLM path fails.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..endpoints.base import fold_usage
from .actionschema import brief_value
from .observations import format_observation


def replay_messages(events: list[dict]) -> tuple[list[dict], int, list[dict]]:
    """Rebuild the (turn-pair) message list from a run's transcript events — for RESUME. Returns
    (messages, last_turn, turn_records); the caller prepends the freshly-composed system message.
    Every turn is replayed (compaction events are ignored — this reconstitutes the full
    conversation and maybe_compact re-compacts it on the next turn if it's too big).

    Two live message feeds have no 1:1 event: child-exit ANNOUNCEMENTS are reconstituted
    from `subrun_end` events (flushed where the live message sat — before the next
    assistant turn; a child whose summary rode a `wait` observation is not re-announced),
    and blocking-ask answers are NOT replayed from `answer` events — the answer text
    already lives inside the ask_user observation, so replaying both would duplicate it.
    """
    from .control import child_finished_message, render_command_result

    messages: list[dict] = []
    records: list[dict] = []
    last_turn = 0
    # subrun_end events whose announcement has not been placed yet: n → payload
    pending_children: dict[int, dict] = {}

    def flush_children() -> None:
        messages.extend({"role": "user", "content": child_finished_message(
            mode=str(p.get("mode") or "parallel"), n=int(p.get("n") or 0),
            label=str(p.get("label") or ""), workflow=str(p.get("workflow") or ""),
            status=str(p.get("status") or "?"), turns=int(p.get("turns") or 0),
            summary=str(p.get("summary") or ""))} for p in pending_children.values())
        pending_children.clear()

    for ev in events:
        kind_ev = ev.get("type")
        p = ev.get("payload") or {}
        if kind_ev == "assistant_action":
            flush_children()   # live, announcements precede the model's next action
            messages.append({"role": "assistant", "content": json.dumps(p, ensure_ascii=False)})
            turn = ev.get("turn")
            if isinstance(turn, int):
                last_turn = turn
                brief = brief_value(p)[:80]
                records.append({"turn": turn, "kind": p.get("kind", "?"),
                                "brief": json.dumps(brief, ensure_ascii=False),
                                "say": p.get("say", "")})
        elif kind_ev == "observation":
            if p.get("kind") == "wait":
                # a child listed in this wait's finished rows was delivered BY it —
                # replaying an announcement on top would double its summary
                for row in p.get("finished") or []:
                    if isinstance(row, dict) and isinstance(row.get("n"), int):
                        pending_children.pop(row["n"], None)
            rendered = (render_command_result(p) if p.get("user_command")
                        else format_observation(p))
            messages.append({"role": "user", "content": rendered})
        elif kind_ev == "user_injection":
            label = ("USER COMMAND (executed directly)" if p.get("command")
                     else "USER MESSAGE (injected mid-run)")
            messages.append({"role": "user", "content": f"{label}: {p.get('text', '')}"})
        elif kind_ev == "subrun_end":
            n = p.get("n")
            if isinstance(n, int):
                pending_children[n] = p
        elif kind_ev == "finish":
            # the run concluded knowing (or not needing) these — never re-announce after
            pending_children.clear()
        # header / question / answer / compaction / error / subrun_start stay out of the prompt
    flush_children()   # a crash between _collect and the next turn still delivers the result
    return messages, last_turn, records


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


def rewind_transcript(run_dir: Path, keep_through_turn: int) -> dict | None:
    """D69: rewind a conversation to a chosen turn so a dead/derailed run can be RE-OPENED and
    continued from there instead of being lost. Rewrites runs/<ts>/transcript.jsonl to keep
    every event up to and INCLUDING the assistant_action of `keep_through_turn` and the
    observation that immediately followed it — dropping every later turn (and any trailing
    finish/error). The discarded tail is not destroyed: it is moved to a timestamped
    `rewind-<ts>.jsonl` sibling so the rewind is auditable and reversible by hand.

    A subsequent `runner.resume` on the same run dir replays the truncated transcript, so the
    conversation continues live from the kept point with a fresh budget window. Returns a
    summary dict (kept/dropped counts + archive name), or None when there is nothing to do
    (turn not found, or already the last turn — no tail to drop).
    """
    from ..ids import now_iso
    from ..paths import atomic_write
    from .transcript import read_events

    tpath = run_dir / "transcript.jsonl"
    events, _ = read_events(tpath, 0)
    if not events:
        return None
    cut = cut_index_for_turn(events, keep_through_turn)
    if cut is None or cut >= len(events) - 1:
        return None   # turn not found, or nothing after it to drop
    kept = events[: cut + 1]
    dropped = events[cut + 1 :]
    archive = f"rewind-{now_iso().replace(':', '').replace('-', '')}.jsonl"
    atomic_write(run_dir / archive,
                 "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in dropped))
    atomic_write(tpath,
                 "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in kept))
    return {"kept_events": len(kept), "dropped_events": len(dropped),
            "kept_through_turn": keep_through_turn, "archive": archive}


def orphaned_children(events: list[dict]) -> list[dict]:
    """Children that were RUNNING when the run was interrupted — a `subrun_start` (subtask or
    subrun) with no matching `subrun_end`. Children are threads in the parent process, so they do
    NOT survive a restart: on resume these are dead. Returns [{n, label, mode}] so the engine can
    mark them aborted and tell the model, instead of leaving it to `wait` forever for a child that
    will never finish.
    """
    started: dict[int, dict] = {}
    ended: set[int] = set()
    for ev in events:
        p = ev.get("payload") or {}
        n = p.get("n")
        if not isinstance(n, int):
            continue
        if ev.get("type") == "subrun_start":
            started[n] = {"n": n, "label": p.get("label"), "mode": p.get("mode", "parallel"),
                          "workflow": p.get("workflow", "")}
        elif ev.get("type") == "subrun_end":
            ended.add(n)
    return [info for n, info in started.items() if n not in ended]


def prior_usage(events: list[dict]) -> dict:
    """Token spend recorded across ALL prior legs of a run's transcript. A resume starts a
    fresh budget window (ctx.usage), so without this base status.json under-reports resumed
    runs by however much the earlier legs spent. Sums every event that carries usage:
    assistant actions, llm-subcall observations, and compaction calls.
    """
    total: dict = {"in": 0, "out": 0}
    for ev in events:
        etype = ev.get("type")
        if etype == "assistant_action":
            u = ev.get("usage")
        elif ((etype == "observation" and (ev.get("payload") or {}).get("kind") == "llm")
              or etype == "compaction"):
            u = (ev.get("payload") or {}).get("usage")
        else:
            continue
        if not isinstance(u, dict):
            continue
        fold_usage(total, u)
    return total


def seen_paths(events: list[dict]) -> list[str]:
    """Path strings (as the actions gave them) that earlier legs read, viewed, or wrote —
    successful read_file / view_image / write_file / edit_file observations. Rebuilds
    write_file's grounding set on resume, so a file read before an interruption stays
    overwritable after it.
    """
    out: list[str] = []
    for ev in events:
        if ev.get("type") != "observation":
            continue
        p = ev.get("payload") or {}
        kind = p.get("kind")
        if kind in ("read_file", "view_image"):
            entries = p.get("files") or ([p] if p.get("path") else [])
            out.extend(str(f["path"]) for f in entries if f.get("path") and not f.get("error"))
        elif kind in ("write_file", "edit_file") and p.get("path") and not p.get("error"):
            out.append(str(p["path"]))
    return out


# Cumulative per-run telemetry counters mirrored to status.json (RunContext.write_status) and
# the finish/workflow-usage record. Tokens and turns already carry a resume base (usage_base /
# budget_base_turn); these did NOT, so a resumed leg reset them to its own tally — a
# finish→reopen showed utils:{} + asks_deferred:0 despite the pre-finish leg's real activity
# (F131/F132). The GLOBAL util-stats snapshot is transcript-derived and was always correct;
# this only repairs the per-run status.json + finish event.
_RESUME_COUNTER_FIELDS = ("asks_deferred", "schema_retries", "schema_forcefails", "referrals")


def prior_counters(status: dict) -> dict:
    """Cumulative telemetry counters to reseed onto the RunContext on RESUME, read from the
    prior leg's status.json (the run dir is reused across legs). Keeps status.json and the
    finish event cumulative across legs — like usage_base for tokens — instead of resetting
    the util histogram and the integer counters to the resumed leg's own tally. Returns a
    dict of {RunContext attribute: value}; missing or malformed fields are skipped, and the
    util cells are deep-copied so mutating the live ctx never writes back into the read dict.
    """
    out: dict = {}
    utils = status.get("utils")
    if isinstance(utils, dict):
        cells = {k: dict(v) for k, v in utils.items() if isinstance(v, dict)}
        if cells:
            out["util_stats"] = cells
    for fld in _RESUME_COUNTER_FIELDS:
        val = status.get(fld)
        if isinstance(val, int) and not isinstance(val, bool):
            out[fld] = val
    return out
