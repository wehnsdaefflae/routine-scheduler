---
tags: [tool-use, ssh, remote, machines]
requires:
  utils: [remote]
---
# permission: remote-machines — act on bound remote machines over SSH

Unlocks the reserved `remote` util: run commands and move files on the SSH hosts this routine
is BOUND to (a machine name → `gu remote list`). You never handle credentials — the engine
injects the bound machines' connection details and keys; a machine not bound is invisible, and
host keys are pinned (a mismatch refuses to connect). Use it for work that needs specific
hardware (a GPU box, a build server). Commands:
- `remote exec <machine> --command "…"` — run and WAIT; for SHORT commands only (there is a
  timeout, and a lost connection loses the output).
- `remote submit <machine> --command "…"` — start a DETACHED job (a GPU train/render that runs
  for minutes-to-hours); returns a job id. Poll `remote status`/`logs <machine> --job <id>`, or
  pass `--notify-webhook <your trigger URL>` so the job pings you on completion — do NOT block a
  run polling for hours.
- `remote push/pull <machine> --src … --dest …` — move files (SFTP).
The remote host is NOT sandboxed: a command there runs with that account's full privileges.
