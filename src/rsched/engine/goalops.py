"""The `goal` action — how a run keeps its GOALS (engine/goals.py): set, add, change, drop, check,
list.

Every verb answers with the whole ledger after it, and the observation carries it as data
(`ledger`): that snapshot is what a resumed leg, a rewind and a branch read the goals back from
(`goals.replay`). A refusal changes nothing and carries no ledger.

The grounding rule is the module's reason to exist (goals.py says why): `set`/`add` need words
the person wrote this run, `change`/`drop` need words they wrote AFTER the goal's own. `check`
is the run's claim, so it meets the same judge a finish's `met` meets (`verifier.refuted`): a
refuted check leaves the goal open ONCE and says why; checking it again with the challenge spent
stands, and the objection is recorded beside the evidence. The challenge spends the same
once-per-line budget as the finish's (`loop._challenged`), so a goal is never argued twice.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import goalpicks, goals, inbox, runkind, verifier

# `runkind` and `verifier` are imported at module scope deliberately (F658): `goal check` runs
# the SAME claim verification a finish does, so a lazy import here has the same failure as the
# one that cost a run its summary on 2026-10-09 — the engine's in-memory caller meeting a
# freshly-edited module from disk. Neither imports this module back.

VERBS = ("list", "set", "add", "change", "check", "drop")
_ID_RE = re.compile(r"^b\d+$")


def field_problems(obj: dict) -> list[str]:
    """The shape a `goal` action must have. Whether the goal EXISTS, is open, or the quote is
    the person's is the handler's to judge against the ledger and the transcript — this is only
    the grammar, so a malformed call is corrected inside the schema-retry cycle and costs no
    turn.
    """
    verb = str(obj.get("verb") or "")
    if verb not in VERBS:
        return [f"kind=goal requires 'verb' to be one of {list(VERBS)}"]
    out = []
    gid = str(obj.get("id") or "")
    if verb in ("change", "check", "drop") and not _ID_RE.match(gid):
        out.append(f"kind=goal verb={verb} requires 'id' — the goal's id, like b1")
    items = obj.get("goals")
    if verb in ("set", "add", "change"):
        if not isinstance(items, list) or not items:
            out.append(f"kind=goal verb={verb} requires 'goals' — [{{text, quote}}, …]")
        elif any(not isinstance(i, dict) or not str(i.get("text") or "").strip()
                 or not str(i.get("quote") or "").strip() for i in items):
            out.append("kind=goal: every entry of 'goals' needs a non-empty 'text' (the end "
                       "state) AND 'quote' (the person's own words it came from)")
        elif verb == "change" and len(items) != 1:
            out.append("kind=goal verb=change takes exactly ONE entry in 'goals' — the new "
                       "wording of that goal")
    elif items:
        out.append(f"kind=goal verb={verb}: 'goals' belongs to set, add and change")
    if verb == "drop" and not str(obj.get("quote") or "").strip():
        out.append("kind=goal verb=drop requires 'quote' — the person's own words, written after "
                   "the goal was set, that withdraw it")
    if verb == "check" and (not isinstance(obj.get("evidence"), str)
                            or not obj["evidence"].strip()):
        out.append("kind=goal verb=check requires 'evidence' (text) — what shows it is met")
    return out


def _person(loop) -> tuple[list[dict], list[str]]:
    """The transcript's events, and everything the person wrote to this run in order. Both,
    from one read: grounding a numbered PICK needs the questions the events carry, not only
    the words (engine/goalpicks.py).
    """
    ctx = loop.ctx
    opening = str(loop.instruction or "") if runkind.is_conversation(ctx) else ""
    events = goals.transcript_events(ctx.run_dir)
    return events, goals.person_texts(events, opening=opening, brief=ctx.brief)


def answered_texts(routine_dir, *, since: str = "") -> list[str]:
    """Every settled ANSWER this routine's runs received, oldest first — the person's words
    that are not in any one run's transcript (F911).

    An operator answer is the person speaking, and `goals._person_spoke` already counts an
    `answer` event as such; but an answer given to an EARLIER run was consumed by that run, so
    it is in `questions/answered/` and in no transcript this run can read. Without this, a goal
    the operator withdrew could not be retired by the words that withdrew it — reported by
    folder-reorg (R3513/R3569) with a settled "Yes, drop b19 — it is withdrawn" whose drop was
    refused as "not later user words".

    `since` (a YYYY-MM-DD date, what `obligations.save` stamps on a carried goal) bounds the
    read the way `heard_at(after=…)` bounds the transcript: an answer the person gave BEFORE a
    goal was taken on cannot be the reason it changed. Same-day counts — `since` is a date and
    the answer carries a time, so a goal taken on and withdrawn the same day is withdrawn.
    """
    rows = inbox.answered_questions(Path(routine_dir))
    out = []
    for row in reversed(rows):                      # answered_questions is newest first
        text = str(row.get("answer") or "").strip()
        day = str(row.get("answered_ts") or "")[:10]
        if text and (not since or not day or day >= since):
            out.append(text)
    return out


def _heard_later(loop, goal: dict, quote: str) -> bool:
    """Did the person write `quote` after the words this goal came from? The transcript first,
    and for a CARRIED goal the answered-questions store as well — its grounding lies in an
    earlier run (`heard == -1`, what `obligations.carried` stamps), and so may the words that
    withdraw it.
    """
    if goals.heard_at(quote, _person(loop)[1], after=goal["heard"]) is not None:
        return True
    if goal.get("heard") != -1:
        return False
    texts = answered_texts(loop.ctx.routine.dir, since=str(goal.get("since") or ""))
    return goals.heard_at(quote, texts) is not None


def _obs(loop, verb: str, message: str, **extra) -> dict:
    ledger = [dict(g) for g in loop.ctx.goals]
    return {"kind": "goal", "verb": verb,
            "message": f"{message}\nGOALS NOW:\n{goals.listing(ledger)}",
            "ledger": ledger, **extra}


def _refuse(verb: str, reason: str) -> dict:
    return {"kind": "goal", "verb": verb, "rejected": True, "reason": reason}


def _items(action: dict) -> list[tuple[str, str]]:
    return [(str(i.get("text") or "").strip(), str(i.get("quote") or "").strip())
            for i in action.get("goals") or [] if isinstance(i, dict)]


def _not_found(quote: str, *, later: bool, carried: bool = False) -> str:
    where = ("in anything the person wrote AFTER the words this goal came from — an earlier "
             "sentence cannot be the reason a later request changed" if later else
             "in anything the person wrote to this run")
    # A carried goal's words may be in an answer an EARLIER run consumed, so that store was
    # read too (F911): the refusal names every surface searched, or the next run cannot tell
    # a missing quote from an unread store.
    searched = (" Both this run's transcript and the answers this routine has already "
                "received (questions/answered/) were searched." if carried else "")
    return (f"the words {quote!r} are not {where}.{searched} Quote them verbatim (an excerpt "
            "is fine; join two excerpts with …). A goal is theirs: when they asked for nothing "
            "like it, it belongs in your plan, not here.")


def _open(loop, verb: str, gid: str) -> dict:
    """The open goal `gid`, or the refusal saying why there is none — told apart by the
    refusal's own `rejected` key.
    """
    goal = goals.find(loop.ctx.goals, gid)
    if goal is None:
        return _refuse(verb, f"no goal {gid!r} (goal verb=list shows them)")
    if goal["status"] != "open":
        return _refuse(verb, f"{gid} is already {goal['status']} — a new request is a new goal "
                             "(verb=add)")
    return goal


def _add(loop, action: dict, verb: str) -> dict:
    ctx = loop.ctx
    if verb == "set" and (still := goals.open_goals(ctx.goals)):
        return _refuse(verb, f"goals are already open ({', '.join(g['id'] for g in still)}) — "
                             "set opens the list; add, change or drop them instead")
    items = _items(action)
    if len(goals.open_goals(ctx.goals)) + len(items) > goals.MAX_OPEN:
        return _refuse(verb, f"at most {goals.MAX_OPEN} goals open at once — merge them into "
                             "fewer end states; the steps toward them belong in your plan")
    events, texts = _person(loop)
    heard = []
    for _text, quote in items:
        if (at := goals.heard_at(quote, texts)) is not None:
            heard.append((quote, at))
            continue
        # A bare numbered PICK ("1.", "2", "3)") is what the person's whole reply is when he
        # answers the numbered choices the engine itself rendered, so the WORDS of the choice
        # he picked are what ground the goal — and what it is recorded as (F731). A number no
        # choice he picked carries resolves to nothing and is refused below, like any other
        # words he never wrote.
        chosen = goalpicks.quoted_choice(quote, events)
        at = goals.heard_at(chosen, texts) if chosen else None
        if at is None:
            return _refuse(verb, _not_found(quote, later=False))
        heard.append((chosen, at))
    added = []
    for (text, _asked), (quote, at) in zip(items, heard, strict=True):
        goal = goals.blank(goals.next_id(ctx.goals), text, quote, source="person", heard=at,
                           turn=ctx.turn)
        ctx.goals.append(goal)
        added.append(goal["id"])
    ctx.write_status()
    return _obs(loop, verb, f"added {', '.join(added)}.")


def _change(loop, action: dict) -> dict:
    goal = _open(loop, "change", str(action.get("id") or ""))
    if goal.get("rejected"):
        return goal
    text, quote = _items(action)[0]
    at = goals.heard_at(quote, _person(loop)[1], after=goal["heard"])
    if at is None and not _heard_later(loop, goal, quote):
        return _refuse("change", _not_found(quote, later=True, carried=goal.get("heard") == -1))
    goal.update(text=text[:goals.TEXT_MAX], quote=quote[:goals.QUOTE_MAX],
                heard=at if at is not None else goal["heard"],
                turn=loop.ctx.turn, remains="")
    loop.ctx.write_status()
    return _obs(loop, "change", f"{goal['id']} now reads as the person last put it.")


def _drop(loop, action: dict) -> dict:
    goal = _open(loop, "drop", str(action.get("id") or ""))
    if goal.get("rejected"):
        return goal
    quote = str(action.get("quote") or "").strip()
    if not _heard_later(loop, goal, quote):
        return _refuse("drop", _not_found(quote, later=True, carried=goal.get("heard") == -1))
    goal.update(status="dropped", dropped_by=quote[:goals.QUOTE_MAX], turn=loop.ctx.turn)
    loop.ctx.write_status()
    return _obs(loop, "drop", f"{goal['id']} dropped on the person's word.")


def _check(loop, action: dict) -> dict:
    gid = str(action.get("id") or "")
    goal = _open(loop, "check", gid)
    if goal.get("rejected"):
        return goal
    evidence = str(action.get("evidence") or "").strip()
    objections = verifier.refuted(loop, [{"id": gid, "text": goal["text"], "stage": ""}],
                                  f"(checking off goal {gid} mid-run) {evidence}")
    if objections and gid not in loop._challenged:
        loop._challenged.add(gid)
        loop.ctx.claims_challenged += 1
        return _obs(loop, "check",
                    f"{gid} stays OPEN — a check of your own transcript does not support it: "
                    f"{objections[0]['evidence']}\nDo the missing work and check it again, or — "
                    "if the check is wrong, which it can be, since it reads only the tail of "
                    "your transcript — check it again with the SAME evidence and point at it. "
                    "You will not be asked twice: a repeated check stands, and the disagreement "
                    "is recorded for the person.", claims_unsupported=[gid])
    goal.update(status="met", evidence=evidence[:goals.TEXT_MAX], turn=loop.ctx.turn,
                disputed=objections[0]["evidence"] if objections else "")
    loop.ctx.claims_disputed += bool(objections)
    loop.ctx.write_status()
    return _obs(loop, "check", f"{gid} met." + (" The check's objection is recorded beside "
                                                "your evidence." if objections else ""))


def _list(loop, _action: dict) -> dict:
    return _obs(loop, "list", f"{len(loop.ctx.goals)} goal(s) this run.")


def handle_goal(loop, action: dict) -> dict:
    verb = str(action.get("verb") or "")
    if loop.ctx.depth > 0:
        return _refuse(verb, "a child run has no goals of its own — your parent's brief is "
                             "your task")
    if verb in ("set", "add"):
        return _add(loop, action, verb)
    return {"change": _change, "drop": _drop, "check": _check, "list": _list}[verb](loop, action)


def format_goal(obs: dict, kind: str) -> str | None:
    """The observation wording for `goal` — the handler wrote the whole message."""
    if kind != "goal":
        return None
    return f"OBSERVATION (goal {obs.get('verb')}): {obs.get('message', '')}"
