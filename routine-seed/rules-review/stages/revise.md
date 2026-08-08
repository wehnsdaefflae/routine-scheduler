# revise — change the rule, or say why you did not

You hold rule authoring, so a finding you can defend becomes an edit, not a memo. Write it
with `write_rule`: `anchor` + `replacement` for a revision (copy the anchor verbatim from the
text you just read), `content` for a rule that does not exist yet. Your approval level decides
whether the user confirms; when it asks, the question already names every routine the change
reaches.

**Revising costs more than authoring, which is the opposite of how it feels.** A new rule binds
NOBODY until the user binds it — the held set is config and stays theirs — so authoring is the
cheap, reversible move. A revision lands on every current holder at its next run, with no step
where anyone opts in. Spend your caution accordingly.

Before you write:

- **Re-read the sentence you are replacing, in full context.** The anchor must be the text
  that actually caused the misreadings, not the nearest sentence to them.
- **Check the revision against every `followed` row.** If a reading that was working no longer
  survives your wording, you have traded a known failure for an unknown one. Narrow the change
  until both hold.
- **Keep the rule's shape.** A principle, stated and stopped. No util, no routine, no file, no
  threshold you cannot justify. If your fix needs a mechanism to make sense, the rule is the
  wrong home — the mechanism belongs in a recipe or a conduct doc, and that is a report to
  its owner instead.
- **Prefer one clause to a rewrite.** The wording you are touching has been read correctly many
  times. Adding a bullet is a real cost paid by every holder on every read.
- **One rule per edit.** If two rules contradict each other, change the less settled one and
  say in the LEDGER why the other stood.

Then record what you did, in the run's own words: the rule, the defect, the routines and run
ids the evidence came from, and the exact before/after. A revision nobody can trace back to
runs is indistinguishable from a preference.

**A rule that should not exist is NOT yours to delete** — deleting one silently un-binds every
holder and nothing catches it. Report it instead (`stages/route-elsewhere.md` for the shape),
naming the rule, the evidence that it is dead or harmful, and what its holders should hold
instead. The user removes it on the Library tab.

**When you decide not to change anything**, that is a result: record the rule, what you looked
at, and why the current wording survived. It is what stops the next pass re-opening it.

Then loop back to **gather-evidence** for the next rule in the queue, or advance to **record**
when the queue is empty.
