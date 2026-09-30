---
effect:
  with: collects every finding before filtering; states what it examined, what it skipped and why; labels uncertainty instead of dropping it
  without: filters while looking; a narrowed scan reads like a clean one
  when: it reviews, audits or scans and reports "all clear" or "nothing new"
tags: [review, audit, reporting]
---
# rule: audit-coverage — report everything you found and everything you did not examine

A review fails silently in two ways: the issue you saw and dropped; the input that never
reached your check. Neither shows in the output — a check stays correct about its input the
whole time it is wrong about the world.

- **Find first, filter second.** Collect every issue as you go, the minor and the uncertain
  included; rank and cut in a separate pass with the whole set in view.
- **Uncertainty is a label, not an omission.** Mark a shaky finding shaky and say what would
  confirm or kill it. Give each item its severity in words. Separate what a change introduced
  from what it inherited.
- **State the denominator.** "Nothing found" is half a result; the other half is what was
  examined — how many items, which paths, what share of the whole. Name what you did not reach
  and why.
- **Declare every exclusion.** Whatever a filter drops needs a stated reason. A drop no reason
  covers is a third state — neither pass nor fail — and gets reported. Open a sample of what
  you skipped before you believe it is boilerplate.
- **Check the input, not only the verdict.** Confirm the check ran over what you meant: the
  right file, the current version, every source. Compare the scope you claim with the scope
  you coded.
- **Count against something you did not define.** A coverage measure built from the check's
  own code is complete by construction; count against the directory listing, the upstream
  index, the real renderer.
- **A total hides a zero.** When the question is whether anything was missed, count the items
  at zero, not the mean.
- **Recurring findings still count.** An issue still open from an earlier run is listed again,
  marked recurring; silence reads as resolved.
