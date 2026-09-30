---
effect:
  with: secures a local undo point before it edits a project repo, then names it in its summary
  without: edits in place with no undo point — the engine versions no project repo it was granted
  when: it has write access to a git project whose history you care about
assists:
  - id: before-the-first-repo-edit
    moment: pre-action
    predicate: uncheckpointed-repo-write
    payload: hold
    line: >-
      This edit lands in a git repo the engine does not version. If its tree has uncommitted
      changes, commit a local checkpoint naming what you are about to do; if it is clean, its
      current commit is the undo point — note it. Then emit this edit again.
tags: [git, safety, undo]
---
# rule: git-checkpoint — an undo point before you edit a project repo

The engine commits a routine's own directory at run end. It versions no project repo you were
granted and no conversation directory, so an edit there has no undo unless you make one. Use
the version-control capability in your catalog; note in your memory which one worked.

- **Secure an undo point before the first edit.** If the tree has uncommitted changes, make a
  local commit named "checkpoint: <what you are about to do>". If it is clean, its current
  commit already is the undo point — note it and go ahead; an empty commit adds nothing.
- **Checkpoint again before any risky multi-file change.**
- **Commit each coherent piece of work** with a message saying what changed and why — that
  commit is the reviewable unit.
- **Name your undo points in your summary,** so the user knows they exist.
- **Discard a botched attempt by restoring** the repo, or only the files you touched, to the
  undo point; say so, then try differently.
- **A checkpoint stays local.** Push only when your instruction or the user asks for it.
- **Say when there is no repo.** If a directory you edit is not under version control, say so
  the first time you touch it: the user should know those edits are unprotected.
