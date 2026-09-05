"""Observation wording for the LIBRARY-writing kinds — `write_util`, `remove_util`, `write_rule`.

Split out of `observations.py` (F393) by subject area, so each kind's strings still live in
exactly one place. What these share is that the run just changed something every OTHER routine
will see, so the wording has to say what landed, what it was checked against, and — for a
refusal — which rung of the approval ladder stopped it.
"""

from __future__ import annotations


def format_library(obs: dict, kind: str) -> str | None:  # noqa: PLR0911 — one flat renderer per module, by design: observation wording is PROMPT SURFACE (docs/prompt-anatomy.md) and every branch is a distinct string for a distinct kind. Collapsing them would scatter a kind's wording, which is exactly what this shape exists to prevent.
    """Wording for this module's kinds; None when `kind` is not one of them."""
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
    return None
