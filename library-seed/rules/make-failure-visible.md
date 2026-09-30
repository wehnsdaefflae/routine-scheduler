---
effect:
  with: writes code that fails loudly and tests that fail on the regression they name — each watched failing once; no verification step swallows an exit code
  without: may ship catch-alls, silent fallbacks, `|| true` or tests that stay green while the thing breaks
  when: it writes scripts, utils, pipelines or tests later runs rely on
tags: [code, testing, error-handling]
---
# rule: make-failure-visible — code and tests you write must fail loudly

A swallowed failure costs nothing when written, passes every check and surfaces weeks later as
behaviour nobody can trace. A test that cannot fail reports green while the thing it names is
broken. Both are the easiest things to write by accident.

- **Never catch without a reaction.** Handle the case with a named, specific response or let it
  propagate. Before writing a broad handler, name the unrelated failures it would absorb, then
  narrow it to the errors you expect.
- **A fallback is a feature, not a safety net.** Never add one to make an error go away; when
  one runs, record that it ran and why. Mocks, stubs and sample data never reach a production
  path.
- **No step swallows an exit code.** A chain of checks, evaluations or builds stops at the
  first failure and says which. An empty result from a step that should produce output is a
  failure until shown otherwise.
- **Log the context.** The operation, its inputs and the state it found — enough for someone
  without your session to reconstruct the failure.
- **Name the regression before you write the test.** If you cannot name a plausible change
  that turns it red, do not write it.
- **Watch it fail once.** Run it against the unfixed defect or break it on purpose; report what
  it said. A test only ever seen passing has not been seen.
- **Assert behaviour, not internals** — return values, raised errors, written files. Spend
  coverage on the error paths, the empty input, the boundary. Name each test for the failure it
  reports; read the neighbouring tests before adding one.
- **Visibility, not ceremony.** Do not wrap code that cannot fail or invent paths for states
  that cannot occur.
