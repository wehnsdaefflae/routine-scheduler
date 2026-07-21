---
tags: [code, testing, quality]
---
# trait: test design — a test earns its place by failing

A test that cannot fail is worse than no test: it costs runtime, it has to be maintained, and
it reports green while the thing it names is broken. The easiest test to write is one that
asserts the implementation you just wrote — which is exactly why this needs a rule.

- **Name the regression before you write the test.** State in one line the specific breakage
  this test would catch. If you cannot name a plausible future change that turns it red, do
  not write it.
- **Assert behaviour, not internals.** Check what a caller can observe: return values, raised
  errors, written files, recorded state. A test that reaches into private attributes or pins
  the order of internal calls breaks on every refactor and catches nothing.
- **The uncovered paths are the error paths.** New happy-path code arrives with happy-path
  tests. Spend coverage where it is missing — the failure branch, the empty input, the
  boundary value, the invalid argument that must be rejected.
- **Watch it fail once.** Before accepting a test, make it fail: run it against the unfixed
  bug, or break the assertion on purpose, and report what it said. A test only ever observed
  passing has not been observed at all.
- **The name is the failure report.** Nobody reading a red run opens the file first. A name
  like `test_rejects_empty_slug` says what broke; `test_scaffold_2` sends them looking.
- **Read the neighbours first.** Open the existing test module before adding to it. A second
  test asserting what the first already asserts is maintenance with no return, and trivial
  accessors or paths already covered end-to-end need nothing at all.
