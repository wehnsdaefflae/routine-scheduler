---
tags:
- watch
- schedule
- radar
config:
  permissions:
  - reminders
  - global-utils
  - memory
  - util-authoring
  - util-revision
  - run-history
  - workflow-generation
  - background-tasks
  rules:
  - ask-policy
  - decision-record
  - web-research
  - evidence-discipline
  - problem-routing
  - error-recovery
  - independent-verification
  - failure-visibility
  - review-recall
  - unexamined-is-not-clean
  capabilities:
    actions:
    - memory_read
    - memory_write
    - revise_util
    - write_util
    - detach
    utils: []
    util_tags: []
    confirm: always
    rule_confirm: always
    remind_confirm: always
    reminders: local
    runs: last
    workflows: generate
---
# template: watcher — checks the world on a schedule and records what changed

Pick this for a routine whose value is the DIFFERENCE between runs: what appeared, what
went away, what moved. It reads its own history (so "changed since last time" means something),
can start a long fetch that outlives the reply, and carries the rules that keep a survey honest
— name what you did not cover, give every result its denominator, report a failure as a failure.

It cannot reach anyone. A watcher that needs to tell you something either writes it in the run
summary, or wants the `correspondent` template instead.
