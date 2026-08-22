"""Observation rendering — the dispatch result of one action, turned into the next user
message (`format_observation`), plus the head+tail truncation every large output rides
through. The transcript renderer's counterpart: observation wording is prompt surface
(docs/prompt-anatomy.md) and lives here in ONE place per kind.
"""

from __future__ import annotations

import json

from . import outputs

OBS_CAP_CHARS = 8_000


def truncate(text: str, cap: int = OBS_CAP_CHARS, keep: str = "head+tail") -> tuple[str, bool]:
    """Cap a large output for its observation. `keep`:
    - "head+tail" (default): keep both ends, elide the MIDDLE — for failure stderr, where
      the traceback's END is the repair material and must survive (test_utils
      keeps_trace_tail pins it).
    - "head": keep the HEAD only, drop the TAIL — for ordered STDOUT that is spilled in
      full to `.util_outputs/`, so a reader continues IN SEQUENCE from the spill file at
      the char the preview stopped (operator AUDIT note R45: mid-truncation breaks
      sequential paging). The marker names that resume offset.
    """
    if len(text) <= cap:
        return text, False
    if keep == "head":
        marker = (f"\n[... output truncated: showing first {cap} of {len(text)} chars — "
                  f"read the spill file from char {cap} for the rest ...]\n")
        return (text[:cap] + marker), True
    head = int(cap * 0.6)
    tail = cap - head
    marker = f"\n[... output truncated: showing {cap} of {len(text)} chars (head+tail) ...]\n"
    return (text[:head] + marker + text[-tail:]), True


