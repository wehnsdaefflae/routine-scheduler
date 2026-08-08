# sync — commit, bring the remote in, push

Commit the repo's working tree, integrate the remote's history, and push. Nothing else in this
routine leaves the machine, so until the push reports success the instance has no off-box copy
of anything that changed this run.

- **Read the result rather than assuming it.** "Committed" is not "pushed". A run that commits
  and fails to push has done nothing useful, and it is the failure most likely to look fine.
- **A push that has been failing for a while is the bigger finding.** Check the record: if
  previous runs also failed to push, the backlog is the story, not this run's attempt. Say how
  long it has been failing and how many commits have piled up locally.
- **Never force anything, ever.** No forced push, no discarding the remote, no resetting to
  make a push go through. Those destroy history that exists nowhere else. If forcing looks like
  the answer, the answer is a report.
- **An authentication failure is not yours to fix.** Report it naming the remote and the exact
  error, and finish honestly. Do not attempt to re-authenticate.

## When history has diverged

Someone else wrote to this repo — another clone, a web edit, a hand-edit. Your sync tool can
hold a conflicted rebase OPEN instead of discarding it, and tell you which files conflict and
how; ask the catalog how to request that. Held, the conflicted files sit in the working tree
with both sides marked, so you can read them, write a resolution, and let the rebase finish.
Before it holds anything it pins the remote's pre-rebase tip, so no resolution you make can put
a remote commit permanently out of reach.

**Resolve only what the conflict itself answers.** Each conflicted file comes back with a kind,
and the kind decides:

- **both sides changed the same file** — resolve it. Read both versions, keep what each side was
  trying to do, and write the result. This is ordinary editing and you are good at it.
- **one side deleted a file, the other changed it** — STOP. Neither answer is in the diff:
  keeping the change resurrects something somebody deliberately removed, deleting it throws away
  work somebody deliberately did. Abandon the rebase and report it.
- **both sides created the same path** — STOP, for the same reason. Two people solved one
  problem twice and only they know which solution stands.

Two more rules on top of the kinds:

- **The exported instance state is yours by construction.** Anything under the exported
  routines and config trees is a MIRROR of this instance, written by you each run — a remote
  edit there is somebody editing the mirror instead of the thing. Take the local side and say
  in the LEDGER that you did.
- **Verify before you finish.** After resolving, the library must still pass its own
  conformance checks. If it does not, your merge broke something: abandon the rebase, restore
  the pre-pull state, and report — do not push a library that no longer lints. A resolution you
  cannot verify is a guess with a commit hash.

If anything at all is unclear, abandon and report. An unpushed run is recoverable on the next
one; a bad merge into the only off-box copy is not.

Write `{"phase": "record"}` into `state/phase.json` and advance to **record**.
