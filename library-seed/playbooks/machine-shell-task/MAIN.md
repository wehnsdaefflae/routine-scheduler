---
slug: machine-shell-task
title: Do a job on one of my machines from the command line
when: A task is shell work on a real machine — diagnose a service, inspect or reorganize files, check a disk, set something up — on this box or on a bound remote.
tags:
- shell
- system
- diagnostics
- files
axis: the MACHINE and the job vary; what stays fixed is proving where you are before you act, reading before writing, never a destructive command without a dry run, and reporting what the machine says rather than what the command returned
updated: '2026-11-25'
setup:
  permissions:
  - shell
  rules:
  - fix-the-cause
  - make-failure-visible
  - verify-independently
  - ask-policy
  workdir: '{{workdir}}'
  fs_write_roots:
  - '{{workdir}}'
  fs_read_roots: []
  connections: {}
  note: >-
    `shell` is the permission this playbook is about, and it is the one to grant deliberately: a
    shell write passes none of the gates the file actions apply, so a path the principal sealed
    is reachable from it. Grant `{{workdir}}` as a write root so the file actions can work there
    too, and keep every other path READ-only unless the job needs otherwise. A job on another
    box needs that machine BOUND to the conversation — a remote-execution capability reaches only
    machines the principal configured, and an unbound host is simply unreachable.
---

## Parameters

- `{{machine}}` — where the work happens: this box, or a bound remote by name. Default: this box.
- `{{job}}` — what should be true when this is done.
- `{{workdir}}` — the directory the job operates in, if it has one.
- `{{destructive}}` — whether the job deletes, moves or overwrites anything. Default: assume yes
  until the plan proves otherwise.

## Instructions

1. **Prove where you are before you act.** Name the host, the user, the working directory and
   the disk you are about to touch, and check them against `{{machine}}`. A job run on the right
   path of the wrong machine is the mistake this step exists to prevent — and a sandboxed
   environment is not the host: a service manager, a container tool or a system journal may
   simply not exist there, and a pipeline that greps absent output exits cleanly and reads
   exactly like a clean result.
2. **Read before you write.** List, count, measure, check free space. State the current situation
   in numbers before changing it, so the "after" means something.
3. **For anything destructive, dry-run first and show the principal the list.** Most tools that
   delete or sync have a mode that only reports. Run that, count what it would touch, and get
   the go-ahead with the `ask_user` action when the count is larger than expected or the paths
   are not all inside `{{workdir}}`.
4. **Never run an interactive command.** Nothing that waits for a keystroke will ever return
   here: pass the non-interactive flag, or use a capability built for that service.
5. **Credentials belong in a capability, not on a command line.** If the job needs a token or a
   password, use the catalog capability that reads it from the secret store — a secret typed
   into a shell command is a secret in the transcript forever.
6. **One step at a time, with its output read.** Chaining five commands and reading only the last
   exit code hides which one failed. Where a step is slow, start it in the background and keep
   working rather than blocking.
7. **Diagnose to a cause, not to a symptom.** "The service is not running" is a symptom; the
   journal line that says it was killed, with the limit it hit, is a cause. Quote it.
8. **Verify from the machine's own state.** The directory listing, the service status, the free
   space after. A command's exit code is what it claims; the state is what happened.
9. **Turn what you repeated into a script.** A sequence you ran twice belongs in a saved helper
   rather than retyped — and the next session should not rediscover the flags that worked.
10. **Report the before and after in numbers,** the cause of anything you fixed, and anything you
    deliberately left alone.

## Notes

- A path under someone's home may be a mount, a symlink or a network share; the same filename on
  two devices is two files, and hard links make a copy cost nothing while a move costs everything.
- Many a "stuck" job is waiting on a network filesystem. Measure the round trip before concluding
  the machine is slow.
- If a service needs restarting and the environment cannot reach its manager, say so and ask —
  that is a permission or an access question, not something to work around.
- A command whose output is enormous belongs in a file with a few lines summarized, not pasted
  whole into a reply.
