---
effect:
  with: writes new general rules and revises existing ones, for every routine that holds them
  without: reads every rule but changes none — a wording problem becomes a report
  when: curating shared conduct is the job — a revision changes how every holder behaves
tags: [conduct, rules, authoring]
requires:
  actions: [write_rule]
---
# permission: rule-authoring — write and revise the general rules

Revise only where runs show the wording caused the problem: cite them, change the smallest part
that fixes it, keep every reading that already works.
A rule needs an `effect:` block (`with`, `without`, `when` — the operator's toggle label), three
or more `tags:`, a body opening `# rule: <slug> — <summary>`, and no `requires:`.
A new rule binds nobody: which routines hold it is the operator's config.
