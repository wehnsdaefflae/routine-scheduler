"""Observation wording for CHILD RUNS — every scheduling mode of the one concept.

Split out of `observations.py` (F393). One vocabulary across the modes (engine/child.py): a
child has its own dir, its own budget and its own context; it hands work back by writing into
its own `artifacts/`. The wording says so at the point the parent starts one, which is where a
run would otherwise assume it shares the parent's working directory.
"""

from __future__ import annotations

from . import background_status, child
from .subsalvage import STATE as ORPHANED


def _salvaged(row: dict) -> str:
    """What an ORPHANED row adds: where the work is, and that nothing will be handed back.

    Only the orphan carries it (a live child's hand-back is copied into the parent's own
    artifacts/ on exit), so a reader meeting this suffix knows the engine is out of the loop
    and the directory is the deliverable.
    """
    if row.get("state") != ORPHANED:
        return ""
    landed = ", ".join(row.get("artifacts") or []) or "nothing in artifacts/"
    return (f"\n    started by an EARLIER LEG — not waitable, not killable. On disk: "
            f"{row.get('dir')} (hand-back: {landed})")


def format_children(obs: dict, kind: str) -> str | None:  # noqa: PLR0911 — one flat renderer per module, by design: observation wording is PROMPT SURFACE (docs/prompt-anatomy.md) and every branch is a distinct string for a distinct kind. Collapsing them would scatter a kind's wording, which is exactly what this shape exists to prevent.
    """Wording for this module's kinds; None when `kind` is not one of them."""
    if kind == "child":
        # D241: ONE kind, so ONE branch — the MODE chooses the wording, because what differs
        # between a parallel and a sequential child is what the caller does next, not what the
        # child is. The old two branches said "sub-workflow" and "sequential child" for the
        # same thing, which is the vocabulary this decision exists to end.
        if obs.get("rejected"):
            return f"OBSERVATION (child REJECTED): {obs['reason']}"
        note = f" [{obs['note']}]" if obs.get("note") else ""
        if str(obs.get("mode") or "") == "sequential":
            return (f"OBSERVATION (child, sequential): child run {obs['n']} "
                    f"{obs.get('label')!r} started (workflow {obs.get('workflow')}){note} — it "
                    "runs in the BACKGROUND. To keep the order, `wait` for it "
                    f"(n={obs['n']}) before starting the next one and fold its result into that "
                    "brief; the wait yields if the user writes; you are notified when it "
                    "finishes. Or do other work meanwhile.")
        return (f"OBSERVATION (child, parallel): child run {obs['n']} {obs.get('label')!r} "
                f"started (workflow {obs.get('workflow')}, now {obs.get('running')} "
                f"running).{note} It works in parallel — you will be notified when it "
                "finishes; keep going.")
    if kind == "detach":
        if obs.get("rejected"):
            return f"OBSERVATION (detach REJECTED): {obs['reason']}"
        return (f"OBSERVATION (detach): background task {obs.get('label')!r} started "
                f"(id {obs.get('taskid')}, workflow {obs.get('workflow')}). It runs as its OWN "
                "process, independent of this reply — you will be notified HERE when it finishes "
                "and can then relay its result. Do NOT wait: finish this reply now (tell the user "
                "you started it and will report back).")
    if kind == "children":
        bg_rows = background_status.lines(obs.get("background") or [])
        if not obs.get("rows"):
            # Never "none spawned" (F732): the registry is in-memory, so a RESUMED leg knows
            # only about this leg's children — and the sentence it used to print was a claim
            # about the whole run that a fresh leg has no standing to make. It is also a claim
            # about BACKGROUND work it has no standing to make (R3600): the operator met it
            # with a `bg1` script in flight, so it only speaks when nothing is running either.
            if not bg_rows:
                return ("OBSERVATION (children): no record of a child in THIS leg — and none on "
                        "disk either, so none was started.")
            return ("OBSERVATION (children): no child run in THIS leg, and background work is "
                    "in flight:\n" + "\n".join(bg_rows))
        lines = [f"- #{r['n']} {r['label']!r} [{r['workflow']}] {r['state']} · "
                 f"{r['turns']} turns · {r['elapsed_s']}s"
                 + (f" · {r['summary_head']}" if r["summary_head"] else "")
                 + _salvaged(r)
                 for r in obs["rows"]]
        return "OBSERVATION (children):\n" + "\n".join(lines + bg_rows)
    if kind == "kill":
        if obs.get("error"):
            return f"OBSERVATION (kill FAILED): {obs['error']}"
        if obs.get("handle"):
            # A BACKGROUND call's cancellation (D176 option (a)) — it carries a string handle
            # and no `n`, so this branch comes before every line below that reads `obs['n']`.
            # Rendering it there would raise `KeyError: 'n'` and kill the turn AND every later
            # resume, which re-renders each stored observation: the exact failure the capacity
            # refusal already paid for once.
            return (f"OBSERVATION (kill): background call `{obs['handle']}` cancelled. "
                    + str(obs.get("note") or ""))
        if obs.get("already_finished"):
            return (f"OBSERVATION (kill): sub-workflow {obs['n']} had already finished "
                    f"({obs['status']}).")
        if obs.get("status") == "stopping":
            # It was told to stop and has not yet (a model call no abort interrupts) — saying
            # "terminated" here claimed an exit that had not happened.
            return (f"OBSERVATION (kill): sub-workflow {obs['n']} was told to stop and is "
                    "still winding down — its exit is announced like any other when it lands.")
        return f"OBSERVATION (kill): sub-workflow {obs['n']} terminated ({obs.get('status')})."
    if kind == "wait":
        if obs.get("error"):
            return f"OBSERVATION (wait FAILED): {obs['error']}"
        parts = []
        for f in obs.get("finished", []):
            # F338: name the deliverables the engine copied up. The WAIT is one of the two
            # paths that report a child's exit (the turn-boundary announcement is the other),
            # and which one wins is a timing race — so both say where the files landed, in
            # the ONE spelling, or a parent reads two wordings of one hand-back. D246: the
            # HEADLINE is shared for the same reason, and for a second one — this path used to
            # render it off `MODE_SHOUT`, a mode vocabulary that existed only here.
            got = child.handback_paths_line(tuple(f.get("collected") or ()))
            parts.append(child.finished_headline(
                mode=str(f.get("mode") or ""), n=f["n"], label=f["label"],
                status=f["status"], turns=f["turns"])
                + f":\n{f['summary']}" + (f"\n{got}" if got else ""))
        # a landed background call is DELIVERED by the turn boundary right after this (the one
        # path every landing takes, background.collect) — the wait only names it (waitop.py)
        if landed := obs.get("background_landed"):
            parts.append(f"background call(s) {', '.join(landed)} LANDED — the real "
                         "observation follows below, tagged with its handle.")
        bg = (f"; background still running: {', '.join(obs['background_running'])}"
              if obs.get("background_running") else "")
        if obs.get("interrupted_by_user"):
            parts.append("Wait PAUSED — a user message just arrived (delivered next). Handle "
                         "it, then `wait` again for the still-running child(ren) "
                         f"{obs.get('still_running')}{bg} when you are ready to continue the "
                         "sequence.")
        elif obs.get("timed_out"):
            parts.append(f"wait timed out; still running: {obs.get('still_running')}{bg}")
        elif not parts:
            parts.append("nothing new finished")
        return "OBSERVATION (wait):\n" + "\n\n".join(parts)
    return None
