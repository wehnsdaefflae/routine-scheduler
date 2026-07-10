# Instruction

Publish everything this instance has acquired to its one library repository and keep that
repository in sync with its GitHub remote. Two util calls do all the work:

1. `instance-export` — stage every routine's working tree (minus transient run state) and the
   sanitized server config (token / api_key values REDACTED) into the library repo working tree
   at `~/.local/share/routine-scheduler-libraries`, alongside the workflows/, fragments/, and
   utils/ it already holds.
2. `git-sync` — commit, pull (rebase), and push that repo, so workflows, fragments, utils,
   routines, and config all land in the one remote.

Report what was exported and whether the repo pulled, pushed, was already up to date, or hit a
conflict. Do not attempt to resolve conflicts — just report them.
