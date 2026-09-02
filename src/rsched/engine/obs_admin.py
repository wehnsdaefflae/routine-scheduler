"""Observation wording for the kinds that reach OUTSIDE this run — scheduling, creation, reports,
sub-calls and questions.

Split out of `observations.py` (F393). Each one either changes instance state or asks something
of a person, so the wording's job is to be honest about what did NOT happen yet: a draft is not
a routine, a queued proposal is not a creation, a filed report starts no run, and a deferred
question may never be answered.

That honesty is the whole reason `queued` is checked FIRST, in one shared branch, before any
kind's success wording. F328 gave `create_routine` and `manage_group` a proposal path for a
scheduled run, but taught only the HANDLERS about it: the queued observation then fell through
to each kind's success line and read as a completed action over a payload that was not there —
"created routine 'x' from workflow None" (the F378 false-success class, again), "armed a
sequential fire of group None (0 member(s))" (R1200) and "group None (None) now has members []"
(R1183), the last of which reads as a group that was just emptied. One shared branch is also
why the two cannot drift apart again.
"""

from __future__ import annotations

#: The kinds that can come back QUEUED — a scheduled run's proposal for the Decisions page
#: (F328). Both carry the same three keys (`queued`, `id`, `next`) plus a self-describing
#: `proposal` line written by the handler, so one branch renders both.
QUEUEABLE_KINDS = ("create_routine", "manage_group")


def _queued_line(obs: dict, kind: str) -> str:
    """The one wording for a proposal that was filed instead of applied. It must name what was
    proposed: a run that cannot tell WHICH change is waiting cannot say so in its summary, and
    an ack that names nothing reads as a change that lost its payload.
    """
    return (f"OBSERVATION ({kind} QUEUED as proposal {obs.get('id')} — NOTHING CHANGED): "
            f"{obs.get('proposal') or 'the change you asked for'}. {obs.get('next', '')}").strip()


def format_admin(obs: dict, kind: str) -> str | None:  # noqa: C901, PLR0911, PLR0912 — one flat renderer per domain, by design: observation wording is PROMPT SURFACE (docs/prompt-anatomy.md) and every branch is a distinct string for a distinct kind. Collapsing them would scatter a kind's wording, which is exactly what this shape exists to prevent.
    """Wording for this domain's kinds; None when `kind` is not one of them."""
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
            return f"OBSERVATION (create_routine REJECTED): {obs['reason']}"
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
                    f"{obs.get('instruction_preview', '')[:200]!r}. {obs.get('next')}{held})")
        tpl = obs.get("template")
        adopted = (f" It adopted the {tpl!r} settings template — its conduct docs, "
                   f"capabilities and general rules come from there, and its own routine.yaml "
                   f"records only what differs." if tpl else "")
        return (f"OBSERVATION (create_routine: created routine {slug!r} from workflow "
                f"{obs.get('workflow')!r}.{adopted} The daemon's registry rescan (every "
                f"~{obs.get('rescan_s') or 30}s) picks it up and it appears on the dashboard. "
                f"Tell the user it exists, GIVE THEM THE LINK {obs.get('url')} to its page, "
                f"and say what to set next — its schedule, and anything the template does not "
                f"cover such as filesystem roots or a bound machine.)")
    if kind == "manage_group":
        if obs.get("rejected"):
            return f"OBSERVATION (manage_group REJECTED): {obs['reason']}"
        verb = obs.get("verb")
        if verb == "list":
            gs = obs.get("groups") or []
            # F424/R1142: the listing names its MEMBERS, in fire order. A count answered
            # "how big" and nothing answered "which routines are in it" — and no other tool
            # does, so a run reasoning about a group had to guess. Slugs are short; the fire
            # order is the group's whole semantics.
            def _one(g: dict) -> str:
                slugs = [str(m.get("slug", "")) for m in (g.get("members") or [])]
                who = " → ".join(slugs) if slugs else "no members"
                sched = f", cron {g['cron']!r}" if g.get("cron") else ""
                paused = ", PAUSED" if g.get("paused") else ""
                return f"{g.get('name')!r} ({g.get('id')}{sched}{paused}): {who}"

            names = "; ".join(_one(g) for g in gs) or "none"
            return (f"OBSERVATION (manage_group list: default_on_failure="
                    f"{obs.get('default_on_failure')!r}; groups — {names}).")
        if verb == "set-default":
            return (f"OBSERVATION (manage_group: instance default_on_failure set to "
                    f"{obs.get('default_on_failure')!r}).")
        if verb == "delete":
            return f"OBSERVATION (manage_group: deleted group {obs.get('deleted')!r})."
        if verb == "run":
            return (f"OBSERVATION (manage_group: armed a sequential fire of group "
                    f"{obs.get('group_id')!r} ({len(obs.get('members') or [])} member(s)) — "
                    "the daemon fires the members in order on its next tick).")
        g = obs.get("group") or {}
        sched = (f" and schedule cron={g['cron']!r} ({g.get('tz')})" if g.get("cron")
                 else " and no schedule (members fire on their own crons)")
        # member records render as slugs — the model reads the fire order at a glance
        # without the record boilerplate
        members = [m["slug"] for m in g.get("members") or []]
        paused = " PAUSED (cron gated; an explicit run still works)," if g.get("paused") else ""
        return (f"OBSERVATION (manage_group {verb}: group {g.get('name')!r} ({g.get('id')}) now "
                f"has members {members},{paused} on_failure={g.get('on_failure')!r}{sched}).")
    if kind == "report":
        if obs.get("self_target"):
            return ("OBSERVATION (report: a routine cannot address a report to itself — drop "
                    "`target` to send it to triage, or keep the thought in a `note`.)")
        if obs.get("unknown_target"):
            return (f"OBSERVATION (report: no routine {obs.get('target')!r}. Close matches: "
                    f"{obs.get('suggestions') or 'none'}; all routines: "
                    f"{obs.get('valid_targets')}. Retry with one of those, or drop `target` "
                    "to send it to triage.)")
        if obs.get("filed"):
            where = (f"delivered to {obs['target']!r} — it reads this on its next scheduled "
                     "run (no run was started)" if obs.get("target")
                     else "unaddressed, so it goes to triage")
            return (f"OBSERVATION (report filed as {obs.get('id')}: {obs.get('title')!r} — "
                    f"{where}. Refer to it by that id if you mention it again. Continue your "
                    "own task.)")
        return ("OBSERVATION (report: could NOT write the reports log (I/O error) — the "
                "report was not filed. Continue your own task; put it in your finish summary "
                "instead.)")
    if kind == "llm":
        if err := obs.get("error"):
            return f"OBSERVATION (llm subcall FAILED): {err}"
        return f"OBSERVATION (llm reply):\n{obs['reply']}"
    if kind == "ask_user":
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
        return (f"OBSERVATION (ask_user): question filed as deferred ({obs['qid']}). The user will "
                "see it in the UI; the answer, if any, reaches a future run. Continue.")
    return None
