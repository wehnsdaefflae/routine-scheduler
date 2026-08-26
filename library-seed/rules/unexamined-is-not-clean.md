---
tags: [verification, reporting, quality]
---
# rule: unexamined is not clean — a check reports on what it read, never on what it skipped

A check reports on the input it was handed. It cannot report on input it never received. So a
selector that quietly narrows what reaches the check produces the same output as a clean
result, and the check is *correct about its input* the whole time it is wrong about the world.
Reading the output more carefully never finds this, which is why it needs a rule rather than
more diligence.

- **Report the denominator, not just the finding.** "No problems found" is half a result. The
  other half is what was examined: how many files, which paths, what fraction of the whole. A
  result with no denominator cannot be distinguished from a search that ran over nothing.
- **Every exclusion is declared, with a reason.** Anything the check drops on purpose gets
  named and justified where the code drops it. Anything dropped that no declared reason covers
  is a **third state**, neither pass nor fail, and it has to be surfaced. Folding it into pass
  is the whole failure.
- **Open a sample of what was skipped.** You believe the excluded part is boilerplate, or
  generated, or out of scope. Look at ten lines of it before you believe that. This is where
  the surprises live, because nobody has ever read them.
- **Compare the scope you claim against the scope you coded.** A docstring saying "every X" and
  a loop over four hardcoded paths are two things written separately, and they drift toward
  making the work look finished. Read them against each other as a pair.
- **Prefer a denominator you did not define.** If the check and the measure of its coverage
  come from the same code, coverage is 100% by construction. Count against something
  independent: the real renderer, the package index, the directory listing, the upstream API.
- **A total hides a zero.** An aggregate over many items lets a healthy average mask one item
  at nothing. When the question is "did anything get missed", count items at zero, not the mean.
