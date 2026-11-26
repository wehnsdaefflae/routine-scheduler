"""Observation wording for the kinds that reach OUTSIDE this run — scheduling, creation, reports,
sub-calls and questions — and the ONE wording of an ask-back on any blocking decision
(`dialog_reply`), which the library, secret-gate and reminder renderers share.

Each one changes instance state or asks something of a person, so the wording must be honest
about what did NOT happen yet: a draft is not a routine, a queued proposal is not a creation, a
filed report starts no run, and a deferred question may never be answered.

That is why `queued` is checked FIRST, in one shared branch, before any kind's success wording:
otherwise a queued proposal falls through to a success line over a payload that is not there.
"""

from __future__ import annotations

from .. import recipients
from ..report_threads import TRIAGE_READER

#: The kinds that can come back QUEUED — a scheduled run's proposal for the Decisions page.
#: Both carry the same three keys (`queued`, `id`, `next`) plus a self-describing
#: `proposal` line written by the handler, so one branch renders both. Being queueable is also
#: why every depth-0 run is granted them (`loopsetup`): a run with no user files a proposal.
QUEUEABLE_KINDS = ("create_routine", "manage_lane")


def _queued_line(obs: dict, kind: str) -> str:
    """The one wording for a proposal that was filed instead of applied. It must name what was
    proposed: a run that cannot tell WHICH change is waiting cannot say so in its summary;
    an ack that names nothing reads as a change that lost its payload.
    """
    return (f"OBSERVATION ({kind} QUEUED as proposal {obs.get('id')} — NOTHING CHANGED): "
            f"{obs.get('proposal') or 'the change you asked for'}. {obs.get('next', '')}").strip()


def dialog_reply(obs: dict, what: str, resubmit: str, until: str = "") -> str:
    """The ONE wording for the console's "ask back" on a blocking decision — the body every
    kind's observation (and the curated-reminder note) carries after its own head.

    The operator replied WITHOUT deciding. Their words come verbatim, then the move: address
    them and re-submit the action, which replaces the open record (`interact.handle_ask`
    supersedes by subject). One pattern for every kind; a kind supplies only its noun (`what`),
    how it is re-submitted, and what stays untouched until the operator decides (`until`).
    `obs` carries `qid` and `user_message` (`askback.still_pending`).
    """
    tail = f" {until}" if until else ""
    return (f"the user replied WITHOUT deciding — a dialog reply, NOT the answer; the {what} "
            f"stays open ({obs.get('qid')}):\n{obs.get('user_message', '')}\nAddress their "
            f"message, then {resubmit} — the re-submission replaces the open record.{tail}")


def _choice_lists(obs: dict) -> str:
    """The lists a create_routine draft (or a refusal naming an unknown workflow) asks the model
    to put to the user — `workflow_catalog`, `settings_patterns`, `design_checks`. The handler's
    `next` points at them, so they must be IN the text — the model never sees the dict.
    """
    out = []
    if catalog := obs.get("workflow_catalog"):
        out.append("workflow_catalog:")
        out += [f"- {w['slug']}: {w['description']}"
                + (f" — when: {w['when_to_use']}" if w.get("when_to_use") else "")
                for w in catalog]
    if patterns := obs.get("settings_patterns"):
        out.append("settings_patterns:")
        for p in patterns:
            when = f" — when: {p['when']}" if p.get("when") else ""
            out.append(f"- {p['slug']}: {p['summary']}{when}")
            out += [f"    asks: {q}" for q in p.get("asks") or []]
    if checks := obs.get("design_checks"):
        out.append("design_checks:")
        out += [f"- {c}" for c in checks]
    return "\n" + "\n".join(out) if out else ""


