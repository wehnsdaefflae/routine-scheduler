# Curated consequence reminders

One JSON file per reminder, `rem-<run-ts>-<n>.json`:

```json
{
  "id": "rem-20260905-090000-1",
  "regex": "^util:fs-ops mv ",
  "description": "mv over an existing destination overwrites it silently — check the target first",
  "created_run": "some-routine:20260905-090000"
}
```

A reminder here is CURATED and shared: a matching action is HELD before it runs in every
routine whose `reminders` capability is at `global`, so the run can decide again with the
caution in front of it. That reach is why a write needs the user's approval
(`remind_confirm`) and why the routing rule is *born local, global is earned* — a reminder
belongs here only when the same consequence would follow for ANY routine making that exact
call, which is to say when it is about the util or the action rather than about one
routine's files.

The records carry no statistics. A reminder's DEFINITION is shared; the evidence about it —
how often it fired and how those fires turned out — is per-routine and lives in that
routine's `state/reminders.json`, because "did this fire uselessly" is a question about one
routine's work. It also keeps this repo from taking a commit on every fire, from every
routine, concurrently.

Removing one is the Library tab's job (or delete the file and commit). There is deliberately
no way for a run to remove a reminder it does not hold.

See `docs/reminders.md` in the routine-scheduler repo.
