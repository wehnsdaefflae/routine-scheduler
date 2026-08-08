# export — stage the instance into the repo's working tree

Mirror this instance's own state into `~/.local/share/routine-scheduler-libraries`:

- every routine under `~/routines` into `routines/<slug>/`, minus transient run state — the
  recipe, config, state files and LEDGER travel; run transcripts, inboxes and queued questions
  do not, because they are per-instance history and would grow the repo without ever being read
  back.
- the server config into `config/config.yaml`, with **every credential value replaced by a
  redaction marker**. This is the load-bearing part of the whole routine: the repo has a public
  remote, and a token written there is a token published. It must be redacted by parsing the
  configuration as structured data and replacing values by key — never by pattern-matching the
  text, which silently misses whatever it was not written to expect.

The operation is rsync-like and idempotent: unchanged files are left alone, and files that have
disappeared from the instance are pruned from the tree. That is what makes a re-run safe and a
diff meaningful.

- **Do not re-run it to be sure.** It is idempotent, so a second call proves nothing and the
  first call's result already says what changed.
- **Read what it reported** — how many routines were staged, how many files changed, how many
  were pruned, whether the config was redacted. Those counts are the evidence the record stage
  needs; "it ran" is not.
- **A large prune count is a finding, not a step.** Files vanishing from the tree in bulk means
  routines were archived or deleted since the last run — plausible, but say so explicitly, and
  if it does not match anything you can account for, STOP and report rather than committing a
  deletion you cannot explain. The repo is the only off-box copy.
- **If the export fails, do not repair it.** Record the error verbatim and go to **record**
  without syncing: pushing a half-staged tree is worse than not pushing.

Write `{"phase": "sync"}` into `state/phase.json` and advance to **sync** — or to **record** if
the export failed.
