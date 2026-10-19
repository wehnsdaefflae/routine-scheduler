"""The DELIVERY CHANNELS a message can arrive on — `via`, and the policy it decides.

Lifted out of `engine/inbox.py` (D152 step 3, 2026-10-19) when that module reached its size
ceiling and the next channel — `hook`, a run of this same run tree speaking (F695) — had
nowhere to go. The split is the one the inbox docstring already drew: that module owns the
`msg-*` FILE SHAPE and its consume-by-rename discipline, and `via` is not part of the shape.
It is the switch three unrelated subsystems read — when a message is consumed, whether the
reap counts a user talking to a finished run, whether the conversation surface offers it as
the user's own editable text — and it was chosen freehand at every writer before the set was
closed.

Every name here is re-exported by `engine/inbox`, so `inbox.VIAS`, `inbox.LIVE_MESSAGE_VIAS`
and `inbox.user_authored` keep resolving and no caller moved.
"""

from __future__ import annotations

#: The injection channels that mean "the user is talking to THIS run": the conversation
#: composer and the run page. The daemon's post-finish sweep re-opens a finished run only
#: for these (R108/F268) — a machine delivery waits for the run's next leg instead of
#: buying one.
USER_MESSAGE_VIAS = ("conversation", "web", "web-converse")

#: What a LIVE run may consume at a mid-run turn boundary, and what a RESUMED leg's boot
#: drains (F359, generalized by the user order of 2026-08-20): the user talking to THIS run
#: (USER_MESSAGE_VIAS) plus the results of work this run itself started — a detached
#: background task's delivery (the daemon's delivery contract counts on the live owner
#: draining it at the next boundary, daemon/detached_delivery.wake), a branch's hand-back
#: (branches.hand_back: the parent's next reply reads it) and a one-shot's fire text. A
#: one-shot's text is filed only by the fire it starts (daemon/schedule_once._fire), and a
#: CONVERSATION's one-shot — the "remind me in 3 days" it armed for itself — fires by RESUMING
#: it: with the text off this set that leg drained nothing, ran without the reminder, and the
#: message sat in the inbox forever, since a conversation never boots fresh. A routine's
#: one-shot fires a fresh run, which drains everything anyway. Everything else — reports,
#: audit feedback, routine-page queued messages, trigger texts, answers to other runs'
#: questions — is addressed to the routine's NEXT FRESH run and is consumed only by that
#: run's boot, never mid-flight: a follow-up leg draining it wholesale silently ate decision
#: answers meant for that night's run (D92/D93). Mid-run injection into a running run is the
#: live run view's channel, by design.
#: `oversight` is here for the reason the whole ladder exists: a directive that only reached
#: the routine's NEXT FRESH run would arrive after the drift it was written to correct. It is
#: the one MACHINE channel that must reach a run mid-flight, and it may do so only at a turn
#: boundary — never as a prefix rewrite, because the message list is a prompt-caching
#: contract with exactly three sanctioned rewriters and a supervisor must not become a fourth.
LIVE_MESSAGE_VIAS = (*USER_MESSAGE_VIAS, "background", "branch", "schedule_once",
                     "oversight")

#: The CLOSED set of delivery channels. `via` is not a label: it is the switch that decides
#: when a message is consumed (the two tuples above), whether the reap counts it as a user
#: talking to a finished run (daemon/runner_state), and whether the conversation surface
#: offers it as the user's own editable text (web/api_conversations). It was chosen freehand
#: at every writer, which is how a branch hand-back came to be filed on the USER channel and
#: was read by the parent as the operator speaking. A writer now names a channel this set
#: knows, or `file_message` refuses the write.
#:
#: "" is a channel too — the routine page's queued message, which is the operator typing with
#: no surface of its own to name.
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
    "pending",        # pending.notify_proposer — the outcome of a queued proposal
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


#: What a mid-run injection is LABELLED as in the prompt, by channel. The channel decides,
#: because the channel is what knows who spoke: a delivered report carries its own "REPORT
#: <id> from routine <slug>" heading and a run-tree hook says which run is speaking, so
#: labelling either "USER MESSAGE" names the wrong sender — and a run that cannot tell a
#: machine from its operator answers the wrong party. The live renderer and the REPLAY
#: renderer both read this table (`control.injected_message`, `history.replay_messages`): a
#: resumed leg whose prefix differs by one label is re-written to the provider instead of
#: re-read from its cache.
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

    A delivered report already carries its own "REPORT <id> from routine <slug>" heading
    (reports.message_text) — labelling it a USER MESSAGE would name the wrong sender. The same
    holds for a RUN-TREE hook (D152 step 3): the parent or child speaking is not the operator.
    `engine/control` re-exports this under the name every caller already uses.
    """
    return f"{injected_lead(via, report=report)}:\n{text}"
