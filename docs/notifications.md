# Notifications — how the system reaches you

There is exactly **one way** an agent — routine, conversation, or background task — contacts
you: it files a **durable record on the web console**. Delivery channels then fan out from
that record, and *you* choose which ones are on. The agent never picks a channel implicitly.

## The one primitive: the record

| What the agent does | Where the record lives | Where you see it |
|---|---|---|
| `ask_user` (blocking or deferred) | `questions/pending/<qid>.json` | **Decisions page** |
| util-approval ask (`write_util`) | same record shape, `type: util-approval` | **Decisions page** |
| finish summary (conversation reply) | the run transcript / `result.md` | the chat / run view |
| background-task result (`detach`) | a durable message in the owner conversation's `inbox/` | the conversation |

Everything that needs *a decision from you* is always on the **Decisions page** — blocking
asks, deferred asks, and util approvals share one record shape (`{mode, type, default,
expires}`). Answering on any surface resolves the record everywhere.

## The channels: you switch them on

- **Web** — always on. The Decisions page, the in-app notification tier (Settings →
  Notifications, opt-in), and browser **Web Push** (opt-in per browser, works with the tab
  closed). Both push tiers key off the same open-decisions source the Decisions page reads,
  so the surfaces can never disagree.
- **Discord** — opt-in per routine/conversation by activating the **`communication`**
  permission (which reserves the `discord` util). Two things then happen engine-side:
  - every **blocking decision** is mirrored to your channel; a reply there resolves it on
    the web too (and vice versa — whichever surface answers first counts);
  - a finished **background task** pings the channel so an away user knows to look.
- **Anything else** (Zulip, e-mail, …) is an ordinary **util call by the agent itself**:
  visible in the transcript, gated by the utils you granted, never engine-implicit.

## Example: a blocking ask, end to end

1. A routine holding `communication` reaches a decision it can't make:
   ```json
   {"say": "Need a go/no-go.", "kind": "ask_user", "mode": "blocking",
    "question": "Ship v2 today?", "options": ["yes", "no"],
    "default": "hold the release"}
   ```
2. The engine files the record (Decisions page shows it immediately, badge + push fire)
   and mirrors the question to Discord with the options and the timeout default.
3. You reply `yes` — on either surface. The record resolves, the other surface is told,
   and the run continues with your answer.
4. If you don't answer within `ask_timeout_min`, the run continues on the stated
   `default` and the record stays open as *deferred* — a late answer still reaches the
   next run.

## For developers: one seam in the code

All implicit outbound sends go through **`rsched/notify.py`** — the engine's decision
mirror (`engine/decisions.py`) and the daemon's background-task ping (`daemon/detached.py`)
both call it. If a new channel is ever added, it becomes a new permission + a `notify.py`
transport; nothing else in the codebase learns about channels.

See also: [Traits & permissions](traits-permissions.md) · [Background tasks](background-tasks.md)
