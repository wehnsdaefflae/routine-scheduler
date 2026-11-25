---
slug: new-routine-from-a-need
title: Turn something I keep needing into a scheduled routine
when: You describe work that recurs — a check, a digest, a mailbox to drain, a site to watch — and want it running on a schedule instead of asked for each time.
tags:
- routines
- scheduling
- automation
axis: the WORK being automated varies; what stays fixed is settling every open point with the person FIRST, as answerable choices, and never materializing a routine on a guess
updated: '2026-11-25'
setup:
  permissions: []
  rules:
  - ask-policy
  - outcome-first
  - work-order
  connections: {}
  fs_read_roots: []
  fs_write_roots: []
  note: >-
    This playbook needs NO reserved permission: creating a routine is a structural capability of
    a conversation, not a grant. What the NEW routine will need — a mailbox, a browser session,
    shell, a folder, an OAuth account — is part of what you settle with the person here, and it
    lands in that routine's own proposed settings for them to accept on its page. Grant nothing
    to this conversation that only the new routine needs.
---

## Parameters

- `{{need}}` — the recurring work, in the person's own words.
- `{{cadence}}` — how often it should run, if they have said.
- `{{artefact}}` — what each run should leave behind, by name and location.

## Instructions

1. **Separate the ONE recurring job from the story around it.** A routine does one job per fire.
   If `{{need}}` contains two jobs on different clocks, say so and propose two routines — a
   routine that does two things does the second one badly.
2. **Settle the open points WITH the person before creating anything,** and ask them as CHOICES,
   not as prose: what each run PRODUCES and where it lands; what one finished run leaves behind,
   in their words; whether it ever finishes for good or runs indefinitely; anything a run must
   NEVER do; which workflow pattern it is built on; and every setup question its pattern asks
   that `{{need}}` does not already answer. The test is whether you could QUOTE their answer for
   each. If you would be inferring one, it is still open — a guessed draft runs for months on
   your assumption.
3. **Ask the access questions explicitly,** because they are the ones a run cannot work around
   later: which folders it may read and write, which mailbox or account, whether it may send
   anything outward, whether it needs a shell or a browser, which machine. Name the least that
   makes the job possible.
4. **Offer a cadence with a reason.** Tie it to when the input actually changes — a daily inbox,
   a weekly report, a monthly bill — rather than to a round number.
5. **Create it with the create-routine capability, which is a TWO-STEP flow:** the first call
   stores a draft and returns a preview; put the remaining open points to the person, or the
   go-ahead as a blocking question; then call it again with the SAME fields to materialize. A
   changed field updates the draft rather than making a second routine.
6. **Carry their words, not your paraphrase,** into what one finished run leaves behind and what
   it must never do. Those lines are what every future run answers for, and a paraphrase quietly
   changes the job.
7. **Tell them it exists and where to accept it:** its own page shows what is specific to it as
   changes awaiting their accept, and a routine whose settings were never accepted is a routine
   that does not run as they intended.
8. **Say what the first run will do and when it fires.** If the first fire is days away and the
   work is already overdue, offer to run it once now.

## Notes

- A routine is not a conversation: it gets no answers mid-run unless it asks, so anything a run
  will need to be told must be in its instruction or its settings.
- Where the work is really one-off, say so rather than scheduling it — a routine that fires
  weekly on a job already done is noise that teaches the person to ignore its output.
- A routine that exists to watch something should say clearly what "nothing new" looks like, or
  every quiet run reads as a failure.
