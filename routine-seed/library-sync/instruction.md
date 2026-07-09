# Instruction

Keep the scheduler's three library repositories in sync with their remotes. Run the git-sync
util on each of these paths (it commits, pulls, and pushes):

- ~/.local/share/workflow-library
- ~/.local/share/routine-fragments
- ~/.local/share/global-utils

Report per repo whether it pulled updates, pushed, was already up to date, or hit a conflict.
Do not attempt to resolve conflicts — just report them.
