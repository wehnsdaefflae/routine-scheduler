# Detached background tasks — long fire-and-forget in conversations

A within-reply `subtask`/`spawn` is a **thread** in the reply's process: it keeps a conversation
live *while a child runs within a reply*, but it dies when the reply's process exits (a conversation
is finish-per-reply / process-per-reply). That can't cover the one case a chat most wants: **kick off
a long job (a 20-minute scrape), keep having a normal back-and-forth about other things, and get told
when it lands.** For that the job must survive across *multiple visible replies* — so it runs as its
**own daemon-managed process**, detached from any single reply.

That is the `detach` action. Naming: the *concept* is a "detached background task"; the *word*
"background" already means the within-reply non-blocking subtask, so the action is the verb `detach`.

## The `detach` action

```json
{"say": "Kicking off the scrape in the background.",
 "kind": "detach",
 "prompt": "<self-contained brief — the task can't ask you blocking questions>",
 "workflow": "general-task",   // optional: a library pattern for the job (default general-task)
 "label": "scrape"}            // optional: the name shown in the conversation rail
```

`detach` is only valid from a **root conversation** (depth 0, under `conversations_home`): a scheduled
routine has no waiting user to relay a result to, and a within-reply child (or a detached task itself)
must not spawn further detaches — so the engine rejects it elsewhere. It is gated by the
**`background-tasks`** permission (default-ON for conversations, `requires: {actions: [detach]}`).

The handler does almost nothing in-process: it writes an intent file to `background_home/.requests/`
and returns. The assistant then `finish`es the reply ("started it — I'll report back") and the
conversation continues normally.

## Lifecycle (the daemon owns it)

A **detached task** is a routine-shaped dir with a unique slug under a NEW `background_home` (a config
peer to `routines_home` / `conversations_home`), whose `routine.yaml` records `owner: {slug, dir}` (the
spawning conversation). The daemon's **`DetachedManager`** (`daemon/detached.py`, the SINGLE writer of
`background_home`, ticked from the scheduler after the cron-fire loop) runs the whole lifecycle, all on
disk so it is restart-safe:

1. **intake** — drains `.requests/*.json`, materializes the task dir (`childrun.materialize_to_disk`),
   writes its `routine.yaml` (owner + permissions/capabilities/models/fs-roots copied from the owner, a
   background-sized budget — NOT the owner's 10-turn reply window — and `write_util`/`memory_*`/`detach`
   stripped), then `runner.fire`s it on a dedicated `BACKGROUND_SLOTS` pool. Idempotency is keyed on
   **run existence**, so a crash between materialize and fire is recoverable.
2. **deliver** (guarded by a `delivered.json` marker + a deterministic message filename, so delivery is
   exactly-once across restarts) — on the task's terminal status it copies its `artifacts/` into
   `<owner>/artifacts/from-bg-<taskid>/` (namespaced, never clobbering the conversation's own artifacts)
   and writes a durable `[background task finished] …` message into the owner's `inbox/`.
3. **wake** — if the owner conversation is idle (its last run is terminal), `runner.resume`s it so the
   result reaches an away user; if a reply is live, the message rides its next turn boundary. This wake
   is state-driven (terminal-owner + pending inbox), so it also catches the race where the owner finishes
   a reply just after the message was written. If the owner holds `communication`, a best-effort Discord
   ping nudges the user to look (the *result* is in the conversation, not the ping).
4. **digest** — rebuilds `<owner>/state/background.json`, which the composer inlines into each reply's
   state digest ("Background tasks you launched: …") so the assistant can answer "how's the scrape
   going?" and knows to relay a newly-finished result.
5. **gc** — removes a delivered task dir a grace window after the owner has drained its message.

Because the engine child spawns with its own session, it **survives a daemon self-update restart**; the
manager's disk-poll delivers it afterward, and detached runs are excluded from the restart drain gate so
a long job never blocks a deploy. Detached tasks use **deferred asks only** (coerced in `handle_ask`) so
one can never park in `waiting_user` and hold a restart.

## Monitor + cancel

- The conversation rail (`static/views/conversations.js`) shows a **background** card: each task's label,
  state, and a cancel button while it runs. It refreshes when the conversation wakes (a completion) and on
  a light poll.
- API (`web/api_conversations.py`): `GET /conversations/{slug}/background` lists them,
  `POST …/background` drops an intent (the human/test analog of the engine action),
  `POST …/background/{id}/cancel` aborts one (`runner.abort` + a pid fallback for a task that outlived a
  restart). Deleting a conversation tears down its detached tasks.
- A detached run's transcript / task-tree resolve on the generic `/api/runs/{run_id}` endpoints
  (`_run_dir` searches `background_home` too).

## Contrast with subtasks/subruns

| | lives | blocks the reply | reports via | survives reply-finish |
|---|---|---|---|---|
| `subtask` (sequential) | a thread in the reply | no (you `wait`) | the finished-hook / `wait` | no |
| `spawn` (parallel) | a thread in the reply | no | the finished-hook | no |
| `detach` (background) | its OWN daemon process | no (you `finish`) | an inbox message + wake | **yes** |

Reach for `detach` only when a job is genuinely long and independent; anything you can finish within the
reply should stay a direct step or a `subtask`.
