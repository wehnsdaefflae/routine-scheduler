---
tags: [routing, delegation, maintenance]
requires:
  actions: [hand_off]
---
# permission: work orders — hand work to the routine that owns it

Unlocks the `hand_off` action: address a durable **work order** to another routine, which
reads it on its **next scheduled run**. Give `target` (the routine's slug), `title` (one
line), and `detail` — the artefact, what is wrong, the evidence (a run id, a `path:line`, the
error text), and what *done* looks like. It is filed under a `W<n>` id and delivered into that
routine's inbox.

Nothing is started and nobody is interrupted.

Write it to stand alone: the target reads it with none of your context, possibly days later.
Send one where the ARTEFACT that must change belongs to the target — a recipe that misuses a
working tool is the recipe owner's, a tool no reasonable recipe could use correctly is the
tool owner's.

Receiving is half of it. Work orders arrive in your prompt as their own section: treat them as
a first-class input, not noise — something else in the system found a problem inside your remit
and did the diagnosis for you. Close each one with a `hand_off` back to the sender carrying
`answers: "<the W id>"`, saying what you did or why you will not.

Your own slug is not a valid target — a note to yourself belongs in a `note` or a memory note.
