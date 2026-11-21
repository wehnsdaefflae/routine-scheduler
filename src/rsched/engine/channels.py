"""The DELIVERY CHANNELS a message can arrive on — `via`, and the policy it decides.

`engine/inbox.py` owns the `msg-*` FILE SHAPE and its consume-by-rename discipline; `via` is not
part of the shape but the switch three subsystems read — when a message is consumed, whether
the reap counts a user talking to a finished run, and whether the conversation surface offers it
as the user's own editable text.

Every name here is re-exported by `engine/inbox`.
"""

from __future__ import annotations

#: The injection channels that mean "the user is talking to THIS run": the conversation
#: composer and the run page. The daemon's post-finish sweep re-opens a finished run only
#: for these — a machine delivery waits for the run's next leg instead of buying one.
USER_MESSAGE_VIAS = ("conversation", "web", "web-converse")

#: What a LIVE run may consume at a mid-run turn boundary, and what a RESUMED leg's boot
#: drains: the user talking to THIS run plus the results of work this run itself started — a
#: detached task's delivery, a branch's hand-back, and a one-shot's fire text (a conversation's
#: one-shot fires by RESUMING it, so its text must be drainable here). Everything else —
#: reports, audit feedback, queued messages, trigger texts, answers to other runs' questions —
#: is for the routine's NEXT FRESH run and consumed only by its boot, never mid-flight (D92/D93).
#: `oversight` is the one MACHINE channel that must reach a run mid-flight — a directive
#: arriving next run would come after the drift it corrects — and only at a turn boundary,
#: never as a prefix rewrite (the caching contract).
LIVE_MESSAGE_VIAS = (*USER_MESSAGE_VIAS, "background", "branch", "schedule_once",
                     "oversight")

#: The CLOSED set of delivery channels — the delivery POLICY, not a label. A writer names a
#: channel this set knows, or `file_message` refuses the write. "" is the routine page's queued
#: message: the operator typing with no surface of its own to name.
VIAS = frozenset({
    "",               # web/api_messages — the routine page's queued message
    "conversation",   # web/api_conversations — the composer
    "web",            # web/api_run_control, engine/interact — the live run view
    "web-converse",   # web/api_run_control — the run view's converse box
    "web-audit",      # web/api_audit — self-audit feedback and decisions
    "report",         # reports.file_report — a sibling routine's addressed report
    "background",     # daemon/detached_delivery — a detached task's result; also
                      # daemon/runner_reap's D99 recovery note, which a resumed leg must drain
    "branch",         # branches.hand_back — a forked conversation's result
    "trigger",        # daemon/triggers — a configured event's text
    "schedule_once",  # daemon/schedule_once — a one-shot fire's provenance
    "pending",        # pending.notify_proposer — the outcome of a queued proposal; also
                      # web/api_owed_obligation — the operator's decision on an obligation
    "oversight",      # engine/control — an escalation rung's directive, binding the run below
    "hook",           # engine/runtree — another run of this run TREE speaking (parent↔child)
})

#: The channels whose freight a MACHINE authored. `ctx.user_replies` — read by the
#: create_routine confirm gate (has the user spoken since the draft?) and by the `user-spoke`
#: assist predicate — counts the user talking, so a report, a background result or a branch
#: hand-back must not advance it. Everything else on VIAS is a person at a console.
MACHINE_VIAS = frozenset({"report", "background", "branch", "trigger", "schedule_once",
                          "pending", "oversight", "hook"})


def user_authored(via: str) -> bool:
    """Is freight on this channel the USER talking? The one answer, for every consumer."""
    return via not in MACHINE_VIAS


#: What a mid-run injection is LABELLED as in the prompt, by channel — the channel knows who
#: spoke, and "USER MESSAGE" on a machine's words names the wrong sender. The live and REPLAY
#: renderers both read this table, or a resumed leg's prefix differs and misses the cache.
INJECTED_LEADS = {"hook": "HOOK (injected mid-run)"}
DEFAULT_INJECTED_LEAD = "USER MESSAGE (injected mid-run)"


def injected_lead(via: str = "", *, report: bool = False) -> str:
    """The lead line for one injected message. `report` is a flag rather than a channel
    because a report's delivery predates the closed channel set and its messages carry the
    key, not the via.
    """
    if report:
        return "REPORT (injected mid-run)"
    return INJECTED_LEADS.get(str(via or ""), DEFAULT_INJECTED_LEAD)


def injected_message(text: str, *, report: bool = False, via: str = "") -> str:
    """One rendering for a mid-run injected message — live and replayed prompts must read
    identically, or a resumed leg's prefix differs from the leg that wrote it and the whole
    conversation is re-written to the provider instead of re-read from its cache.

    A report or a RUN-TREE hook is not the operator speaking, so neither is labelled a USER
    MESSAGE. `engine/control` re-exports this.
    """
    return f"{injected_lead(via, report=report)}:\n{text}"
