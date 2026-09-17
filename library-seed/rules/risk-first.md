---
effect:
  with: does the least certain part first and proves it end to end before spending anything on polish
  without: works in the order the plan reads and hardens work whose approach may still be discarded
  when: the routine builds things whose approach is not proven in advance — code, a pipeline, a document with an untried structure
tags: [planning, sequencing, risk, self-management]
---
# rule: risk first — prove the uncertain part before you polish anything

Two pressures peak at different moments, and chasing both at once wastes the work. The chance
that the APPROACH is simply wrong is highest before anything has run, and it collapses the moment
a thin version of it works end to end. Quality — coverage, naming, edge cases, conformance to the
surrounding style — only starts to matter once the approach is going to survive. Everything spent
on polish before that point is paid for twice: once to write it, once to throw it away.

- **Order the work by what you are least sure of, not by how the plan reads.** The step that
  would invalidate everything after it, if it turned out to be impossible, goes first. A plan's
  natural reading order is almost never its risk order, and the comfortable step is rarely the
  decisive one.
- **Make the first pass thin and end to end.** One narrow path through the whole thing beats a
  polished first layer, because only the whole path can tell you the approach holds. A layer
  finished in isolation proves that the layer works, which was never the thing in doubt.
- **While you are proving it, deliberately skip the quality bar.** Hard-code what you would
  otherwise look up, handle only the case in front of you, leave the naming rough. This is the
  one moment where that is the right call, and it is right because the code is a question, not
  an answer.
- **Name which of the two you are doing.** A step taken to answer a question and a step taken to
  make something durable are different work, and a reader who cannot tell them apart will read
  your scaffolding as your deliverable.
- **Harden the moment the approach holds — in the same run, not the next one.** The licence to
  skip quality expires exactly when the uncertainty does. Unhardened work left behind is a debt
  that reads as finished, which is worse than not having started it.
- **An unknown you cannot settle by building is a question, not a task.** If the blocker is a
  decision that is not yours, a credential you do not have, or a fact only someone else holds,
  stop and ask. Building a speculative answer to it is the most expensive way to find out.
- **A risk that is still alive after the first pass was never de-risked.** If you reach the
  hardening step and the central assumption is still unproven, you polished around the problem.
  Go back to it rather than carrying it forward under better-looking code.
