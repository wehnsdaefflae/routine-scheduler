"""Observation wording for the LIBRARY-writing kinds — `write_util`, `remove_util`, `write_rule`.

Split out of `observations.py` (F393) by subject area, so each kind's strings still live in
exactly one place. What these share is that the run just changed something every OTHER routine
will see, so the wording has to say what landed, what it was checked against, and — for a
refusal — which rung of the approval ladder stopped it. An ask-back on the approval is worded
by `obs_admin.dialog_reply`, the one wording every blocking decision shares; this module
supplies only each kind's way of re-submitting.
"""

from __future__ import annotations

from .obs_admin import dialog_reply


def _selftest_phase(output: str) -> str:
    """Name WHICH PHASE a refused util write failed in, when the captured traceback says.

    A selftest failure has two very different meanings and the author needs to know which
    before reading a line number. If the deepest frame is `in <module>`, the script did not
    survive being imported — a syntax error, a missing import, a name the dispatch table
    references but the file does not define — and no assertion ever ran. If the deepest frame
    is inside a function, the script loaded and a check failed.

    Those two call for opposite repairs, and the distinction was invisible: three reports
    (R2036, R2037, R2042) from one run describe six consecutive refusals of large util folds,
    each answered with a line number, none with the fact that every one of them broke at
    module level before any test executed. That run spent ~20 turns testing hypotheses about
    its test logic while the file was not importable.

    Best-effort by design: it reads the traceback the observation ALREADY carries and adds a
    sentence, never a verdict of its own. An unrecognised shape adds nothing.
    """
    frames = [ln for ln in output.splitlines() if ln.lstrip().startswith('File "')]
    if not frames:
        return ""
    if frames[-1].rstrip().endswith("in <module>"):
        return ("It failed AT IMPORT — the last frame is module level, so no selftest check ran "
                "yet: the script does not load (a syntax error, a missing import, or a name the "
                "dispatch references but the file does not define). Fix the load, not the test "
                "logic.\n")
    return ("It failed INSIDE a check — the script loaded and ran, so the selftest itself is "
            "what objected. The last frame names where.\n")


#: An ask-back on a library write's approval, per kind: how the action is re-submitted and what
#: stays untouched until the operator decides — the two blanks of `obs_admin.dialog_reply`.
_ASKED_BACK = {
    "write_util": (("re-submit write_util {name!r} (as it was, or revised in light of their "
                    "message), your answer in its `say`"),
                   "Nothing reaches the library until they approve."),
    "remove_util": ("re-submit remove_util {name!r}, your answer in its `say`",
                    "The util stays in the library until they approve."),
    "write_rule": (("re-submit write_rule {name!r} (as it was, or revised in light of their "
                    "message), your answer in its `say`"),
                   "The rule is unchanged until they approve."),
}


def _asked_back(obs: dict, kind: str) -> str:
    """The approval this write is waiting on got an ask-back: the operator's words and the way
    to re-submit, in the one wording every blocking decision shares.
    """
    resubmit, until = _ASKED_BACK[kind]
    head = f"{obs['name']!r}" if kind != "write_rule" else obs["name"]
    return (f"OBSERVATION ({kind} {head}): "
            + dialog_reply(obs, "approval", resubmit.format(name=obs["name"]), until))


def format_library(obs: dict, kind: str) -> str | None:  # noqa: PLR0911 — one flat renderer per module, by design: observation wording is PROMPT SURFACE (docs/prompt-anatomy.md) and every branch is a distinct string for a distinct kind. Collapsing them would scatter a kind's wording, which is exactly what this shape exists to prevent.
    """Wording for this module's kinds; None when `kind` is not one of them."""
    if kind in _ASKED_BACK and obs.get("dialog"):
        # checked before `pending_approval`, which an ask-back also carries: the decision IS
        # still pending, but the run owes the operator an answer first
        return _asked_back(obs, kind)
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
                    f"{obs.get('output', '')}\n{_selftest_phase(obs.get('output') or '')}"
                    "Fix the script and write_util again.")
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