def _runtree_line(obs: dict) -> str:
    """Wording for a RUN-TREE hook — the `report` action with `target: parent` / `child:<label>`
    (engine/runtree.py). Its own shape: `delivered` and a `hook` id, never `filed` and an `R<n>`.

    Rendered FIRST, before any branch that assumes a ledger row: falling through would report a
    DELIVERED hook as a failed write, and the sender would redo work that already landed.
    """
    if not obs.get("delivered"):
        if refused := obs.get("refused"):
            return f"OBSERVATION (hook NOT sent — {refused})"
        return ("OBSERVATION (hook to "
                f"{obs.get('target')!r} FAILED to be written (I/O error) — it was not "
                "delivered and nothing is queued. Say it in your finish summary instead.)")
    return (f"OBSERVATION (hook {obs.get('hook')} sent to {obs.get('addressee')!r} — "
            f"{obs.get('note')}.)")


def format_admin(obs: dict, kind: str) -> str | None:  # noqa: C901, PLR0911, PLR0912 — one flat renderer per module, by design: observation wording is PROMPT SURFACE (docs/prompt-anatomy.md) and every branch is a distinct string for a distinct kind. Collapsing them would scatter a kind's wording, which is exactly what this shape exists to prevent.
    """Wording for this module's kinds; None when `kind` is not one of them."""
    if kind in QUEUEABLE_KINDS and obs.get("queued"):
        return _queued_line(obs, kind)
    if kind == "schedule_run":
        target = obs.get("target")
        if obs.get("unknown_target"):
            sugg = obs.get("suggestions") or []
            valid = obs.get("valid_targets") or []
            hint = f" Did you mean: {', '.join(sugg)}?" if sugg else ""
            listing = f" Valid target slugs: {', '.join(valid)}." if valid else ""
            return (f"OBSERVATION (schedule_run: no routine {target!r} — nothing armed."
                    f"{hint}{listing})")
        if obs.get("bad_fire_at"):
            return f"OBSERVATION (schedule_run {target!r} REJECTED): {obs['bad_fire_at']}"
        if "cancelled" in obs:
            which = f"id {obs['id']}" if obs.get("id") else "all armed one-shots"
            return (f"OBSERVATION (schedule_run {target!r}: cancelled {obs['cancelled']} "
                    f"one-shot(s) — {which}).")
        return (f"OBSERVATION (schedule_run {target!r}: armed one-shot {obs['armed']} for "
                f"{obs['fire_at']} — the daemon fires it once, then consumes it).")
    if kind == "create_routine":
        slug = obs.get("slug")
        if obs.get("rejected"):
            return f"OBSERVATION (create_routine REJECTED): {obs['reason']}{_choice_lists(obs)}"
        if obs.get("already_exists"):
            return (f"OBSERVATION (create_routine: a routine {slug!r} already exists — nothing "
                    "created. Pick another slug, or edit the existing routine instead.)")
        if obs.get("error"):
            return (f"OBSERVATION (create_routine {slug!r} FAILED): {obs['error']}. Fix the "
                    "slug / workflow / instruction and try again.")
        if obs.get("draft"):
            # The preview step MUST NOT read as success (D92).
            state = "draft UPDATED — confirmation restarted" if obs.get("updated") \
                else "draft stored"
            held = f" HELD: {obs['held']}" if obs.get("held") else ""
            return (f"OBSERVATION (create_routine DRAFT {slug!r} — NOTHING CREATED YET; "
                    f"{state}. name {obs.get('name')!r}, workflow {obs.get('workflow')!r}, "
                    f"instruction {obs.get('instruction_chars')} chars, beginning: "
                    f"{obs.get('instruction_preview', '')[:200]!r}. {obs.get('next')}{held})"
                    f"{_choice_lists(obs)}")
        return (f"OBSERVATION (create_routine: created routine {slug!r} from workflow "
                f"{obs.get('workflow')!r}. The daemon's registry rescan (every "
                f"~{obs.get('rescan_s') or 30}s) picks it up and it appears on the dashboard. "
                f"Tell the user it exists, GIVE THEM THE LINK {obs.get('url')} to its page, "
                f"and say what to set next — its schedule and anything its work needs beyond "
                f"the defaults, such as filesystem roots or a bound machine.)")
    if kind == "manage_lane":
        if obs.get("rejected"):
            return f"OBSERVATION (manage_lane REJECTED): {obs['reason']}"
        verb = obs.get("verb")
        if verb == "list":
            rows = obs.get("lanes") or []
            # The listing names its MEMBERS in fire order — the lane's whole semantics, and
            # no other tool answers "which routines are in it".
            def _one(lane: dict) -> str:
                slugs = [str(m.get("slug", "")) for m in (lane.get("members") or [])]
                who = " → ".join(slugs) if slugs else "no members"
                sched = f", cron {lane['cron']!r}" if lane.get("cron") else ""
                paused = ", PAUSED" if lane.get("paused") else ""
                return f"{lane.get('name')!r} ({lane.get('id')}{sched}{paused}): {who}"

            names = "; ".join(_one(lane) for lane in rows) or "none"
            return (f"OBSERVATION (manage_lane list: default_on_failure="
                    f"{obs.get('default_on_failure')!r}; lanes — {names}).")
        if verb == "set-default":
            return (f"OBSERVATION (manage_lane: instance default_on_failure set to "
                    f"{obs.get('default_on_failure')!r}).")
        if verb == "delete":
            return f"OBSERVATION (manage_lane: deleted lane {obs.get('deleted')!r})."
        if verb == "run":
            return (f"OBSERVATION (manage_lane: armed a sequential fire of lane "
                    f"{obs.get('lane_id')!r} ({len(obs.get('members') or [])} member(s)) — "
                    "the daemon fires the members in order on its next tick).")
        lane = obs.get("lane") or {}
        sched = (f" and schedule cron={lane['cron']!r} ({lane.get('tz')})" if lane.get("cron")
                 else " and no schedule (members fire on their own crons)")
        # member records render as slugs — the model reads the fire order at a glance
        # without the record boilerplate
        members = [m["slug"] for m in lane.get("members") or []]
        paused = (" PAUSED (cron gated; an explicit run still works),"
                  if lane.get("paused") else "")
        return (f"OBSERVATION (manage_lane {verb}: lane {lane.get('name')!r} ({lane.get('id')}) "
                f"now has members {members},{paused} on_failure={lane.get('on_failure')!r}"
                f"{sched}).")
    if kind == "report":
        if obs.get("runtree"):
            return _runtree_line(obs)
        if obs.get("self_target"):
            return ("OBSERVATION (report: a routine cannot address a report to itself — drop "
                    "`target` to send it to triage, or keep the thought in a `note`.)")
        if obs.get("unknown_target"):
            return (f"OBSERVATION (report: no routine {obs.get('target')!r}. Close matches: "
                    f"{obs.get('suggestions') or 'none'}; all routines: "
                    f"{obs.get('valid_targets')}. Retry with one of those, or drop `target` "
                    "to send it to triage.)")
        if obs.get("target_unreachable"):
            # The STATE and LAST RUN DATE are the substance: `disabled` may be a pause,
            # `retired` (a finish line reached) is permanent.
            when = (f"it last ran {obs['last_run']} ({obs.get('last_run_state')})"
                    if obs.get("last_run") else f"{obs.get('last_run_state')}")
            # Where the work went: a routine is usually off because its work MOVED.
            return (f"OBSERVATION (report REFUSED — {obs.get('target')!r} is "
                    f"{obs.get('state')}: {obs.get('because')}, and {when}. "
                    f"{obs.get('reason')} "
                    f"{recipients.render(obs.get('suggestions') or [])})")
        if cap := obs.get("thread_cap"):
            return (f"OBSERVATION (report REFUSED — you already have {len(obs['open_to_target'])} "
                    f"reports open to {obs.get('target')!r}, and {cap} parallel threads to one "
                    f"owner is the limit): {', '.join(obs['open_to_target'])}, oldest first. A "
                    f"fourth thread does not raise the priority of the first three — it makes "
                    f"the owner re-triage the same ground. Re-emit this report with "
                    f"`supersedes` naming the ones it takes over (start with "
                    f"{obs.get('oldest')!r}), or with `answers` if it replies to one. Nothing "
                    "was filed.)")
        if obs.get("unusable"):   # a clause PER ID, worded by `supersedable`
            return ("OBSERVATION (report REFUSED — `supersedes` names rows that cannot be "
                    f"taken over: {'; '.join(f'{r} {c}' for r, c in obs['unusable'])}. "
                    "Nothing was filed; re-emit without them.)")
        if obs.get("undeliverable_disposal"):
            # D182: two refusals with different moves, never merged — a row delivered
            # elsewhere is its owner's to answer; an unrouted row must reach an owner first.
            parts = []
            if elsewhere := obs.get("elsewhere") or []:
                named = ", ".join(f"{rid} → {owner or 'unknown'}"
                                  for rid, owner in elsewhere)
                parts.append("delivered to another routine, so only that routine disposes of "
                             f"it: {named}. Reply to its owner instead, or re-emit with "
                             "`supersedes` naming it to take the thread over")
            if unrouted := obs.get("unrouted") or []:
                parts.append("NOT ROUTED YET — unaddressed, so it sits in triage and only "
                             f"triage ({TRIAGE_READER}) disposes of it: {', '.join(unrouted)}. "
                             "Route it first (a report with `supersedes` naming it and a "
                             "`target`), then the owner settles it")
            return ("OBSERVATION (report REFUSED — `answers`/`settles` dispose of a row only "
                    "for the routine the row was delivered TO. " + "; and ".join(parts)
                    + ". Nothing was filed.)")
        if obs.get("filed"):
            where = (f"delivered to {obs['target']!r} — it reads this on its next scheduled "
                     "run (no run was started)" if obs.get("target")
                     else "unaddressed, so it goes to triage")
            took = (f" It TAKES OVER {', '.join(obs['supersedes'])}: they leave triage now and "
                    f"settle when this one does, so do not route them again."
                    if obs.get("supersedes") else "")
            # A cut detail is named with the size of the loss; only the DELIVERY lost it —
            # the ledger row keeps it whole (D177) — so nothing needs re-filing.
            cut = (f" Its detail was TRUNCATED IN DELIVERY: {obs['detail_truncated']} "
                   "characters past the cap did not reach the reader's prompt. Nothing needs "
                   "re-filing: the ledger row keeps it whole and the delivered copy points the "
                   "reader at that row for the rest."
                   if obs.get("detail_truncated") else "")
            # `options` also filed a decision for the operator: say the card exists and
            # that his answer comes back to this routine's inbox.
            decided = (" Its `options` also filed it as an OPEN DECISION for the user "
                       f"({obs['decision_qid']}): it is on his Decisions page now, counted "
                       "on your routine card, and his answer arrives in YOUR inbox for your "
                       "next run — so do not also ask it as an ask_user."
                       if obs.get("decision_qid") else "")
            # The `closes` was dropped because it completed nothing. Said HERE, in the turn
            # that filed it: the filing stands, so the only thing that makes this a correction
            # rather than a silent no-op is that the run reads what became of the field.
            dropped = (" Your `closes` was DROPPED — it completes a disposal, and this report "
                       "names none: the notice is filed as owing NO reply back, and it settled "
                       "no row. Name the row in `answers` or `settles` if you meant to dispose "
                       "of one." if obs.get("closes_dropped") else "")
            # The prose claims a disposal no FIELD performs: the row stays open. Not a
            # refusal — the prose may be a citation this check read as a claim.
            said = (" Your text says it disposes of "
                    f"{', '.join(obs['claimed_not_disposed'])}, but `answers`, `settles` and "
                    "`supersedes` name none of them — so " +
                    ("that row stays" if len(obs["claimed_not_disposed"]) == 1
                     else "those rows stay") +
                    " open, counted in every backlog, and its recipient cannot close it for "
                    "you (a row is disposed of only for the routine it was delivered TO). "
                    "Re-file with the id in the right field if you meant it."
                    if obs.get("claimed_not_disposed") else "")
            return (f"OBSERVATION (report filed as {obs.get('id')}: {obs.get('title')!r} — "
                    f"{where}.{took}{cut}{dropped}{decided}{said} Refer to it by that id if "
                    "you mention it again. Continue your own task.)")
        return ("OBSERVATION (report: could NOT write the reports log (I/O error) — the "
                "report was not filed. Continue your own task; put it in your finish summary "
                "instead.)")
    if kind == "ask_user":
        # D220: a filed proof says what will check it (engine/askproof.note)
        return _ask_user(obs) + ("" if obs.get("error") else obs.get("proof_note") or "")
    return None


