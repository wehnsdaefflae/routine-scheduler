# Schedule-once — one-shot time trigger (DESIGN)

> **Status: IMPLEMENTED in 0.71.0** (audit decision **D27 → A**, scope (a): any
> `scheduling`-holder may target any routine; self-target always allowed). This document is
> both the design rationale and the shipped design. Code: `src/rsched/schedule_once.py`
> (spool), `src/rsched/daemon/schedule_once.py` (`OneShotManager`), the `schedule_run` action
> (`engine/actions.py` + `interact.py`), the `scheduling` permission
> (`library-seed/permissions/scheduling.md`), the API (`web/api_schedule.py`), and
> `tests/test_schedule_once.py`. The UI *Schedule once* card + week-strip surfacing remain a
> follow-up.
> Cross-refs: `docs/triggers.md` (event triggers), `src/rsched/schedule.py` (cron),
> `src/rsched/grants.py` (capabilities), `src/rsched/daemon/triggers.py` (the fire manager
> this mirrors).

## Motivation

Today a routine fires on **cron** (repeats forever) or **manually** (now) or on an
**event trigger** (webhook). There is no way to say *"run once at a specific future
instant, then never again."* Use cases:

- A run schedules its own follow-up: *"re-check the BahnBonus seat in 3 days."*
- A user arms a single future run from the routine page without inventing a throw-away
  cron they must remember to delete.
- One routine arms a **one-shot on another routine** (with permission) after some
  milestone — the reviewer's explicit ask: *settable / activatable / deactivatable by
  another routine.*

## Where it lives — a daemon-owned `.control/` spool, NOT `routine.yaml`

The sharp constraint (from `grants.py`): **`routine.yaml` is NEVER writable by a run** —
config is the user's. Event triggers (`triggers:`) are `routine.yaml` config precisely
because only the user/UI creates them. A one-shot that a *routine* must be able to set
therefore **cannot** be a `routine.yaml` entry.

So model it like `restart.request` and the webhook event spool
(`.control/triggers/<slug>/`): a **daemon-owned request spool** the web layer AND a
gated engine action may write, and the daemon consumes.

```
<routines_home>/.control/schedule-once/<slug>/req-<id>.json   # one armed one-shot
<routines_home>/.control/schedule-once/<slug>/state.json      # daemon fire ledger
```

```jsonc
// req-<id>.json  (atomic write, mirrors triggers.write_event)
{
  "id": "so-4f9a01bc",                    // server/engine-generated stable handle
  "fire_at": "2026-07-22T03:00:00+00:00", // absolute UTC instant (aware)
  "active": true,                         // false = armed-but-paused (deletion = cancel)
  "reason": "re-check BahnBonus seat availability",
  "requested_by": "self-audit:20260719-101727",  // or "ui"
  "created": "2026-07-19T10:30:00+00:00",
  "expires_at": null                      // optional: drop instead of fire if past this
}
```

This keeps `routine.yaml` the user's, makes the one-shot *operational state* the daemon
owns, and reuses an idiom the codebase already trusts (crash-safe file spool + daemon
ledger).

## Firing + auto-deactivate

A new **`OneShotManager`** (`daemon/schedule_once.py`) mirrors `TriggerManager`, ticked by
the `Scheduler` after the cron loop and beside `triggers.tick` (`daemon/scheduler.py`,
5 s tick). Each tick, for every `req-*.json` with `active` and `fire_at <= now`:

1. **Same fire gates as cron/trigger fires** — skip if `runner.draining` or
   `runner.is_active(slug)`. The request stays and fires on the next free tick
   (overrun-safety and coalescing for free, exactly like `TriggerManager._service`).
2. **Fire** via `runner.fire(cfg, reason="schedule_once")`, injecting `reason` as an inbox
   provenance message first (inject-then-fire, crash-safe, mirrors `TriggerManager._fire`).
3. **Auto-deactivate = consume.** On a successful fire, **delete `req-<id>.json`** and
   record the fire in `state.json` (`last_fired`, `fires++`). The armed file is gone, so
   **nothing can re-fire it** — this IS the non-repeating guarantee (no `routine.yaml`
   rewrite, no self-disabling cron).
4. A req whose routine is missing/disabled is dropped with a log line (like
   `TriggerManager._drop`).

**Missed while the daemon was down:** a `fire_at` already past at boot is still on disk →
it fires on the first tick (a make-up fire — desirable for a one-shot; the point is it
*eventually* runs once). Bound staleness with the optional `expires_at`: past it, the req
is dropped instead of fired.

