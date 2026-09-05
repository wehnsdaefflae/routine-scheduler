---
tags:
- files
- machines
- execution
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
  - scripts
  - shell
  - remote-machines
  rules:
  - ask-policy
  - decision-record
  - web-research
  - evidence-discipline
  - problem-routing
  - error-recovery
  - independent-verification
  - failure-visibility
  - git-checkpoint
  - root-cause-fix
  - change-restraint
  - decision-commitment
  capabilities:
    actions:
    - memory_read
    - memory_write
    - revise_util
    - write_util
    - detach
    - script
    - shell
    utils:
    - remote
    util_tags: []
    confirm: always
    rule_confirm: always
    remind_confirm: always
    reminders: local
    runs: last
    workflows: generate
---
# template: operator — acts on files, folders and machines you own

Pick this when the routine changes real things: moves files, runs a job on another box,
reorganises a folder you care about. It gets the shell escape hatch, its own persistent helper
scripts, and the SSH channel — plus the rules that make destructive work reversible: commit a
checkpoint before touching a repo, fix the cause rather than the symptom, make the smallest
change that does the job, and stop re-deciding once you have chosen.

Two things stay yours: the write ROOTS it may touch, and which MACHINES it may reach. Neither is
in the template, because both name paths and hosts specific to this instance.
