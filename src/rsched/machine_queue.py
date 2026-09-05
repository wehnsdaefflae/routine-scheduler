"""Fair-share job queue for a machine whose compute is one-job-at-a-time (a GPU box).

Operator, 2026-09-05: "routines that use the gpu on the external predator often seem to block the
gpu for one another… i would prefer they found a way to schedule it so everyone gets their turn."

## What was actually contended

Not the RUN — the detached JOB. All three predator routines launch work with `remote submit`,
which returns immediately and leaves a process on the box for hours. That is why the obvious fix
was already in place and already failing: voice-model-trainer and funscript-trainer are both in
the `Labs` group, whose chain is strictly sequential, and they still collided — member 0 finishes
in minutes and leaves a training job on the card that member 1 walks straight into.
eye-stabilize-folder is in a different group entirely and cannot see either of them.

Facing that vacuum the routines invented their own protocol: a `gpu_lease.py` inside
funscript-trainer's `scripts/`, lease JSONs in the Labs group store, and a hand-reimplemented copy
in voice-model-trainer that once had to reclaim an 18-hour-stale lease. Three incompatible
protocols, owned by one routine, invisible to the daemon and to the console.

## Why a QUEUE and not a lock

A mutex answers "may I go now?" with yes or no. Asked by three routines on a daily cron, "no" is
the answer two of them get every day, and nothing records that they asked. An flock is only
marginally better: it blocks rather than refusing, but the order is arbitrary, a routine that
submits three jobs can starve one that submits one, and a wedged holder silently stacks the rest
behind it with nothing visible anywhere.

So: tickets, FAIR-SHARE order, and a deadline on every job.

- **Fair share** is round-robin across HOLDERS by each holder's oldest waiting ticket, FIFO within
  one holder. Three jobs from funscript-trainer and one from voice-model-trainer interleave
  f, v, f, f — the routine that asked once does not wait behind a routine that asked three times.
- **Every ticket carries a deadline.** A detached job has no live process to heartbeat against, so
  a wall clock is the only thing that can make the queue self-healing. Past it the job is killed
  and its ticket dropped.
- **Nobody blocks.** `submit` returns a job id and a queue POSITION immediately; the run reads its
  position in the CAPABILITIES section and can spend the run on a non-GPU increment instead. That
  is the difference the operator asked for: everyone gets a turn, and knows when.

## Where the truth lives

ON THE BOX. The tickets are files under the machine's own job root, so the queue survives a daemon
restart, a container recreate, an instance migration, and a human working on the machine by hand —
and the `remote` util enforces it at the one place that opens an SSH connection. This module is a
READ MODEL over that truth plus the write path for an operator cancel: the daemon mirrors the
queue into `<routines_home>/.control/machine-queue/<name>.json` on its tick so the prompt and the
console can render it without an SSH round-trip per reader.

Derived state, never config: deleting the mirror costs one tick.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from itertools import zip_longest
from pathlib import Path

from .paths import atomic_write_json, read_json

log = logging.getLogger("rsched.machine_queue")

QUEUE_DIR = Path(".control") / "machine-queue"
#: A mirror older than this is not shown as truth — a machine we cannot reach must read as
#: unknown rather than as empty, or a run would think the GPU is free because the box is down.
STALE_AFTER_S = 900


def mirror_path(routines_home: Path, machine: str) -> Path:
    return routines_home / QUEUE_DIR / f"{machine}.json"


def save(routines_home: Path, machine: str, tickets: list[dict], *, error: str = "") -> dict:
    """Write one machine's queue mirror. `error` records an unreachable box rather than an
    empty queue — the two must never look the same to a reader.
    """
    doc = {"machine": machine, "fetched": datetime.now(UTC).isoformat(),
           "tickets": tickets, "error": error}
    path = mirror_path(routines_home, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, doc)
    return doc


def load(routines_home: Path, machine: str) -> dict:
    """`{machine, fetched, tickets, error, stale}` — the mirror as a reader sees it."""
    doc = read_json(mirror_path(routines_home, machine))
    if not isinstance(doc, dict):
        return {"machine": machine, "fetched": "", "tickets": [], "error": "", "stale": True}
    doc.setdefault("tickets", [])
    doc.setdefault("error", "")
    doc["stale"] = _stale(str(doc.get("fetched") or ""))
    return doc


def _stale(fetched: str) -> bool:
    if not fetched:
        return True
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(fetched)).total_seconds()
    except ValueError:
        return True
    return age > STALE_AFTER_S


def fair_share_order(tickets: list[dict]) -> list[dict]:
    """Round-robin across holders by each holder's oldest waiting ticket, FIFO within a holder.

    THE definition of "everyone gets their turn". It lives here and the `remote` util ships this
    exact function to the box, so the two halves cannot drift. Three tickets from one routine and
    one from another interleave A, B, A, A — the routine that asked once does not wait behind the
    routine that asked three times.

    **Give it the WHOLE round — the turns already spent plus the ones still waiting.** Applied to
    the live set alone it silently collapses to FIFO, because deleting the ticket that just ran
    also deletes the evidence that its holder used a turn, so that holder is head again
    immediately. (Found by the util's own end-to-end harness: four jobs, two holders, `f1 f2 f3
    v1` instead of `f1 v1 f2 f3`.) The box therefore retires a finished ticket into `round/`
    rather than deleting it and orders over `spent + live`. Nothing here should re-derive an
    order for a partly-served round — read the box's, which `remote queue` already returns in its
    true order and `save()` preserves.
    """
    by_holder: dict[str, list[dict]] = {}
    for t in sorted(tickets, key=lambda t: str(t.get("submitted") or "")):
        by_holder.setdefault(str(t.get("holder") or "?"), []).append(t)
    # holders enter the rotation in the order their oldest ticket arrived, so a newcomer does not
    # jump ahead of someone already waiting
    holders = sorted(by_holder, key=lambda h: str(by_holder[h][0].get("submitted") or ""))
    # interleaving each holder's FIFO queue IS the round-robin: take one from every holder that
    # still has one, in holder order, until all are drained
    return [t for row in zip_longest(*(by_holder[h] for h in holders)) for t in row
            if t is not None]


def position_of(tickets: list[dict], job: str) -> int | None:
    """1-based place in the queue AS THE BOX ORDERED IT, or None when the job is not queued.

    Deliberately does NOT re-sort. The mirror holds what `remote queue` returned, and the box
    orders over the whole round — the turns already spent plus the ones waiting. Re-deriving the
    order here from the live tickets alone would drop the spent half and answer FIFO, so a run
    would be told a position the machine does not agree with. The ordering DEFINITION is
    `fair_share_order` above (the box runs that very function); this is the reader.
    """
    for i, t in enumerate(tickets, start=1):
        if str(t.get("job") or "") == job:
            return i
    return None


def capability_note(routines_home: Path, machine: str, slug: str) -> str:
    """The one clause the CAPABILITIES section carries for an exclusive machine.

    Written for a run deciding what to do THIS run: it says whether the compute is free, how many
    jobs are ahead, whether any of them are this routine's own, and — the load-bearing part — that
    a queued job costs the run nothing, so it should pick other work rather than wait. Reports
    what the machine actually HAS, never what the catalog claims (the R514 doctrine); an
    unreachable box says so instead of reading as free.
    """
    doc = load(routines_home, machine)
    if doc["error"] or doc["stale"]:
        why = doc["error"] or "the queue has not been read recently"
        return (f" · COMPUTE QUEUE UNKNOWN ({why}) — submit if you need it, but do not assume "
                "the machine is free")
    tickets = doc["tickets"]
    if not tickets:
        return " · COMPUTE FREE (no jobs queued)"
    # the mirror is already in the box's own order — see position_of on why not to re-sort
    running = [t for t in tickets if t.get("state") == "running"]
    mine = [t for t in tickets if str(t.get("holder") or "") == slug]
    bits = [f"{len(tickets)} job(s) queued"]
    if running:
        bits.append(f"{running[0].get('holder', '?')} is running now")
    if mine:
        places = ", ".join(f"#{position_of(tickets, str(t.get('job')))}" for t in mine)
        bits.append(f"yours: {places}")
    return (" · COMPUTE QUEUED — " + "; ".join(bits)
            + ". Submitting adds you to the rotation and returns immediately; it does NOT block "
              "this run, so spend the run on work that does not need this machine")


# --------------------------------------------------------------------------------- refresh ----

#: The reserved util that owns the SSH connection. The queue lives ON THE BOX, so reading it is a
#: `remote` call like any other — the daemon does not open its own connection.
REMOTE_UTIL = "remote"


def refresh(server, *, timeout: int = 60) -> dict[str, dict]:
    """Re-read every EXCLUSIVE machine's queue and rewrite its mirror. `{name: doc}`.

    Never raises. A machine we cannot reach records its reason, and `capability_note` renders that
    as UNKNOWN rather than as an empty queue — the one failure mode that would actually cause the
    collision this whole mechanism exists to prevent.
    """
    from . import sandbox, utils_run
    from .secrets import load_secrets

    out: dict[str, dict] = {}
    exclusive = [n for n, m in (server.machines or {}).items() if m.exclusive]
    if not exclusive:
        return out
    secrets = load_secrets()
    for name in exclusive:
        mac = server.machines[name]
        keys = {mac.key_var: secrets[mac.key_var]} if (mac.key_var and mac.key_var in secrets) \
            else {}
        try:
            code, stdout, stderr = utils_run.run_util(
                server.libraries_home, REMOTE_UTIL, ["queue", name, "--json"],
                timeout=timeout, policy=sandbox.base_policy(server),
                extra_secrets={"RSCHED_MACHINE_KEYS": json.dumps(keys),
                               "RSCHED_MACHINES": json.dumps(
                                   [machine_public_of(mac, name)])})
        except OSError as exc:
            out[name] = save(server.routines_home, name, [], error=str(exc))
            continue
        if code != 0:
            out[name] = save(server.routines_home, name, [],
                             error=(stderr.strip() or stdout.strip()
                                    or f"remote util exited {code}")[:300])
            continue
        try:
            payload = json.loads(stdout)
            tickets = payload.get("tickets") if isinstance(payload, dict) else None
        except ValueError:
            tickets = None
        if not isinstance(tickets, list):
            out[name] = save(server.routines_home, name, [],
                             error="the remote util did not report a queue (is it new enough "
                                   "to support `remote queue`?)")
            continue
        out[name] = save(server.routines_home, name, tickets)
    return out


def machine_public_of(mac, name: str) -> dict:
    """The util's own metadata shape for one machine — imported lazily so this module stays
    importable without the config package.
    """
    from .machines import machine_public

    return machine_public(mac, name=name, key_set=bool(mac.key_var))
