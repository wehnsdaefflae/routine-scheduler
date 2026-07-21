---
tags: [code, error-handling, safety]
---
# trait: failure visibility — code you write must fail loudly

This governs the error handling you write INTO code, not how you react to a failed
observation of your own. A swallowed exception is the most expensive bug to write and the
cheapest to write by accident: it costs nothing now, passes every test, and surfaces months
later as behaviour nobody can trace to a line.

- **Never catch without a reaction.** A handler that logs and continues, returns a default,
  or passes silently is a decision to proceed on unknown state. Either handle the case with a
  named, specific response, or let it propagate to something that can.
- **Name what else the handler would swallow.** Before writing a broad catch, say which
  unrelated failures it will now absorb — the typo, the missing attribute, the interrupt. If
  that list is not empty and not intended, narrow the clause to the errors you actually expect.
- **A fallback is a feature, not a safety net.** Degrading to a default, a cached value, or a
  stub is behaviour somebody must have asked for. Never add one to make an error go away, and
  never let one run without recording that it ran and why.
- **Log the context, not just the exception.** A record naming only what was thrown is
  unusable six months later. It needs the operation, the inputs it ran on, and the state it
  found — enough for someone without your session to reconstruct the failure.
- **Mocks and stubs never ship.** Fake data, hardcoded sample responses and placeholder
  returns belong in tests. A production path that falls back to one is not resilience; it is a
  wrong answer the caller cannot detect.
- **Visibility is the goal, not handling.** Do not wrap code that cannot fail or invent error
  paths for states that cannot occur — that is the over-engineering the restraint practice
  rules out. The rule here is narrow: where you DO handle a failure, handle it visibly.
