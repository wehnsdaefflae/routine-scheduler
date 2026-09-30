# Curated consequence reminders

One JSON file per reminder, `rem-<run-ts>-<n>.json`:

```json
{
  "id": "rem-20260905-090000-1",
  "regex": "^util:fs-ops mv ",
  "description": "mv over an existing destination overwrites it silently — check the target first",
  "reach": "universal",
  "created_run": "rules-review:20260905-090000"
}
```

A reminder here is CURATED and shared: a matching action is HELD before it runs, so the run can
decide again with the caution in front of it. Its `reach` says whom it holds:

- `universal` — every routine whose action matches. For a consequence any caller of that util or
  action form meets; its anchor limits it, since a routine that never makes the call never pays.
- `listed` — only the routines whose settings list it (`shared_reminders`, which a settings
  pattern carries for its kind of work). For a caution that belongs to one kind of work: git in
  a published repository, a job on a shared GPU.

Every routine with the reminder layer on reads the ones that reach it. Writing here is the
curator's setting (`reminders: global`, held by rules-review) and every write needs the
operator's approval (`remind_confirm`), because a reminder here taxes every routine it reaches.
The routing rule is *born local, shared once earned*: rules-review's reminder pass takes a
census of every routine's own reminders, promotes a caution whose evidence holds across
routines, and routes the rest to the owner of the util or code it describes.

The records carry no statistics. A reminder's DEFINITION is shared; the evidence about it — how
often it fired and how those fires turned out — is per-routine and lives in that routine's
`state/reminders.json`, because "did this fire uselessly" is a question about one routine's
work. It also keeps this repo from taking a commit on every fire, from every routine,
concurrently.

Removing one is the Library tab's job (or the curator's), or delete the file and commit.

See `docs/reminders.md` in the routine-scheduler repo.
