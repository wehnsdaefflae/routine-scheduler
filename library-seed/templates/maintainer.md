---
tags:
- meta
- library
- self-improvement
config:
  permissions:
  - global-utils
  - memory
  - util-authoring
  - util-revision
  - run-history
  - workflow-generation
  - background-tasks
  - scripts
  - shell
  - util-removal
  - rule-authoring
  rules:
  - ask-policy
  - decision-record
  - web-research
  - evidence-discipline
  - problem-routing
  - error-recovery
  - independent-verification
  - failure-visibility
  - root-cause-fix
  - change-restraint
  - review-recall
  - test-design
  - unexamined-is-not-clean
  - teaching-insights
  - suggestion-discipline
  - git-checkpoint
  capabilities:
    actions:
    - memory_read
    - memory_write
    - revise_util
    - write_util
    - detach
    - script
    - remove_util
    - write_rule
    utils:
    - shell
    util_tags: []
    confirm: always
    rule_confirm: always
    runs: all
    workflows: generate
---
# template: maintainer — maintains this instance — its library, its routines, itself

Pick this only for routines whose subject is the system itself: the improver, the auditor,
the library reviewer. It is the widest template by a distance — it can retire a util every other
routine calls, and reword a rule every routine follows — so it
carries the rules that make that survivable: check work from outside the context that produced
it, find first and filter second, name what you did not cover, and never ship a suggestion you
have not implemented or justified.

It reads ALL previous runs, not just the last, because a maintainer's evidence is the history.
Rewriting its OWN recipe is deliberately NOT here: `recipe-authoring` is a per-routine
decision, because a run that can reword its own task can change what it is for. Add it
on the routine that needs it. `routine.yaml` stays sealed either way.
