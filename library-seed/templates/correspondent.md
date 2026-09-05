---
tags:
- outbound
- writing
- contact
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
# template: correspondent — drafts and sends things to people in your name

Pick this when the routine writes something a person will read as if you wrote it. It gets
the mail channel plus the rules that make that safe to delegate: sound like yourself rather than
like a model, continue a thread instead of starting a new one, keep track of what you already
sent, and never widen a message beyond what was asked.

The channel is email by default because it is the one that needs no linked session. A messenger
(Signal, Telegram, WhatsApp, Discord) is a per-routine addition — each needs its own permission
AND a grant for the session store that holds its login.
