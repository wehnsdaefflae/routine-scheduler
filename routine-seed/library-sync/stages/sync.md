# sync — commit, bring the remote in, push

Commit the repo's working tree, integrate the remote's history, and push to the configured
GitHub remote. Nothing else in this routine leaves the machine, so until the push reports
success the instance has no off-box copy of anything that changed this run.

- **Read the result rather than assuming it.** "Committed" is not "pushed". A run that commits
  and fails to push has done nothing useful, and it is the failure most likely to look fine.
- **Never resolve a conflict.** Diverged history means something else wrote to this repo — a
  second instance, a clone, a hand-edit. Reconciling it blind can drop someone's work
  irrecoverably. Record what the operation said and report it; a human decides.
- **Never force anything.** No forced push, no discarding the remote, no resetting to make the
  push go through. If that seems like the answer, the answer is a report.
- **A refused credential is not yours to fix either.** An authentication failure means the
  instance's stored credential expired or lost its scope — report it, naming the remote and the
  exact error, and finish honestly. Do not attempt to re-authenticate.
- **A push that has been failing for a while is the more important finding.** Check the record:
  if previous runs also failed to push, the backlog is the story, not this run's attempt. Say
  how long it has been failing and how many commits have piled up locally.

Write `{"phase": "record"}` into `state/phase.json` and advance to **record**.
