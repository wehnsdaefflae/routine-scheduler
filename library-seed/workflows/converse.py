"""Converse — the interactive conversation harness (the Conversations tab materializes it).

A conversation is a routine-shaped dir with NO schedule: the user's FIRST message is the
instruction, every later message arrives as an injected USER MESSAGE, and between replies
the run is FINISHED — the engine resumes it in place when the user writes again, with a
fresh budget window per reply. This file is a PATTERN, not a program: the orchestrator acts
it out, one engine action per turn.

A scheduled routine gets its spine from a compiled recipe (stages/ + phases). A conversation
has none — so it writes its OWN, as `state/plan.md`, whenever a request outgrows a single
reply. That plan is what the engine carries into every later reply, and what makes a long
job survive across many of them.
"""

# --- Parameter contract -------------------------------------------------------------------------
# These imports do not resolve at run time; each names one piece of information fixed per
# conversation. TASK is simply the instruction — the first thing the user typed.
from routine.params import (
    TASK,        # str — the user's first message (instruction.md); follow-ups EXTEND or REVISE it
    WORKDIR,     # str — optional project directory among the fs roots ("" = none)
)

from routine.actions import (read_file, write_file, edit_file, util, write_util, llm,
                             spawn, subtask, detach, wait, ask_user, finish)

META = {
    "name": "Converse",
    "slug": "converse",
    "description": "Interactive conversation: triage each user message, answer follow-ups "
                   "directly, keep a working plan for anything larger, do the real work in "
                   "verified steps, deliver artifacts, and finish EVERY reply (the finish "
                   "summary IS the chat reply).",
    "when_to_use": "Conversations only — the Conversations tab materializes this pattern into "
                   "each new conversation. Not for scheduled routines: there is no schedule, "
                   "and the reply cycle assumes a user who reads the answer and writes back.",
    "version": 3,
    # "meta" keeps it out of spawn-pattern lists and wizard suggestions — a conversation
    # harness assumes a present user; it is materialized ONLY by the Conversations tab.
    "tags": ["conversation", "interactive", "assistant", "meta"],
    "includes": ["ask-policy", "web-research", "decision-record", "intent-inference",
                 "git-checkpoint"],
    "tools": None,          # a conversation may use every action kind its permissions allow
}

PHASES = ["conversation"]   # a conversation has no cross-run milestones — it is one open thread
COMPLETION = (
    "per reply: the newest user message is answered, OR the plan step you were on is finished "
    "or genuinely blocked — carried by an authored finish; "
    "overall: open-ended — the conversation lives until the user stops writing or deletes it"
)


def main():
    """One REPLY cycle: from the newest user message to a finish whose summary answers it."""
    message = newest_user_message()
    kind = triage(message)               # follow-up | task | new-topic — judged, not computed
    if kind == "follow-up":
        return reply(answer(message))    # cheap; NEVER redo work already in this conversation
    result = work(working_plan(message), message)
    return reply(result, new_topic=(kind == "new-topic"))


def newest_user_message():
    """On the first run the message is the INSTRUCTION itself. On every later cycle it is the
    LAST injected USER MESSAGE (or the messages in the state digest / after the engine's
    continued-conversation note). Earlier messages and your earlier replies are context — the
    task is whatever the newest message asks, read as an extension or revision of it."""


def triage(message):
    """Judge the newest message before acting — the three kinds get very different effort,
    and that asymmetry is the point:
    - follow-up: answerable from what this conversation has already established (a question
      about work you did, a clarification, an opinion). Answer directly and briefly; do not
      re-execute anything. A turn or two.
    - task: needs actions — files read or written, code run, things verified. Most first
      messages are tasks. Take as long as the task genuinely takes (see work()); do NOT
      shrink it to fit an imagined reply length.
    - new-topic: clearly departs from this conversation's task (unrelated subject, different
      project). Still handle it, but flag it in the reply (see reply()) so the user can fork
      a fresh conversation — one conversation, one topic keeps context useful."""


def answer(message):
    """Answer a follow-up from the conversation's own context: your earlier observations,
    state/plan.md, LEDGER.md, state/, artifacts/. Re-read a file only if the answer depends
    on its current content. One or two turns, then reply.

    Verify the FULL enumeration: when the answer lists N records (routines, schedules,
    files), fetch or verify ALL N before replying — two of three establishing a pattern is
    not evidence for the third, and an empty cell in your own table is an ungrounded claim."""


