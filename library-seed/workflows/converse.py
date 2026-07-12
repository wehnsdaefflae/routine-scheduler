"""Converse — the interactive conversation harness (the Conversations tab materializes it).

A conversation is a routine-shaped dir with NO schedule: the user's FIRST message is the
instruction, every later message arrives as an injected USER MESSAGE, and between replies
the run is FINISHED — the engine resumes it in place when the user writes again, with a
fresh (small) budget window per reply. This file is a PATTERN, not a program: the
orchestrator acts it out, one engine action per turn.
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time; each names one piece of information fixed per
# conversation. TASK is simply the instruction — the first thing the user typed.
from routine.params import (
    TASK,        # str — the user's first message (instruction.md); follow-ups EXTEND or REVISE it
    WORKDIR,     # str — optional project directory among the fs roots ("" = none)
)

from routine.actions import read_file, write_file, util, write_util, llm, spawn, wait, ask_user, finish

META = {
    "name": "Converse",
    "slug": "converse",
    "description": "Interactive conversation: triage each user message, answer follow-ups "
                   "directly, do real work in small verified steps, deliver artifacts, and "
                   "finish EVERY reply (the finish summary IS the chat reply).",
    "when_to_use": "Conversations only — the Conversations tab materializes this pattern into "
                   "each new conversation. Not for scheduled routines: there is no schedule, "
                   "and the reply cycle assumes a user who reads the answer and writes back.",
    "version": 1,
    "status": "stable",
    # "meta" keeps it out of spawn-pattern lists and wizard suggestions — a conversation
    # harness assumes a present user; it is materialized ONLY by the Conversations tab.
    "tags": ["conversation", "interactive", "assistant", "meta"],
    "includes": ["ask-policy", "global-utils", "web-research", "ledger-discipline",
                 "git-checkpoint"],
    "tools": None,          # a conversation may use every action kind its permissions allow
}

PHASES = ["conversation"]   # a conversation has no cross-run milestones — it is one open thread
COMPLETION = (
    "per reply: the NEWEST user message is addressed and an authored finish carries the reply; "
    "overall: open-ended — the conversation lives until the user stops writing or deletes it"
)


def main():
    """One REPLY cycle: from the newest user message to a finish whose summary answers it."""
    message = newest_user_message()
    kind = triage(message)               # follow-up | task | new-topic — judged, not computed
    if kind == "follow-up":
        return reply(answer(message))    # 1-2 turns; NEVER redo work already in this conversation
    result = work(message)               # the real-work case: plan briefly, act, verify
    return reply(result, new_topic=(kind == "new-topic"))


def newest_user_message():
    """On the first run the message is the INSTRUCTION itself. On every later cycle it is the
    LAST injected USER MESSAGE (or the messages in the state digest / after the engine's
    continued-conversation note). Earlier messages and your earlier replies are context — the
    task is whatever the newest message asks, read as an extension or revision of it."""


def triage(message):
    """Judge the newest message before acting — the reply budget is small (~10 turns), so
    spend it deliberately:
    - follow-up: answerable from what this conversation has already established (a question
      about work you did, a clarification, an opinion). Answer directly; do not re-execute.
    - task: needs actions — files read or written, utils run, things verified. Most first
      messages are tasks.
    - new-topic: clearly departs from this conversation's task (unrelated subject, different
      project). Still handle it, but flag it in the reply (see reply()) so the user can fork
      a fresh conversation — one conversation, one topic keeps context useful."""


def answer(message):
    """Answer a follow-up from the conversation's own context: your earlier observations,
    LEDGER.md, state/, artifacts/. Re-read a file only if the answer depends on its current
    content. One or two turns, then reply."""


def work(message):
    """Do the work in small verified steps — this is the Claude-Code-like case:
    - Attachments: a message may carry an '[attached files]' block of paths. read_file the
      text ones; run the `vision` util for images/PDFs; pick a fitting util for other
      binary formats. Never guess at an attachment's content.
    - Project edits: work under WORKDIR (your fs read/write roots). Follow
      traits/git-checkpoint.md — a checkpoint commit BEFORE risky edits, one after coherent
      work, named in your reply.
    - Verify what you produce: read it back, check exit codes, count results. A claimed but
      unverified outcome is the worst failure this system knows.
    - Budget: you have roughly 10 turns per reply. If the engine's BUDGET warning appears,
      STOP starting new work — write down where you are (LEDGER.md), then reply with honest
      progress and end with: say 'continue' and I will pick up right here. The user's
      'continue' opens a fresh window in this same conversation.
    - A genuinely big, parallelizable job may spawn sub-workflows — but prefer doing the
      work directly; a conversation is interactive, not a batch scheduler.
    - Ask (ask_user, blocking) when a decision is genuinely the user's — they are usually
      present in a conversation; still, batch what can wait into the reply itself."""


def artifacts():
    """Deliverables that are MORE than a chat answer — reports, generated pages, images,
    data files, documents — are written into artifacts/ with write_file (e.g.
    artifacts/report.md, artifacts/chart.html). The UI renders that folder in a side panel:
    html, markdown, images, PDF, CSV, JSON and code all display inline. Re-writing the same
    filename updates the artifact in place. Name every artifact you produced in the reply."""


def reply(result, new_topic=False):
    """EVERY reply is an authored finish: status ok (or partial when you ran out of budget
    mid-work), and the summary is the MESSAGE the user reads in the chat — direct,
    conversational markdown grounded in this cycle's observations. Include: what you did or
    found, artifact filenames if any, checkpoint commits if any, and open questions. If
    new_topic, make the summary's FIRST line exactly `[new-topic] <a short title for the
    suggested new conversation>` and answer on the lines below — the UI turns that marker
    into a one-click fork button. Do NOT ask 'anything else?' filler — end when answered."""
    return finish("ok", "the reply the user reads")


if __name__ == "__main__":
    main()
