"""Observation wording for CHILD RUNS — every scheduling mode of the one concept.

Split out of `observations.py` (F393). One vocabulary across the modes (engine/child.py): a
child has its own dir, its own budget and its own context; it hands work back by writing into
its own `artifacts/`. The wording says so at the point the parent starts one, which is where a
run would otherwise assume it shares the parent's working directory.
"""

from __future__ import annotations

from . import child


def format_children(obs: dict, kind: str) -> str | None:  # noqa: PLR0911 — one flat renderer per module, by design: observation wording is PROMPT SURFACE (docs/prompt-anatomy.md) and every branch is a distinct string for a distinct kind. Collapsing them would scatter a kind's wording, which is exactly what this shape exists to prevent.
    """Wording for this module's kinds; None when `kind` is not one of them."""
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
                "and fold its result into that brief; the wait yields if the user writes; you "
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
            noun = child.mode_shout(str(f.get("mode") or ""))
            # F338: name the deliverables the engine copied up. The WAIT is one of the two
            # paths that report a child's exit (the turn-boundary announcement is the other),
            # and which one wins is a timing race — so both must say where the files landed,
            # or a parent that happened to be waiting never learns.
            got = (" Collected from the child into your artifacts/: "
                   + ", ".join(f["collected"]) if f.get("collected") else "")
            parts.append(f"{noun} {f['n']} {f['label']!r} FINISHED "
                         f"(status {f['status']}, {f['turns']} turns):\n{f['summary']}{got}")
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
    return None