# One flat renderer on purpose: observation wording is prompt surface (docs/prompt-anatomy.md)
# and lives in ONE place per kind — a dispatch table would only scatter the strings.
def format_observation(obs: dict) -> str:  # noqa: C901, PLR0911, PLR0912, PLR0915
    kind = obs.get("kind")
    if kind in ("util", "script"):
        if kind == "util" and obs.get("name") == "search":
            return (f"OBSERVATION (util search {obs.get('query')!r} — closest utils "
                    "by keyword; the full catalog is always in CAPABILITIES):\n"
                    + obs["listing"])
        if obs.get("listing") is not None:
            return "OBSERVATION (util list — available global utils):\n" + obs["listing"]
        if obs.get("source") is not None:
            out = (f"OBSERVATION (util show — source of {obs['target']!r}; revise it with "
                   "write_util: 'content' for a full rewrite, or 'anchor'/'replacement' "
                   "to patch it in place):\n" + obs["source"])
            if obs.get("hint"):
                out += "\n\n[hint] " + obs["hint"]
            return out
        if obs.get("missing"):
            names = ", ".join(obs.get("available") or []) or "(none yet)"
            if kind == "script":
                return (f"OBSERVATION (script {obs['name']!r} does not exist). Available: "
                        f"{names}. Author it first — write_file scripts/{obs['name']}.py, "
                        "a PEP 723 script whose docstring header carries "
                        "'<name> — <summary>', 'net:', 'secrets:' — then call it again. "
                        "Script names are lowercase letters/digits with '-' or '_'.")
            miss = (f"OBSERVATION (util {(obs.get('target') or obs['name'])!r} does not exist). "
                    f"Available: {names}. Pick one of those (run `util name=list` for their "
                    "usage), or write it with write_util, then call it.")
            if obs.get("script_match"):
                # R367: the file exists as a routine-local script — the util action will
                # never run it; teach the one action that does, and the grant to request
                # when that kind is absent from this run's schema.
                miss += (f" NOTE: {obs['name']!r} exists as a ROUTINE-LOCAL script "
                         f"(scripts/) — run it with the script action: "
                         f'{{"kind": "script", "name": "{obs["name"]}"}}. If "script" is '
                         "not among your action kinds it is gated behind the scripts "
                         'permission — request it via ask_user with request: '
                         '"action:script".')
            return miss
        if obs.get("declined_secrets") or obs.get("pending_secrets"):
            # D39 secret-exposure gate: the util was NOT run — say why and what to do next.
            # A DECLINE never enumerates the names it refused (R17): the refusal must not
            # read as a consolation listing of exactly what the user just protected — the
            # model gets a count; the transcript dict keeps the names for the user's own
            # surfaces. A PENDING request still names them: it is the run's open ask, not
            # a refusal, and the names are the run's working knowledge (the util's own
            # `secrets:` declarations).
            if declined := obs.get("declined_secrets"):
                head = (f"secret exposure declined for {len(declined)} "
                        f"secret{'s' if len(declined) != 1 else ''} it declares")
            else:
                head = f"secret exposure pending for {', '.join(obs['pending_secrets'])}"
            text = f"OBSERVATION ({kind} {obs['name']} NOT run — {head}): {obs['reason']}"
            if obs.get("answer"):
                text += f"\nThe user's verbatim reply: {obs['answer']}"
            return text
        head = f"OBSERVATION ({kind} {obs['name']}, exit {obs['exit']})"
        body = obs.get("stdout") or "(no stdout)"
        if obs.get("stderr"):
            body += f"\n[stderr]\n{obs['stderr']}"
        if full := obs.get("full_output"):
            # The pointer rides the observation that lost the middle — the moment of need,
            # so the store needs no index and costs nothing on an untruncated call.
            body += "\n[full output] " + outputs.pointer_line(full)
        if obs.get("usage"):
            body += f"\n[usage] {obs['usage']}"
        if wo := obs.get("withheld_optional"):
            # F290: the call RAN, but optional secrets it declares were not injected.
            # Undecided names are requestable; denied ones stay a count (R17).
            bits = []
            if wo.get("undecided"):
                bits.append(f"{', '.join(wo['undecided'])} (not yet granted — if this call "
                            "actually needed it, request exposure via ask_user with "
                            f"request 'secret:{wo['undecided'][0]}')")
            if wo.get("denied"):
                bits.append(f"{wo['denied']} declined by the user")
            body += ("\n[note] optional secret(s) withheld from this call: "
                     + "; ".join(bits))
        if obs.get("hint"):
            body += f"\n[hint] {obs['hint']}"
        return f"{head}:\n{body}"
    if kind == "write_util":
        if obs.get("pending_approval"):
            return (f"OBSERVATION (write_util {obs['name']!r}): approval requested from the user "
                    f"({obs['qid']}). It is NOT active yet; continue with other work or wait.")
        if obs.get("declined"):
            return (f"OBSERVATION (write_util {obs['name']!r} DECLINED by the user). "
                    "Do not retry it.")
        if obs.get("edit_failed"):
            return (f"OBSERVATION (write_util {obs['name']!r} edit mode: NOT applied — "
                    f"{obs.get('reason', '')})")
        if obs.get("header_ok") is False:
            # A doc-standard rejection is NOT a selftest failure (R93: reporting it as one
            # sent authors debugging their test logic instead of adding a header line) —
            # name the violated header contract and each concrete fix.
            probs = "\n".join(f"- {p}" for p in (obs.get("problems") or []))
            return (f"OBSERVATION (write_util {obs['name']!r}: docstring HEADER violations — "
                    f"not saved, the selftest was not run):\n{probs}\n"
                    "Fix the docstring header lines (not the test logic) and write_util again.")
        if not obs.get("selftest_ok"):
            return (f"OBSERVATION (write_util {obs['name']!r}: selftest FAILED — not committed):\n"
                    f"{obs.get('output', '')}\nFix the script and write_util again.")
        return (f"OBSERVATION (write_util {obs['name']!r}: selftest passed, "
                f"{'created' if obs.get('created') else 'revised'} and committed). "
                "You can now run it with the util action.")
    if kind == "remove_util":
        if obs.get("declined"):
            reason = obs.get("reason")
            return (f"OBSERVATION (remove_util {obs['name']!r} DECLINED"
                    + (f": {reason}" if reason else " by the user") + "). Do not retry it.")
        if obs.get("pending_approval"):
            return (f"OBSERVATION (remove_util {obs['name']!r}): approval requested from the "
                    f"user ({obs['qid']}). It is NOT removed yet; continue with other work.")
        if obs.get("missing"):
            return (f"OBSERVATION (remove_util {obs['name']!r}): no such util — nothing to "
                    "remove (see `util name=list`).")
        if obs.get("callers"):
            return (f"OBSERVATION (remove_util {obs['name']!r} REFUSED): still called by "
                    f"{', '.join(obs['callers'])}. Remove or update those callers first.")
        return (f"OBSERVATION (remove_util {obs['name']!r}: removed from the library and "
                "committed — recoverable from git history).")
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
        return (f"OBSERVATION (create_routine: created routine {slug!r} from workflow "
                f"{obs.get('workflow')!r} — the daemon's registry rescan (every "
                f"~{obs.get('rescan_s') or 30}s) picks it up and it appears on the dashboard. "
                "Tell the user it exists and what to set next, e.g. its schedule.)")
    if kind == "manage_group":
        if obs.get("rejected"):
            return f"OBSERVATION (manage_group REJECTED): {obs['reason']}"
        verb = obs.get("verb")
        if verb == "list":
            gs = obs.get("groups") or []
            names = ", ".join(f"{g.get('name')!r} ({g.get('id')}, {len(g.get('members') or [])} "
                              f"member(s))" for g in gs) or "none"
            return (f"OBSERVATION (manage_group list: default_on_failure="
                    f"{obs.get('default_on_failure')!r}; groups: {names}).")
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
    if kind == "read_file":
        if obs.get("files") is not None:  # batched multi-path read
            parts = []
            for f in obs["files"]:
                if f.get("error"):
                    parts.append(f"--- {f['path']} FAILED: {f['error']}")
                else:
                    parts.append(f"--- {f['path']} (lines {f['start_line']}-{f['end_line']} "
                                 f"of {f['total_lines']}) ---\n{f['content']}")
            return f"OBSERVATION (read_file, {len(obs['files'])} files):\n" + "\n\n".join(parts)
        if err := obs.get("error"):
            return f"OBSERVATION (read_file {obs.get('path')} FAILED): {err}"
        return (f"OBSERVATION (read_file {obs['path']}, lines "
                f"{obs['start_line']}-{obs['end_line']} of {obs['total_lines']}):\n"
                f"{obs['content']}")
    if kind == "view_image":
        parts = []
        for f in obs.get("files", []):
            if f.get("error"):
                parts.append(f"--- {f['path']} FAILED: {f['error']}")
            elif f.get("native"):
                parts.append(f"--- {f['path']} ({f['media_type']}) — shown to you below; "
                             "look at it now.")
            elif f.get("via") == "vision-util":
                parts.append(f"--- {f['path']} (described by the vision util — this run's model "
                             f"can't view it directly):\n{f.get('text', '')}")
            else:
                parts.append(f"--- {f['path']}: (no result)")
        head = ("OBSERVATION (view_image — image(s) attached below for you to see):"
                if obs.get("media") else "OBSERVATION (view_image):")
        return head + "\n" + "\n\n".join(parts)
    if kind == "write_file":
        if err := obs.get("error"):
            return f"OBSERVATION (write_file {obs.get('path')} FAILED): {err}"
        base = f"OBSERVATION (write_file): wrote {obs['bytes']} bytes to {obs['path']}"
        if obs.get("append"):
            size = obs.get("size")
            # show the resulting total so a silent overwrite (size == bytes) is visible
            return base + (f" (appended; file now {size} bytes)" if size is not None
                           else " (appended)")
        return base
    if kind == "edit_file":
        if err := obs.get("error"):
            return f"OBSERVATION (edit_file {obs.get('path')} FAILED): {err}"
        return (f"OBSERVATION (edit_file): replaced {obs['replacements']} occurrence(s) in "
                f"{obs['path']} (now {obs['bytes']} bytes)")
    if kind == "memory_read":
        if obs.get("missing"):
            topics = ", ".join(obs.get("topics") or []) or "(none yet)"
            return (f"OBSERVATION (memory_read): no note named {obs['name']!r}. "
                    f"Existing topics: {topics}.")
        return (f"OBSERVATION (memory_read {obs['name']}.md, {obs['lines']} lines):\n"
                f"{obs['content']}")
    if kind == "read_rule":
        if obs["name"] == "list":
            rows = "\n".join(f"- {r['slug']}{' (binds you)' if r['held'] else ''}: "
                             f"{r['summary']}" for r in obs["rules"]) or "(library is empty)"
            return ("OBSERVATION (read_rule list) — general rules in the shared library. One you "
                    "do not hold applies to THIS run only; which rules bind you is the user's "
                    f"call:\n{rows}")
        if obs.get("missing"):
            avail = ", ".join(obs.get("available") or []) or "(none)"
            return (f"OBSERVATION (read_rule): no rule named {obs['name']!r}. "
                    f"Available: {avail}.")
        binds = (" — this rule BINDS you" if obs.get("held")
                 else " — you do not hold this rule; it applies for the rest of this run only")
        return (f"OBSERVATION (read_rule {obs['name']}, {obs['lines']} lines{binds}). "
                "It states a principle: apply it to the case in front of you.\n"
                f"{obs['content']}")
    if kind == "write_rule":
        name = obs["name"]
        if obs.get("written"):
            who = ", ".join(obs.get("holders") or []) or "no routine yet"
            verb = "authored" if obs.get("created") else "revised"
            return (f"OBSERVATION (write_rule {name}): {verb} and committed to the shared "
                    f"library. It binds: {who} — each picks the new text up at its next run.")
        if obs.get("lint_ok") is False:
            return (f"OBSERVATION (write_rule {name}) — REJECTED, the rule is unchanged:\n- "
                    + "\n- ".join(obs.get("problems") or []))
        if obs.get("pending_approval"):
            return (f"OBSERVATION (write_rule {name}): waiting on the user's approval "
                    f"({obs.get('qid')}). The rule is unchanged until they answer.")
        if obs.get("declined"):
            answer = obs.get("answer")
            return (f"OBSERVATION (write_rule {name}): NOT applied — "
                    + (f"the user answered {answer!r}." if answer
                       else str(obs.get("reason") or "declined.")))
        return (f"OBSERVATION (write_rule {name}): not applied — "
                f"{obs.get('reason') or 'the edit could not be resolved'}")
    if kind == "memory_write":
        if obs.get("deleted"):
            fate = ("deleted and INDEX updated" if obs.get("existed")
                    else "did not exist — nothing to delete")
            return f"OBSERVATION (memory_write): note {obs['name']}.md {fate}."
        return (f"OBSERVATION (memory_write): note {obs['name']}.md "
                f"{'created' if obs.get('created') else 'revised'} ({obs['lines']} lines); "
                "INDEX.md updated from 'about'.")
    if kind == "llm":
        if err := obs.get("error"):
            return f"OBSERVATION (llm subcall FAILED): {err}"
        return f"OBSERVATION (llm reply):\n{obs['reply']}"
    if kind == "spawn":
        if obs.get("rejected"):
            return f"OBSERVATION (spawn REJECTED): {obs['reason']}"
        note = f" [{obs['note']}]" if obs.get("note") else ""
        return (f"OBSERVATION (spawn): sub-workflow {obs['n']} {obs.get('label')!r} started "
                f"(workflow {obs.get('workflow')}, now {obs.get('running')} running).{note} "
                "It works in parallel — you will be notified when it finishes; keep going.")
    if kind == "subtask":
        if obs.get("rejected"):
            return f"OBSERVATION (subtask REJECTED): {obs['reason']}"
        note = f" [{obs['note']}]" if obs.get("note") else ""
        return (f"OBSERVATION (subtask): sequential child {obs['n']} {obs.get('label')!r} started "
                f"(workflow {obs.get('workflow')}){note} — it runs in the BACKGROUND. To keep "
                f"sequential order, `wait` for it (n={obs['n']}) before starting the next subtask "
                "and fold its result into that brief; the wait yields if the user writes, and you "
                "are notified when it finishes. Or do other work meanwhile.")
    if kind == "detach":
        if obs.get("rejected"):
            return f"OBSERVATION (detach REJECTED): {obs['reason']}"
        return (f"OBSERVATION (detach): background task {obs.get('label')!r} started "
                f"(id {obs.get('taskid')}, workflow {obs.get('workflow')}). It runs as its OWN "
                "process, independent of this reply — you will be notified HERE when it finishes "
                "and can then relay its result. Do NOT wait: finish this reply now (tell the user "
                "you started it and will report back).")
    if kind == "subruns":
        if not obs.get("rows"):
            return "OBSERVATION (subruns): no sub-workflows spawned this run."
        lines = [f"- #{r['n']} {r['label']!r} [{r['workflow']}] {r['state']} · "
                 f"{r['turns']} turns · {r['elapsed_s']}s"
                 + (f" · {r['summary_head']}" if r["summary_head"] else "")
                 for r in obs["rows"]]
        return "OBSERVATION (subruns):\n" + "\n".join(lines)
    if kind == "kill":
        if obs.get("error"):
            return f"OBSERVATION (kill FAILED): {obs['error']}"
        if obs.get("already_finished"):
            return (f"OBSERVATION (kill): sub-workflow {obs['n']} had already finished "
                    f"({obs['status']}).")
        return f"OBSERVATION (kill): sub-workflow {obs['n']} terminated ({obs.get('status')})."
    if kind == "wait":
        if obs.get("error"):
            return f"OBSERVATION (wait FAILED): {obs['error']}"
        parts = []
        for f in obs.get("finished", []):
            noun = "SUBTASK" if f.get("mode") == "sequential" else "SUB-WORKFLOW"
            parts.append(f"{noun} {f['n']} {f['label']!r} FINISHED "
                         f"(status {f['status']}, {f['turns']} turns):\n{f['summary']}")
        if obs.get("interrupted_by_user"):
            parts.append("Wait PAUSED — a user message just arrived (delivered next). Handle "
                         "it, then `wait` again for the still-running child(ren) "
                         f"{obs.get('still_running')} when you are ready to continue the "
                         "sequence.")
        elif obs.get("timed_out"):
            parts.append(f"wait timed out; still running: {obs.get('still_running')}")
        elif not parts:
            parts.append("nothing new finished")
        return "OBSERVATION (wait):\n" + "\n\n".join(parts)
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
    return f"OBSERVATION ({kind}): {json.dumps(obs, ensure_ascii=False)[:500]}"
