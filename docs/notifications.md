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
5. Or you **defer it yourself** (the Decisions page's *defer to next run* button): the
   run unblocks immediately on its stated default — the timeout path, chosen by you —
   and Discord is told the question was deferred from the console. Deferred (non-blocking)
   records can also be **snoozed** from the Decisions page: hidden there until a
   timestamp, still open to the routine.

## Web Push: what it needs, and what it does not

Subscribing is the only step that needs the console reachable. Once a browser is
subscribed, the server pushes to that browser's push service (Google's for Chrome,
Mozilla's for Firefox) over the public internet, and `static/sw.js` makes **no network
call at all** — the routine name and question text ride in the payload. So a phone off
your LAN, off your VPN, on mobile data still gets the notification; only *tapping* it,
which opens the Decisions page, needs to reach the console again.

Sends carry a **24h TTL**, so a push service holds the decision for a device that is
asleep or out of coverage rather than dropping it. (The library default is `ttl=0` —
deliver now to a connected device or discard — which loses precisely the notifications an
away operator needs, and reports success while doing it.)

Two consequences worth knowing:

- **Subscribing takes the operator's primary token.** `POST /api/push/subscribe` is
  sealed like every other mutation (see the two tiers below). That is deliberate and must
  stay: `send_to_all` fans every decision's text to every stored endpoint, so a caller who
  could register a subscription would have every future decision delivered to a URL of its
  choosing — an exfiltration channel around the grant model with no transcript entry.
- **Subscriptions rotate, and a browser does not tell you.** When one is retired, the
  server only learns on the next push (404/410) — that notification is already lost. The
  service worker re-registers on `pushsubscriptionchange`, authenticating with a token the
  console leaves in a Cache entry (a worker cannot read localStorage); Settings →
  Notifications re-POSTs the live subscription on open as the fallback for browsers that
  never fire the event. Both paths are upserts keyed on the endpoint, so they are no-ops
  when nothing drifted.

A browser holding the **routine** token renders the whole console — every tab is a read —
and fails only on the first mutation. That 403 carries `WWW-Authenticate: Bearer
error="insufficient_scope"`, on which `static/api.js` drops the token and re-opens the
gate; ordinary 403s (a protected template, the credentials dir, a denied path) omit the
marker and are left alone.

## For developers: one seam in the code

All implicit outbound sends go through **`rsched/notify.py`** — the engine's decision
mirror (`engine/decisions.py`) and the daemon's background-task ping (`daemon/detached.py`)
both call it. If a new channel is ever added, it becomes a new permission + a `notify.py`
transport; nothing else in the codebase learns about channels.

See also: [Rules & permissions](rules-permissions.md) · [Background tasks](background-tasks.md)
