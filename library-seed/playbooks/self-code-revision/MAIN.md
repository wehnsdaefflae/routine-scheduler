---
slug: self-code-revision
title: Revise code in a repo you own, gated green and merged
when: You want a change made in one of your own repos — a defect fixed, a surface added, cruft removed — built, tested and landed rather than described.
tags:
- code
- repo
- testing
- release
axis: the REPO and the change asked for vary; what stays fixed is the loop — read the map before the source, edit in a disposable branch, gate before committing, land through the repo's own merge path, and leave no branch behind
updated: '2026-11-25'
setup:
  permissions:
  - shell
  rules:
  - fix-the-cause
  - git-checkpoint
  - change-scope
  - make-failure-visible
  workdir: '{{repo}}'
  fs_write_roots:
  - '{{repo}}'
  fs_read_roots: []
  connections: {}
  note: >-
    The shell permission is what makes this playbook work at all — a build, a test run and a
    merge are shell work. `{{repo}}` must be BOTH the workdir and a write root: the workdir is
    the project directory and the first write root, and the file actions resolve reads against
    the read roots, so a repo that is write-only is unreadable to them.
---

## Parameters

- `{{repo}}` — absolute path of the repo to change. Default: this scheduler's own checkout.
- `{{change}}` — what should be different when this is done, stated as an outcome, not a diff.
- `{{gate}}` — how this repo proves a change is safe (its test command, its release gate, its
  linter). Default: whatever its contributor documentation names.
- `{{landing}}` — how a finished change reaches the main line: a merge tool, a pull request, a
  direct push. Default: the repo's own documented path.

## Instructions

1. **Read the brief back as an outcome.** Say what will be true when this is done, and what you
   will NOT touch. If `{{change}}` could mean two different things, ask ONE question before
   spending effort — a wrong guess here costs the whole build.
2. **Look things up before opening files.** Most repos of any size carry a derived map or index;
   this scheduler's own lives under `.codemap/` and your catalog has a capability that
   regenerates it in seconds. Find the symbol, the route or the module there, then open the
   source only at the lines it names. Reading whole files to find one function is how a change
   runs out of context before it is written.
3. **Establish the baseline BEFORE editing.** Run `{{gate}}` on the unchanged tree. A red you
   did not cause is a different job: say so and stop rather than fixing someone else's defect
   inside your change.
4. **Work in a disposable branch, never on the main line.** Create a branch (and, for a repo
   whose main checkout is live, a separate working tree) for this one change, and plan to delete
   both in this same session. A long-lived branch is a merge conflict with interest.
5. **Make the smallest change that achieves `{{change}}`.** Follow the conventions already in
   the file you are editing: its error handling, its naming, its test style. A change that reads
   as foreign is a change someone will revert.
6. **Where you are fixing a defect, pin it with a test that FAILS first.** Write the test, watch
   it red, then fix. A fix with no red-first test is a fix nobody can tell from a coincidence —
   and if the repo's own prose claims an invariant nothing checks, the test IS the fix.
7. **Gate the tree you changed, not the one you did not.** Run `{{gate}}` again, in the working
   tree holding your edits. Watch for the trap of an editable install: a test runner invoked
   inside a secondary tree may import the MAIN checkout, and then it is testing the wrong bytes.
8. **A red gate is restored, not argued with.** Put the tree back as your very next step, keep
   the candidate aside, confirm the restore is clean, and only then try again. Leaving a red
   tree while you think blocks everyone else who shares it.
9. **Commit by explicit path.** Name the files you changed; never commit the whole index. The
   message says WHY, what it cost, and what the alternative would have broken — the diff
   already says what.
10. **Land it through `{{landing}}`** and then prove it: the change is on the main line, the main
    line is in sync with its remote, and the version or changelog the repo keeps says so.
11. **Tear down.** Delete your branch on BOTH sides and remove the working tree, then list what
    the remote carries and confirm it is gone. A push hook often publishes a branch the moment
    you commit on it, so a branch you never pushed can still be there.
12. **Report the outcome, with its shortfall.** What landed, where, proven by what; and anything
    you found and did not fix, named rather than left for someone to discover.

## Notes

- Deleting a fallback, a legacy branch or a compatibility shim is usually right in a repo that
  has decided against backwards compatibility — but it is a decision about someone's data, so
  say what the old path was for before you remove it.
- Prose is part of the code: a docstring or guide that describes behaviour the code no longer
  has will be trusted by the next reader and sends them looking for the wrong property.
- If the change needs a credential, a network host or a permission you do not hold, ask for it
  with the `ask_user` action rather than working around it — a workaround becomes the convention.
