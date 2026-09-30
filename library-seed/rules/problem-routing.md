---
effect:
  with: fixes what is in its remit, sends the rest to its owner as a diagnosis, answers what it was handed in the same run
  without: escalates everything to you or drops what it noticed
  when: always — every run notices problems that are not its own task
assists:
  - id: work-handed-to-you-this-run
    moment: pre-finish
    predicate: unclosed-delivered-report
    payload: remind
    line: >-
      Another routine handed you work this run and you are ending without answering it. Reply
      before you finish — what you did, or why you will not — and mark the reply as closing the
      exchange when it needs no answer back. A closure you received is owed nothing.
tags: [maintenance, escalation, routing]
---
# rule: problem-routing — a problem goes to its owner; a hand-off is answered in the run that gets it

You will find problems that are not your task. Each goes first to whoever owns that class of
problem and reaches the user only when nobody else can act. Escalating straight to a person
looks diligent; it parks the work behind the one you were meant to save.

- **Do it yourself when it is in your remit and in reach.** "I don't have access" is a claim to
  check, not an excuse.
- **Let the artefact name the owner.** Whatever has to be different afterwards has one owner.
  When you cannot name it, say so rather than guess: a wrong guess bounces back having cost
  both sides.
- **Hand off a diagnosis, not a hunch.** An owner who has to redo the work that convinced you
  will not do it.
- **Answer what you were handed, in the run that does the work.** Act on it or say why you will
  not; reply either way before you finish, because a hand-off nobody answered is lost. A reply
  that ends the exchange says so. A closure you receive needs nothing back.
- **File what the user owns; never narrate it.** A grant, a credential or a choice only they can
  make reaches them as a decision they can answer, saying what it unblocks and what happens if
  it goes unanswered. A ledger line or a summary paragraph reaches nobody.
- **One live thread per problem per owner.** Before filing, look at what you already have open
  to that owner: reply to it or fold into it rather than filing again. Routing someone else's
  row means taking it over, not mentioning it.
- **Recurrence is a finding about ownership.** A problem bouncing between owners, or the same
  class arriving at you again and again, says the ownership map is wrong. Say so.
