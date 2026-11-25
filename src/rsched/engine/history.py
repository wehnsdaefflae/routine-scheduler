"""Facts derived from a run's TRANSCRIPT — what a resumed leg rebuilds from the record rather
than from a live loop.

`replay_messages` turns the events back into the message list the model read (resume), and
the rest recover what a fresh RunContext would otherwise forget: children that died with the
process, earlier legs' spend and counters, the paths a run has seen, and the stage modules it
has entered. Where a rewind, a branch or the refusal flag may CUT a transcript is `rewind.py`.
Shrinking a LIVE message list lives in `compaction.py` / `window.py`.
"""

from __future__ import annotations

import json

from ..endpoints.usage import fold_usage
from ..paths import resolve_rel
from . import enginenote, obstails
from .compaction import turn_record
from .observations import format_observation


def replay_messages(events: list[dict]) -> tuple[list[dict], int, list[dict]]:
    """Rebuild the (turn-pair) message list from a run's transcript events — for RESUME. Returns
    (messages, last_turn, turn_records); the caller prepends the freshly-composed system message.
    Every turn is replayed (compaction events are ignored — this reconstitutes the full
    conversation and maybe_compact re-compacts it on the next turn if it's too big).

    Two live message feeds have no 1:1 event: child-exit ANNOUNCEMENTS are reconstituted
    from `child_end` events (flushed where the live message sat — before the next
    assistant turn; a child whose summary rode a `wait` observation is not re-announced),
    and blocking-ask answers are NOT replayed from `answer` events — the answer text
    already lives inside the ask_user observation, so replaying both would duplicate it.

    Every message comes from the SAME renderer the live path used, so the resumed prefix is
    byte-identical and stays a provider cache hit.
    """
    from .control import child_finished_message, command_message, injected_message

    messages: list[dict] = []
    records: list[dict] = []
    last_turn = 0
    # child_end events whose announcement has not been placed yet: n → payload
    pending_children: dict[int, dict] = {}
    # a slash command's text, waiting for the observation it was recorded apart from: live it
    # is ONE message (command_message), so the replay has to put the pair back together
    pending_command: str | None = None

    def flush_children() -> None:
        messages.extend({"role": "user", "content": child_finished_message(
            mode=str(p.get("mode") or "parallel"), n=int(p.get("n") or 0),
            label=str(p.get("label") or ""), workflow=str(p.get("workflow") or ""),
            status=str(p.get("status") or "?"), turns=int(p.get("turns") or 0),
            summary=str(p.get("summary") or ""),
            collected=tuple(p.get("collected") or ()))} for p in pending_children.values())
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
                records.append(turn_record(turn, p))
        elif kind_ev == "observation":
            if p.get("kind") == "wait":
                # a child listed in this wait's finished rows was delivered BY it —
                # replaying an announcement on top would double its summary
                for row in p.get("finished") or []:
                    if isinstance(row, dict) and isinstance(row.get("n"), int):
                        pending_children.pop(row["n"], None)
            if p.get("user_command"):
                messages.append({"role": "user",
                                 "content": command_message(pending_command or "", p)})
                pending_command = None
                continue
            if p.get("media"):
                # the replay rebuilds TEXT-only messages, so the media is not re-attached:
                # say so instead of replaying "attached below for you to see" over nothing
                p = {k: v for k, v in p.items() if k != "media"} | {"media_replayed": True}
            # …plus whatever RODE that message (engine/obstails.py), recorded verbatim live
            messages.append({"role": "user",
                             "content": format_observation(p) + obstails.of(p)})
        elif kind_ev == "user_injection":
            if p.get("command"):
                pending_command = str(p.get("text", ""))
                continue
            if p.get("source") == "engine":
                # an ENGINE NOTE, not the user: replay it as the note, never as a user message
                if p.get("replay") is False:
                    continue        # boot re-authors this note on every leg; see enginenote
                messages.append({"role": "user",
                                 "content": enginenote.message(str(p.get("text", "")))})
                continue
            messages.append({"role": "user", "content": injected_message(
                str(p.get("text", "")), report=bool(p.get("report")),
                via=str(p.get("via") or ""))})
        elif kind_ev == "child_end":
            n = p.get("n")
            if isinstance(n, int):
                pending_children[n] = p
        elif kind_ev == "finish":
            # the run concluded knowing (or not needing) these — never re-announce after
            pending_children.clear()
        # header / question / answer / compaction / error / child_start stay out of the prompt
    flush_children()   # a crash between _collect and the next turn still delivers the result
    return messages, last_turn, records