def _ask_user(obs: dict) -> str:  # noqa: PLR0911 — one distinct string per ask outcome (prompt surface)
    """The ask_user observation, one wording per way an ask can end."""
    if err := obs.get("error"):
        # REFUSED before any record was filed: nothing is pending, nobody was asked.
        return (f"OBSERVATION (ask_user REFUSED — no question was filed): {err}")
    if obs.get("batch"):
        # D212: ONE action, N decision records; "blocking" is a recorded class, not a wait.
        return (f"OBSERVATION (ask_user): {obs['count']} questions filed as one batch "
                f"({obs['batch']}), {obs.get('blocking') or 0} marked blocking. They are "
                "on the user's Decisions page together; answers reach a future run. "
                f"Continue.{obs.get('refused_note') or ''}")
    if obs.get("decision"):
        # an access request settled by one of the typed decisions — the result
        # line already teaches scope (this run vs forever) and the way forward
        return f"OBSERVATION (ask_user — access request decided): {obs['result']}"
    if obs.get("answered"):
        via = f" (via {obs['source']})" if obs.get("source", "web") != "web" else ""
        return f"OBSERVATION (ask_user): the user answered{via}:\n{obs['answer']}"
    if obs.get("deferred_by_user"):
        tail = (f"Proceed on your stated default: {obs['default']}"
                if obs.get("default") else "Continue and plan around it")
        return (f"OBSERVATION (ask_user): the user DEFERRED this question to a future run — "
                f"it stays open as deferred ({obs['qid']}). {tail}; their answer, if any, "
                "reaches a future run.")
    if obs.get("timed_out"):
        tail = (f"Proceed on your stated default: {obs['default']}"
                if obs.get("default") else "Continue and plan around it")
        return (f"OBSERVATION (ask_user): no answer within {obs.get('timeout_min')}m — "
                f"question stays open as deferred ({obs['qid']}). {tail}; a late answer "
                "reaches a future run.")
    if obs.get("dialog") and obs.get("request"):
        # An ask-back on an ACCESS REQUEST: the same request again re-submits it (the
        # subject is its entity ids), and its question prose is where the answer goes.
        ids = ", ".join(obs["request"])
        return ("OBSERVATION (ask_user — access request): " + dialog_reply(
            obs, "request", f"ask again with ask_user and the same request ({ids}), its "
            "question answering them", "Nothing is granted until they decide."))
    if obs.get("dialog"):
        # The console's "ask back": the user replied to a BLOCKING question without
        # deciding it, and their words must reach the model.
        return "OBSERVATION (ask_user): " + dialog_reply(
            obs, "question", "ask again with ask_user (the original question, or a sharper "
            "version)")
    return (f"OBSERVATION (ask_user): question filed as deferred ({obs['qid']}). The user will "
            "see it in the UI; the answer, if any, reaches a future run. Continue.")
