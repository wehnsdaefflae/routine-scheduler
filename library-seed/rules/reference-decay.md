---
effect:
  with: settles every statement naming a thing it deletes, and verifies a named mechanism still exists before relying on it
  without: leaves instructions confidently describing endpoints, helpers and files that are gone, which still read as correct and still get followed
  when: the routine writes or follows instructions that name concrete things — an endpoint, a helper, a file, a control, a host
tags: [maintenance, instructions, staleness]
---
# rule: reference decay — prose keeps naming what has been deleted

Instructions name things: an endpoint, a helper, a file, a button, a tool, a host. Those things
are changed and deleted by people and runs that never read the instructions naming them. So the
prose goes on describing an interface that no longer exists, and it reads exactly as it did when
it was true — confident, specific, and wrong. Nothing in a document can notice this about itself,
which is why it needs a rule and not more care.

The damage is not that a reader is confused. It is that the instruction still *works* as an
instruction: a run follows it, the step fails or silently does nothing, and every check written
around the old world still passes. The gap can stay open for weeks while each side looks healthy
on its own.

- **When you delete a thing, hunt what named it.** Deleting is only half the change. Search the
  prose for the name you just removed — every statement of it, not the one you happened to be
  looking at — and settle each one in the same act. A deletion that leaves its references
  standing has not finished.

- **A name you did not verify is a claim, not a fact.** Before you rely on an instruction that
  names a concrete mechanism, confirm the mechanism is still there. That check is cheap where a
  wrong answer is expensive — an irreversible step, a delivery, an authorization.

- **Warning someone off a thing keeps the thing alive.** "Do not use X" is still an instruction
  naming X, and it ages the same way. Once X is gone the warning is a puzzle: the reader either
  goes looking for it or concludes the document is stale in ways they cannot bound, and that
  doubt spreads to the paragraphs that are still correct. Prefer stating the surviving principle
  over cataloguing what not to touch.

- **A rule whose mechanism died is worse than a missing rule.** If the only way to do what an
  instruction demands has stopped working, the instruction does not quietly lapse — each reader
  privately invents a substitute, and none of them are recorded. Say what is no longer possible,
  say what to do instead, and say what the substitute cannot see.

- **Dormant prose decays unobserved.** A change updates what the current path reads. Whatever is
  off that path — a branch for a state you are not in, a first-run procedure, a fallback — keeps
  the old world intact and is not exercised by anything. Dormant and wrong is not harmless: it is
  one state change away from being followed. When a design is abandoned, search the whole
  document set for the abandoned mechanism, not just the pages in use.

- **One fact, one place.** The same fact restated in two documents will eventually disagree, and
  the disagreement lands on whichever a reader opens first. Keep the statement where it belongs
  and refer to it from elsewhere by name, rather than copying it.