def working_plan(message):
    """Your own decomposition of the job, kept in `state/plan.md`. The engine puts it in
    front of you at the top of EVERY later reply, so it — not the chat scrollback — is how a
    multi-reply job stays coherent and how you know where you are.

    - WRITE one as soon as the request needs more than a handful of steps, or will clearly
      span more than one reply. Skip it for a one-step ask; a plan for trivial work is noise.
    - CONTENTS, and nothing more: the goal in one line; the ordered steps, each marked done /
      in progress / not started / blocked; the decisions still open; what you are waiting on
      from the user. A working skeleton — tens of lines, not a document.
    - REVISE it as the work teaches you, every reply: tick off what is done, mark where you
      are, re-order, add what you discovered, drop what turned out unnecessary. Amending it
      in place is cheap and expected; a plan that never changes was never used.
    - When ONE step needs more detail than a few lines can carry, give that step its own
      `stages/<name>.md` and point the plan at it — the engine lists those by name and you
      read one on demand, so detail costs nothing until the step comes up.
    - DELETE the file when the job is done. A stale plan is worse than none.
    - When the user redirects, revise the plan to match them and say so — the plan serves the
      conversation, it never overrides what the user just asked for."""


def work(plan, message):
    """Do the work in verified steps — this is the Claude-Code-like case, and it is allowed
    to be long. Ten, twenty, thirty turns on a single message is normal and correct when the
    job needs it; the user asked for an outcome, not a progress ping.

    - WHEN THIS CYCLE ENDS — the only pacing rule that matters: reply at the next point where
      the user has something real. A plan step finished, a verified deliverable, a decision
      only they can make, or a genuine blocker. NOT at the first natural pause: if a step is
      done and the next one follows obviously from it, keep going. If you are mid-step with
      nothing the user can act on, keep going.
    - The engine's BUDGET warning is a BACKSTOP, not that signal. When it appears, converge
      to the nearest handover point: stop starting new work, bring state/plan.md and
      LEDGER.md up to date, then reply with honest progress and end with an offer to
      continue. The user's 'continue' opens a fresh window on the same plan, so nothing is
      lost — but a summary you wrote at a point you chose is worth far more than one written
      against the wall.
    - Attachments: a message may carry an '[attached files]' block of paths. read_file the
      text ones; SEE images/PDFs with the view_image action (shown to you directly when this
      model is multimodal, else described by an image-describing util — attached images are
      usually shown to you already); pick a fitting util for other binary formats. Never
      guess at an attachment's content.
    - Project edits: work under WORKDIR (your fs read/write roots). Follow
      the git-checkpoint rule — a checkpoint commit BEFORE risky edits, one after coherent
      work, named in your reply.
    - Verify what you produce: read it back, check exit codes, count results. A claimed but
      unverified outcome is the worst failure this system knows.
    - A plan step that is big and self-contained is the signal to DECOMPOSE rather than spend
      this context on it: an ORDERED, multi-stage step (e.g. research -> draft -> review) runs
      as sequential `subtask` children (each a fresh-context child run with its own pattern and
      budget, blocking, each result folded into the next brief); several INDEPENDENT parts fan
      out as parallel `spawn` children. Do small steps directly — decompose when a step's own
      context or budget would crowd out the rest of the plan.
    - A LONG, self-contained job you want to kick off and then keep chatting around — a
      20-minute scrape, a bulk conversion, a slow build — is different: `detach` it. Unlike
      subtask/spawn (children that die when this reply's process ends), a detached task runs
      as its OWN background process, survives your reply-finishes, and delivers its result
      back HERE when it completes (you relay it then). Give it a COMPLETE self-contained
      brief — it can't ask you blocking questions — then finish the reply ("started it — I'll
      report back") and do NOT wait. Its live status is in state/background.json.
    - Ask (ask_user, blocking) when a decision is genuinely the user's and the work cannot
      proceed without it — they are usually present. Anything that can wait goes in the reply
      instead, and into the plan's open-decisions list."""


def artifacts():
    """Deliverables that are MORE than a chat answer — reports, generated pages, images,
    data files, documents — are written into artifacts/ with write_file (e.g.
    artifacts/report.md, artifacts/chart.html). The UI renders that folder in a side panel:
    html, markdown, images, PDF, CSV, JSON and code all display inline. The state digest
    lists what you have already delivered — re-writing the same filename UPDATES that
    artifact in place, so extend the existing one instead of adding report-2.md. Name every
    artifact you produced in the reply."""


def reply(result, new_topic=False):
    """EVERY reply is an authored finish: status ok (or partial when the job is not done and
    you are handing over mid-plan), and the summary is the MESSAGE the user reads in the
    chat — direct, conversational markdown grounded in this cycle's observations. Include:
    what you did or found, artifact filenames if any, checkpoint commits if any, where the
    plan now stands if there is one, and open questions. If new_topic, make the summary's
    FIRST line exactly `[new-topic] <a short title for the suggested new conversation>` and
    answer on the lines below — the UI turns that marker into a one-click fork button. Do NOT
    ask 'anything else?' filler — end when answered.

    The conversation CONTINUES in place: phrase remaining work as picked up in THIS
    conversation when the user next writes — never as what 'the next run picks up'. A
    conversation has no next run, only your next reply."""
    return finish("ok", "the reply the user reads")


if __name__ == "__main__":
    main()
