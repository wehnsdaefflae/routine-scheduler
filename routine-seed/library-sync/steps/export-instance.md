# State: export-instance

Stage the instance into the library repo working tree. One util call; no editing, no cleanup.

## Do

Run the util action `instance-export` once, with args
`["~/.local/share/routine-scheduler-libraries", "--json"]`.

It mirrors every routine dir (minus transient run state: runs/, .git/, inbox/, questions/,
status.json) into `routines/<slug>/` inside the repo tree, and writes the server config —
`token` / `api_key` values REDACTED — to `config/config.yaml`. It is idempotent and rsync-like:
files deleted at the source are pruned from the tree.

## Read the result

From the JSON, note for the summary: how many routines were exported, files copied/pruned, and
whether the config was exported. An `error` in the result (or a non-zero exit) → note it verbatim
and STILL advance — the sync step must run regardless so nothing already staged is stranded; the
run will finish `partial`.

## Advance

Set `state/phase.json` current state to `sync-repo` and follow `steps/sync-repo.md`.
