---
tags: [conduct, rules, authoring]
requires:
  actions: [write_rule]
---
# permission: rule authoring — write the general rules every routine follows

Unlocks `write_rule`: author a new general rule, or revise an existing one, in the shared
library. `content` (the complete rule markdown) creates; `anchor` + `replacement` revises in
place — read the current text with `read_rule` first and copy the anchor verbatim. Whether a
change needs approval is this routine's rule_confirm level (user-set; a required approval files
a blocking question naming every routine the change would reach, so batch other work while it
waits). The library linter gates the write: a rule needs a `# rule: <name> — <summary>` heading,
at least three tags, and no capabilities in its frontmatter.

Write for every holder, not for the case in front of you. A rule states a PRINCIPLE and stops —
it names no util, no routine, no file, no threshold it cannot justify; mechanism belongs in a
recipe or a conduct doc like this one. Revise only when runs show the current wording caused the
problem, cite those runs, and keep every reading that was already working: the change lands on
every routine holding the rule at its next run, so a careless clause is a system-wide
regression. Prefer the smallest revision the evidence supports over a rewrite.

There is no delete. Removing a rule silently un-binds every holder with nothing to catch it, so
a rule you believe should go is a `report` or a deferred `ask_user` naming it and saying why —
the user removes it on the Library tab. Which rules bind which routine is config, and stays
theirs in both directions: this permission is about the TEXT.