def orphaned_children(events: list[dict]) -> list[dict]:
    """Children that were RUNNING when the run was interrupted — a `child_start` (subtask or
    subrun) with no matching `child_end`. Children are threads in the parent process, so they do
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
        if ev.get("type") == "child_start":
            started[n] = {"n": n, "label": p.get("label"), "mode": p.get("mode", "parallel"),
                          "workflow": p.get("workflow", "")}
        elif ev.get("type") == "child_end":
            ended.add(n)
    return [info for n, info in started.items() if n not in ended]


def prior_usage(events: list[dict]) -> dict:
    """Token spend recorded across ALL prior legs of a run's transcript — the resume base, since
    a resume starts a fresh budget window (ctx.usage). Sums every event that carries usage:
    assistant actions, llm-subcall and decide observations, compaction calls, and the exit of
    every CHILD RUN an earlier leg collected — a child's spend is folded live at collection
    (`subruns._collect`), so omitting `child_end` here understates a resumed tree (R3112).
    """
    total: dict = {"in": 0, "out": 0}
    for ev in events:
        etype = ev.get("type")
        if etype == "assistant_action":
            u = ev.get("usage")
        # The two RETIRED kinds (D288) booked their spend in the observation payload, so a
        # leg recorded before that retirement still counts here — a resumed run's prior usage
        # must not shrink because the kind that bought it no longer exists.
        elif ((etype == "observation"
               and (ev.get("payload") or {}).get("kind") in ("llm", "decide"))
              or etype in ("compaction", "child_end")):
            u = (ev.get("payload") or {}).get("usage")
        else:
            continue
        if not isinstance(u, dict):
            continue
        fold_usage(total, u)
    return total


def rehydrate_seen_paths(ctx, events: list[dict]) -> None:
    """Rebuild `write_file`'s grounding set on RESUME — the paths earlier legs read stay
    overwritable, and deletable, after an interruption.

    A root revoked between legs simply drops out of the set (`resolve_rel` raises).
    """
    roots = [*ctx.routine.fs_read_roots, *ctx.routine.fs_write_roots]
    for rel in seen_paths(events):
        try:
            ctx.seen_paths.add(str(resolve_rel(ctx.routine.dir, rel, roots)))
        except (OSError, PermissionError):
            continue


def seen_paths(events: list[dict]) -> list[str]:
    """Path strings (as the actions gave them) that earlier legs read, viewed, or wrote —
    successful read_file / view_image / write_file / edit_file observations, plus a
    read_file refusal that carries `size` (a binary file, or one over the read cap): that
    is a STAT the run has seen, and fileops._refusal grounds it live.
    """
    out: list[str] = []
    for ev in events:
        if ev.get("type") != "observation":
            continue
        p = ev.get("payload") or {}
        kind = p.get("kind")
        if kind in ("read_file", "view_image"):
            entries = p.get("files") or ([p] if p.get("path") else [])
            out.extend(str(f["path"]) for f in entries
                       if f.get("path") and (not f.get("error") or "size" in f))
        elif kind in ("write_file", "edit_file") and p.get("path") and not p.get("error"):
            out.append(str(p["path"]))
    return out


# Cumulative per-run telemetry counters mirrored to status.json and the finish/usage record,
# reseeded on resume so a leg does not reset them to its own tally (as usage_base does tokens).
_RESUME_COUNTER_FIELDS = ("asks_deferred", "schema_retries", "schema_forcefails", "referrals",
                          "claims_challenged", "claims_disputed", "holds", "interventions",
                          "refusal_level", "takeover_left")


def prior_counters(status: dict) -> dict:
    """Cumulative telemetry counters to reseed onto the RunContext on RESUME, read from the
    prior leg's status.json (the run dir is reused across legs). Returns {RunContext
    attribute: value}; malformed fields are skipped, util cells are deep-copied.
    """
    out: dict = {}
    utils = status.get("utils")
    if isinstance(utils, dict):
        cells = {k: dict(v) for k, v in utils.items() if isinstance(v, dict)}
        if cells:
            out["util_stats"] = cells
    # not a counter, same problem: a resumed leg must still know which reports it owes replies
    owed = status.get("reports_open")
    if isinstance(owed, list):
        out["reports_open"] = [str(r) for r in owed]
    # …and the stages ENTERED are the run's, not the leg's (else earlier work reads as skipped)
    stages = status.get("stages")
    entered = stages.get("entered") if isinstance(stages, dict) else None
    if isinstance(entered, list):
        out["phases_entered"] = [str(s) for s in entered]
    for fld in _RESUME_COUNTER_FIELDS:
        val = status.get(fld)
        if isinstance(val, int) and not isinstance(val, bool):
            out[fld] = val
    if legs := prior_legs(status):
        out["prior_legs"] = legs
    return out


#: How many prior legs' windows status.json keeps: a resume chain is bounded at three windows
#: (`turnwindow.TOTAL_TURN_MULTIPLE`), plus the one that re-armed it.
MAX_PRIOR_LEGS = 4


def prior_legs(status: dict) -> list[dict]:
    """The windows of every leg BEFORE the one about to boot, oldest first.

    A leg's own `write_status` replaces the whole `budgets` block, so without this the window a
    previous leg ran under (e.g. a task leg before a 12-turn revise leg) is lost. Each leg's
    block is appended as it is displaced; nothing is back-filled.
    """
    block = status.get("budgets")
    if not isinstance(block, dict):
        return []
    kept = [leg for leg in block.get("legs", []) if isinstance(leg, dict)]
    window = {k: block.get(k) for k in ("base_turn", "window_turns", "max_turns",
                                        "max_total_turns", "total_turns", "turns_left")}
    if all(v is None for v in window.values()):
        return kept[-MAX_PRIOR_LEGS:]
    return [*kept, window][-MAX_PRIOR_LEGS:]
