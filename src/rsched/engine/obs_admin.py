"""Observation wording for the kinds that reach OUTSIDE this run — scheduling, creation, reports,
sub-calls and questions — and the ONE wording of an ask-back on any blocking decision
(`dialog_reply`), which the library, secret-gate and reminder renderers share.

Split out of `observations.py` (F393). Each one either changes instance state or asks something
of a person, so the wording's job is to be honest about what did NOT happen yet: a draft is not
a routine, a queued proposal is not a creation, a filed report starts no run, and a deferred
question may never be answered.

That honesty is the whole reason `queued` is checked FIRST, in one shared branch, before any
kind's success wording. F328 gave `create_routine` and `manage_lane` a proposal path for a
scheduled run, but taught only the HANDLERS about it: the queued observation then fell through
to each kind's success line and read as a completed action over a payload that was not there —
"created routine 'x' from workflow None" (the F378 false-success class, again), "armed a
sequential fire of lane None (0 member(s))" (R1200) and "lane None (None) now has members []"
(R1183), the last of which reads as a lane that was just emptied. One shared branch is also
why the two cannot drift apart again.
"""

from __future__ import annotations

#: The kinds that can come back QUEUED — a scheduled run's proposal for the Decisions page
#: (F328). Both carry the same three keys (`queued`, `id`, `next`) plus a self-describing
#: `proposal` line written by the handler, so one branch renders both.
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

    The operator replied WITHOUT deciding: they need some back-and-forth first. Their words are
    the whole point of the reply, so they come verbatim; then what to do with them — address
    them, and re-submit the action that asked, which files the decision again and replaces the
    open record (`interact.handle_ask` supersedes by subject). One pattern for every kind, so a
    question, an approval and a request cannot drift into dialects of the same promise; a kind
    supplies only its noun (`what`), how it is re-submitted, and what stays untouched until the
    operator decides (`until`). `obs` carries `qid` and `user_message` (`askback.still_pending`).
    """
    tail = f" {until}" if until else ""
    return (f"the user replied WITHOUT deciding — a dialog reply, NOT the answer; the {what} "
            f"stays open ({obs.get('qid')}):\n{obs.get('user_message', '')}\nAddress their "
            f"message, then {resubmit} — the re-submission replaces the open record.{tail}")


def _choice_lists(obs: dict) -> str:
    """The lists a create_routine draft (or a refusal naming an unknown workflow) asks the model
    to put to the user — `workflow_catalog`, `settings_patterns`, `design_checks`. The handler's
    `next` points at them ("its options are the entries of workflow_catalog"), so they must be IN
    the text: riding only the observation dict, they never reached the model, which was told to
    offer options it could not see and answered the workflow question from memory (F383's
    failure, back through the renderer).
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
            # D92's preview step MUST NOT read as success: before 0.222.0 this fell through
            # to the created-copy below, so the agent announced a routine that did not exist
            # (R476/R477/R478, conversation c-20260822-174836).
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
            # F424/R1142: the listing names its MEMBERS, in fire order. A count answered
            # "how big" and nothing answered "which routines are in it" — and no other tool
            # does, so a run reasoning about a lane had to guess. Slugs are short; the fire
            # order is the lane's whole semantics.
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
        if obs.get("self_target"):
            return ("OBSERVATION (report: a routine cannot address a report to itself — drop "
                    "`target` to send it to triage, or keep the thought in a `note`.)")
        if obs.get("unknown_target"):
            return (f"OBSERVATION (report: no routine {obs.get('target')!r}. Close matches: "
                    f"{obs.get('suggestions') or 'none'}; all routines: "
                    f"{obs.get('valid_targets')}. Retry with one of those, or drop `target` "
                    "to send it to triage.)")
        if obs.get("target_unreachable"):
            # F614: 24 reports sat open against routines that start no run, the oldest six
            # weeks. The STATE and the LAST RUN DATE are the refusal's substance — `disabled`
            # is the operator's switch and may be a pause (one such routine had run two days
            # before the count), `retired` is a finish line reached and is permanent.
            when = (f"it last ran {obs['last_run']} ({obs.get('last_run_state')})"
                    if obs.get("last_run") else f"{obs.get('last_run_state')}")
            return (f"OBSERVATION (report REFUSED — {obs.get('target')!r} is "
                    f"{obs.get('state')}: {obs.get('because')}, and {when}. "
                    f"{obs.get('reason')})")
        if cap := obs.get("thread_cap"):
            return (f"OBSERVATION (report REFUSED — you already have {len(obs['open_to_target'])} "
                    f"reports open to {obs.get('target')!r}, and {cap} parallel threads to one "
                    f"owner is the limit): {', '.join(obs['open_to_target'])}, oldest first. A "
                    f"fourth thread does not raise the priority of the first three — it makes "
                    f"the owner re-triage the same ground. Re-emit this report with "
                    f"`supersedes` naming the ones it takes over (start with "
                    f"{obs.get('oldest')!r}), or with `answers` if it replies to one. Nothing "
                    "was filed.)")
        if obs.get("unusable"):
            return (f"OBSERVATION (report REFUSED — `supersedes` names rows that cannot be "
                    f"taken over: {', '.join(obs['unusable'])}. Each is {obs.get('reason')}. "
                    "Nothing was filed; re-emit without them.)")
        if obs.get("filed"):
            where = (f"delivered to {obs['target']!r} — it reads this on its next scheduled "
                     "run (no run was started)" if obs.get("target")
                     else "unaddressed, so it goes to triage")
            took = (f" It TAKES OVER {', '.join(obs['supersedes'])}: they leave triage now and "
                    f"settle when this one does, so do not route them again."
                    if obs.get("supersedes") else "")
            return (f"OBSERVATION (report filed as {obs.get('id')}: {obs.get('title')!r} — "
                    f"{where}.{took} Refer to it by that id if you mention it again. Continue "
                    "your own task.)")
        return ("OBSERVATION (report: could NOT write the reports log (I/O error) — the "
                "report was not filed. Continue your own task; put it in your finish summary "
                "instead.)")
    if kind == "llm":
        if err := obs.get("error"):
            return f"OBSERVATION (llm subcall FAILED): {err}"
        return f"OBSERVATION (llm reply):\n{obs['reply']}"
    if kind == "ask_user":
        if err := obs.get("error"):
            # The ask was REFUSED before any record was filed (e.g. a config_patch naming a
            # routine that does not exist — D123/F458). Nothing is pending and nobody was
            # asked, so say that plainly instead of falling through to the "filed as
            # deferred" line, which assumed every ask_user observation carries a qid.
            return (f"OBSERVATION (ask_user REFUSED — no question was filed): {err}")
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
            # deciding it. Their words are the whole point — this used to fall through to the
            # "filed as deferred … Continue." line below, so the model never saw them and
            # carried on as if nobody had answered.
            return "OBSERVATION (ask_user): " + dialog_reply(
                obs, "question", "ask again with ask_user (the original question, or a sharper "
                "version)")
        return (f"OBSERVATION (ask_user): question filed as deferred ({obs['qid']}). The user will "
                "see it in the UI; the answer, if any, reaches a future run. Continue.")
    return None
