---
tags: [git, safety, undo]
---
# trait: git checkpoint — undo points for project repos you edit

The engine does NOT version the external project directories under your fs write roots
(and a conversation's own dir is not versioned at all) — so an edit with no checkpoint has
no undo. You own that safety line, via the git utils (`git-sync`, `git-restore`):

- **Before the first edit of a reply** to a git-tracked project dir, and before any risky
  multi-file change: `util` name `git-sync`, args
  `["<repo>", "-m", "checkpoint: <what you are about to do>", "--no-push", "--no-pull"]` —
  a local commit, your undo point. Skip it only when the working tree is already clean
  (the checkpoint commit will simply be empty — that is fine and cheap).
- **After a coherent piece of work**, checkpoint again with a message that says what
  changed and why — that commit is the reviewable unit.
- **Name checkpoint commits in your reply**, so the user knows the undo point exists.
- **To discard a botched attempt**: `util` name `git-restore`, args `["<repo>"]` (or with
  specific files) restores the tree to HEAD — then say so and try differently.
- **Never push** unless the user explicitly asked for it (`git-sync` without `--no-push`
  pushes; keep the flag on for checkpoints).
- If a directory you are editing is NOT a git repo, say so in the reply the first time you
  touch it — no checkpoints are possible there, and the user should know their edits are
  unprotected.
