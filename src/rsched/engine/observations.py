"""Observation rendering — the dispatch result of one action, turned into the next user
message (`format_observation`), plus the head+tail truncation every large output rides
through. The transcript renderer's counterpart: observation wording is prompt surface
(docs/prompt-anatomy.md) and lives here in ONE place per kind.
"""

from __future__ import annotations

import json

OBS_CAP_CHARS = 8_000


def truncate(text: str, cap: int = OBS_CAP_CHARS) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    head = int(cap * 0.6)
    tail = cap - head
    marker = f"\n[... output truncated: showing {cap} of {len(text)} chars (head+tail) ...]\n"
    return (text[:head] + marker + text[-tail:]), True


# One flat renderer on purpose: observation wording is prompt surface (docs/prompt-anatomy.md)
# and lives in ONE place per kind — a dispatch table would only scatter the strings.
def format_observation(obs: dict) -> str:  # noqa: C901, PLR0911, PLR0912, PLR0915
    kind = obs.get("kind")
    if kind == "util":
        if obs.get("listing") is not None:
            return "OBSERVATION (util list — available global utils):\n" + obs["listing"]
        if obs.get("source") is not None:
            return (f"OBSERVATION (util show — source of {obs['target']!r}; to revise it, "
                    "write_util the COMPLETE corrected script):\n" + obs["source"])
        if obs.get("missing"):
            return (f"OBSERVATION (util {(obs.get('target') or obs['name'])!r} does not exist). "
                    "Run `util name=list` to see what exists, or write it with write_util, "
                    "then call it.")
        head = f"OBSERVATION (util {obs['name']}, exit {obs['exit']})"
        body = obs.get("stdout") or "(no stdout)"
        if obs.get("stderr"):
            body += f"\n[stderr]\n{obs['stderr']}"
        if obs.get("usage"):
            body += f"\n[usage] {obs['usage']}"
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
    if kind == "report_bug":
        if obs.get("filed"):
            return (f"OBSERVATION (report_bug filed: {obs.get('title')!r} — appended to "
                    ".control/bug-reports.jsonl; the self-audit routine will review it. "
                    "Continue your own task.)")
        return ("OBSERVATION (report_bug: could NOT write the bug-reports log (I/O error) — "
                "the report was not filed. Continue your own task; mention the bug in your "
                "finish summary instead.)")
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
    if kind == "read_trait":
        if obs["name"] == "list":
            rows = "\n".join(f"- {t['slug']}{' (already yours)' if t['held'] else ''}: "
                             f"{t['summary']}" for t in obs["traits"]) or "(library is empty)"
            return ("OBSERVATION (read_trait list) — practice modules in the shared library. "
                    "Reading one applies it to THIS run only; making it a standing practice is "
                    f"the user's call:\n{rows}")
        if obs.get("missing"):
            avail = ", ".join(obs.get("available") or []) or "(none)"
            return (f"OBSERVATION (read_trait): no practice module named {obs['name']!r}. "
                    f"Available: {avail}.")
        already = (" — this is ALREADY one of your standing practices"
                   if obs.get("held") else "")
        return (f"OBSERVATION (read_trait {obs['name']}, {obs['lines']} lines{already}). "
                "It applies for the rest of this run; it is not added to your recipe:\n"
                f"{obs['content']}")
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