## Deactivate / cancel before firing

- **Cancel** = delete the req file (idempotent). This is what the UI Cancel button and the
  routine `cancel` variant both do.
- `active: false` is an optional armed-but-paused state (re-arm later). Deletion is the MVP;
  the `active` flag is cheap to honour in the tick.

## The cross-routine permission + engine action (the novel part)

Routines cannot write `.control/` (outside `fs_write_roots`) nor `routine.yaml`. So arming
is a **new engine action** the engine executes un-sandboxed, exactly like `write_util` /
`remove_util` / `detach`:

- **New gated action kind `schedule_run`.** Fields: `{target: <slug>, fire_at: <iso or
  relative like "+3d">, reason: <text>, active?}`; a `cancel` variant by `{target, id}`.
  Wire in `actions.py` (KINDS, REQUIRED_FIELDS, KIND_EXAMPLES, validate: `is_slug(target)`,
  `fire_at` parseable and in the future, non-empty `reason`).
- **Gating.** Add `schedule_run` to `grants.GATED_KINDS`, sourced from a new conduct
  permission doc `permissions/scheduling.md` whose `requires.actions: [schedule_run]` drives
  the activation cascade; add to `_DEFAULT_KIND_SOURCE`.
- **Dispatch.** `loop.py` routes to `interact.handle_schedule_run`, which resolves the
  target routine dir (`paths`/`registry`), writes the spool req atomically engine-side, and
  returns the created `id`. `cancel` removes the file. Follow the `remove_util` wiring
  (observations/composer/capabilities) for the full surface.
- **Cross-routine scope** (a real decision for the build — flag in D27):
  - **(a)** Any routine holding `scheduling` may target ANY routine. Simple; fits the current
    single-operator deployment (every routine is the same owner). **Recommended now.**
  - **(b)** Consent model: a target opts in (`accepts_scheduled: true`) or the armer carries a
    `targets:` allow-list. The hardening path if multi-tenant ever arrives.
  - **Self-targeting** (a routine arming its own follow-up) is the common case — always allowed.

## API + UI

- **API** (web layer, sibling of `api_schedule.py` / `api_hooks.py` — the web layer already
  writes `.control/`):
  - `POST /api/routines/<slug>/schedule-once` `{fire_at, reason}` → writes a req (user/UI path).
  - `GET /api/routines/<slug>/schedule-once` → armed one-shots + fire ledger (a
    `describe_*` like `describe_triggers`).
  - `DELETE /api/routines/<slug>/schedule-once/<id>` → cancel.
- **UI** — a *Schedule once* card on the routine page beside the Triggers card: a local-time
  datetime picker (converted to an absolute UTC instant on write, reusing `schedule.py`'s
  server-tz conventions), a reason field, and the list of armed one-shots with a Cancel
  button + last-fired ledger.
- **Dashboard** — armed one-shots surface as single future points in the week strip
  (`api_schedule.schedule_week`), so the operator sees them alongside cron fires.

## Testing plan (implementation follow-up)

- **Unit:** spool read/write/consume; `OneShotManager.tick` fires when due, defers on
  active/draining, consumes on fire, drops on missing/disabled routine, make-up-fires a past
  req at boot, honours `expires_at`.
- **Action:** `validate_action` for `schedule_run` (bad slug, past `fire_at`, empty reason);
  grants gating (denied without `scheduling`).
- **Web + UI:** the three endpoints; a `tests/ui/` Playwright flow (arm → appears → cancel →
  gone; stub-daemon fire consumes the req). Every UI change is exercised in the gate
  (CLAUDE.md).

## Why this needs sign-off (D27, not a self-evident fix)

It adds a **new engine action kind** (`schedule_run`) — a change to the **action-schema
contract** — plus a new conduct permission and a new **cross-routine authority**. Those are
behaviour/contract changes, so they go through a decision (**D27: approve this spec &
implement**), never a unilateral audit fix. This document is the spec that decision approves.

## Alternatives considered

- **`routine.yaml` `triggers:` entry `type: once`** — rejected for the routine-set case
  (`routine.yaml` is never run-writable); it would force two homes (UI-config vs routine
  spool). The unified `.control/` spool serves both the user and a routine.
- **A cron that fires once** — cron has no "once"; it would need catchup + self-disable that
  rewrites `routine.yaml`, fighting the user-owns-config invariant. Rejected.
