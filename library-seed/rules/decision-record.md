---
effect:
  with: appends what it changed, chose and rejected where the next run reads it first
  without: leaves only artefacts; the next run re-buys old lessons
  when: a routine that works one problem across runs — not a conversation, whose plan file is its spine
assists:
  - id: ledger-before-finish
    moment: pre-finish
    predicate: ledger-untouched
    payload: remind
    line: >-
      Append one LEDGER.md entry — what changed, what you chose, what you rejected and why —
      before the reasoning leaves with this run. If this run already appended one, finish again.
tags: [self-management, record-keeping, review]
---
# rule: decision-record — keep the reasoning the artefacts cannot carry

What you produce shows what the state is. It cannot show what you tried and abandoned, what
you weighed or why the obvious approach was wrong — exactly what a later run needs so it does
not buy the same lesson twice. Keep that record in the ledger (LEDGER.md), whose tail every
run's digest carries.

- **Read the record before you explore.** A dead end already paid for is not worth
  rediscovering; an idea already rejected is not worth proposing again. Search the whole record
  when the tail does not settle it.
- **Append, never rewrite.** The record is chronological: entries are added, not edited.
- **One entry per run, before you finish.** What changed and why, the decisions you took, the
  candidates you rejected with the reason for each. Negative evidence is the most valuable part
  and the first thing a hurried run drops.
- **Write for a reader without your context.** Name the artefact, give the evidence, say what
  done looked like. A note only you could decode records nothing.
- **Rotate before it outgrows a reader.** Past roughly 400 lines or 40 entries, move the older
  entries to an archive beside the record in the same run; leave one rollup entry naming what
  moved and the lessons that must stay. Noticing the overflow and deferring the rotation is how
  a record becomes unreadable.
- **Artefacts stay present-tense.** What a thing is belongs in the thing; how it got there
  belongs in the record.
