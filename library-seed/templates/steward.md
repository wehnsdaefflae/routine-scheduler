---
tags:
- project
- publishing
- long-running
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
  - outbound-mail
  - scripts
  rules:
  - ask-policy
  - decision-record
  - web-research
  - evidence-discipline
  - problem-routing
  - error-recovery
  - independent-verification
  - failure-visibility
  - ai-writing-tells
  - email-thread-continuation
  - change-restraint
  - teaching-insights
  - engagement-accountability
  - status-page
  - interface-design
  - interface-copy
  - intent-inference
  - feedback-implementation-gate
  capabilities:
    actions:
    - memory_read
    - memory_write
    - revise_util
    - write_util
    - detach
    - script
    utils:
    - fau-mail-send
    util_tags:
    - smtp
    confirm: always
    rule_confirm: always
    remind_confirm: always
    reminders: local
    runs: last
    workflows: generate
---
# template: steward — tends one project over weeks and publishes a page you can check

Pick this for work that runs for months and needs somewhere you can look between runs. On
top of the correspondent's channel it carries the publishing rules: one shared page shell rather
than a bespoke one per routine, interface copy that names things by what you control, and the
discipline of acting on feedback you were given instead of collecting it.

The shell is not here: it is an escape hatch, not a publishing tool, and a template is
the wrong way for it to arrive. It needs one thing this template cannot supply: a WRITE ROOT for the directory it publishes into.
That path is yours, so the routine page asks for it — the setup check will say so until you give
it one.
