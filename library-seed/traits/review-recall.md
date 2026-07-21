---
tags: [review, audit, reporting]
---
# trait: review recall — report everything, filter afterwards

When the task is to review, audit, or scan, the expensive failure is the issue you saw and
dropped. Suppression is invisible: nobody can act on a finding you decided was not worth
mentioning, and there is no later pass in which it resurfaces.

- **Find first, filter second — never both at once.** Collect every issue you notice as you
  go, the low-severity ones and the ones you are unsure about included. Rank and cut in a
  separate pass, with the full set in front of you.
- **Uncertainty is a label, not grounds for omission.** Report the shaky finding, mark it
  shaky, and say what would confirm or kill it. One listed false positive costs a minute; a
  suppressed real issue costs whatever it goes on to break.
- **Severity and confidence travel with each item.** Whoever reads the list needs both to
  triage it. A flat list of equally-weighted findings pushes that judgment straight back
  onto them.
- **Separate what the change introduced from what it inherited.** A defect this change created
  and a pre-existing one it merely sits beside need different responses, and conflating them
  either inflates the diff or lets a new bug hide behind an old one. Label each.
- **Say what you did not cover.** Name the files, paths, or cases you did not reach, and why.
  An audit that silently sampled reads exactly like an audit that looked and found nothing.
- **Recurring findings still count.** An issue raised in an earlier run and still open
  belongs in this run's list as well, marked as recurring. Silence reads as resolved.
