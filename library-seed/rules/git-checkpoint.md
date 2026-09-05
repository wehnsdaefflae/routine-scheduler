---
effect:
  with: commits an undo point before editing a project repo, and names it in the reply
  without: edits in place with no undo point — the engine does not version your project dirs
  when: it has write access to a git project whose history you care about
assists:
  - id: before-the-first-repo-edit
    moment: pre-action
    predicate: uncheckpointed-repo-write
    payload: hold
    line: >-
      No undo point exists for this repo — the engine versions its own working directory, not
      yours. Commit a checkpoint naming what you are about to do before the first edit, so
      this run can be undone in one step if it goes wrong.
tags: [git, safety, undo]
---
# rule: git checkpoint — undo points for project repos you edit

The engine does NOT version the external project directories under your fs write roots
(and a conversation's own dir is not versioned at all) — so an edit with no checkpoint has
no undo. You own that safety line. Your CAPABILITIES catalog carries a tool that commits a repo and
one that restores it; note in your memory which ones you used, so a later run needn't look
again.

- **Before the first edit of a reply** to a git-tracked project dir, and before any risky
  multi-file change: make a LOCAL commit (no push) messaged
  `checkpoint: <what you are about to do>` — your undo point. Skip it only when the working
  tree is already clean (the checkpoint commit will simply be empty — that is fine and cheap).
- **After a coherent piece of work**, checkpoint again with a message that says what
  changed and why — that commit is the reviewable unit.
- **Name checkpoint commits in your reply**, so the user knows the undo point exists.
- **To discard a botched attempt**: restore the repo (or just the files you touched) to HEAD —
  then say so and try differently.
- **Never push** unless the user explicitly asked for it — a checkpoint is local by default,
  so keep whatever flag holds the push back.
- If a directory you are editing is NOT a git repo, say so in the reply the first time you
  touch it — no checkpoints are possible there, and the user should know their edits are
  unprotected.
