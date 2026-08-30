---
tags:
- starter
- minimal
- sandboxed
config:
  permissions:
  - global-utils
  - memory
  - util-authoring
  - util-revision
  rules:
  - ask-policy
  - decision-record
  - web-research
  - evidence-discipline
  - problem-routing
  - error-recovery
  capabilities:
    actions:
    - memory_read
    - memory_write
    - revise_util
    - write_util
    utils: []
    util_tags: []
    confirm: always
    rule_confirm: always
    runs: last
    workflows: catalog
---
# template: basic — thinks, reads and reports — nothing outside its own directory

Pick this when the routine's whole job is to look something up, think about it and say
what it found. It can read the web, keep a memory notebook, write its own utils and read its
last run — and nothing else: no files outside its own directory, no machines, no way to reach
a person except the run summary you read in the console.

It is also the honest starting point for a routine you are still designing. Adding a capability
later is one click; taking one back after a run has used it is a conversation.
