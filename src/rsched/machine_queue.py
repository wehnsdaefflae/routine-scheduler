"""Fair-share job queue for a machine whose compute is one-job-at-a-time (a GPU box).

Operator, 2026-09-05: "routines that use the gpu on the external predator often seem to block the
gpu for one another… i would prefer they found a way to schedule it so everyone gets their turn."

## What was actually contended

Not the RUN — the detached JOB. All three predator routines launch work with `remote submit`,
which returns immediately and leaves a process on the box for hours. That is why the obvious fix
was already in place and already failing: voice-model-trainer and funscript-trainer are both in
the `Labs` lane, whose chain is strictly sequential. They still collided — member 0 finishes in
minutes and leaves a training job on the card that member 1 walks straight into.
eye-stabilize-folder is in a different lane entirely and cannot see either of them. Sequencing
the RUNS was never going to sequence the JOBS, which is the whole finding.

Facing that vacuum the routines invented their own protocol: a `gpu_lease.py` inside
funscript-trainer's `scripts/`, lease JSONs in the store the Labs routines share, and a
hand-reimplemented copy in voice-model-trainer that once had to reclaim an 18-hour-stale lease.
Three incompatible protocols, owned by one routine, invisible to the daemon and to the console.

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
  The ONE definition is the `remote` util's `fair_share_order`, which the util ships to the box
  by source; the box orders over the whole round (the turns already spent plus the ones still
  waiting), so nothing on this side re-derives an order — it reads the box's.
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
queue into `<routines_home>/.control/machine-queue/<name>.json` at most once a minute
(REFRESH_AFTER_S) so the prompt and the console can render it without an SSH round-trip per
reader. The rate is set by what a READER tolerates, not by the tick that notices: every read is
an SSH session and an interpreter boot on the box.

Derived state, never config: deleting the mirror costs one refresh.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from .paths import atomic_write_json, read_json

log = logging.getLogger("rsched.machine_queue")

QUEUE_DIR = Path(".control") / "machine-queue"
#: A mirror older than this is not shown as truth — a machine we cannot reach must read as
#: unknown rather than as empty, or a run would think the GPU is free because the box is down.
STALE_AFTER_S = 900
#: How old a mirror may get before `refresh` re-reads the box. Every read is an SSH session and a
#: `uv run --script` interpreter boot, so the refresh rate is set by what READERS tolerate, not by
#: the scheduler's 5s tick: consumers accept STALE_AFTER_S (15 min) of age, and once a minute
#: keeps the mirror an order of magnitude inside that while turning ~17k sessions a day into ~1.4k.
REFRESH_AFTER_S = 60


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


def _stale(fetched: str, max_age_s: float = STALE_AFTER_S) -> bool:
    if not fetched:
        return True
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(fetched)).total_seconds()
    except ValueError:
        return True
    return age > max_age_s


def position_of(tickets: list[dict], job: str) -> int | None:
    """1-based place in the queue AS THE BOX ORDERED IT, or None when the job is not queued.

    Deliberately does NOT re-sort. The mirror holds what `remote queue` returned, and the box
    orders over the whole round — the turns already spent plus the ones waiting. Re-deriving the
    order here from the live tickets alone would drop the spent half and answer FIFO (the ticket
    that just ran takes with it the evidence that its holder used a turn), so a run would be told
    a position the machine does not agree with. The ordering DEFINITION is the `remote` util's
    `fair_share_order` (the box runs that very function); this is the reader.
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


def _record(routines_home: Path, machine: str, tickets: list[dict], *,
            error: str, was: str) -> dict:
    """Save one mirror and log the moment a machine starts or stops answering.

    Only the TRANSITION is worth a line: a box that is down stays down for hours, and at one
    refresh a minute a per-attempt warning would bury the daemon log in the same sentence. The
    mirror itself carries the current reason for every reader.
    """
    if error and not was:
        log.warning("machine %s queue unreadable: %s", machine, error)
    elif was and not error:
        log.info("machine %s queue readable again", machine)
    return save(routines_home, machine, tickets, error=error)


def refresh(server, *, timeout: int = 60) -> dict[str, dict]:
    """Re-read every EXCLUSIVE machine whose mirror is older than REFRESH_AFTER_S and rewrite it.
    `{name: doc}` for the machines actually re-read.

    The TTL is the point: reading the queue costs an SSH session and an interpreter boot on the
    box, so a fresh mirror is simply handed back unread. A machine that is down is re-attempted on
    the same clock, which bounds the SSH attempts an unreachable box can pile onto the caller.

    Never raises. A machine we cannot reach records its reason, and `capability_note` renders that
    as UNKNOWN rather than as an empty queue — the one failure mode that would actually cause the
    collision this whole mechanism exists to prevent.
    """
    from . import sandbox, utils_run
    from .machines import machines_for_routine
    from .secrets import load_secrets

    out: dict[str, dict] = {}
    # machine -> the error its mirror last recorded, for the machines due a re-read
    due: dict[str, str] = {}
    for name, mac in (server.machines or {}).items():
        if not mac.exclusive:
            continue
        doc = load(server.routines_home, name)
        if not _stale(str(doc.get("fetched") or ""), REFRESH_AFTER_S):
            continue
        due[name] = str(doc.get("error") or "")
    if not due:
        return out
    secrets = load_secrets()
    for name, was in due.items():
        # The util is handed exactly what the engine injects for a routine bound to this
        # machine: `machines_for_routine` owns both env vars and their shapes — the metadata list
        # and `{machine NAME: PEM}`. Hand-rolling them here keyed the PEM by `key_var` instead,
        # so the util reported "no private key available" and the mirror recorded UNKNOWN
        # forever. One resolver, one contract.
        env, _warnings = machines_for_routine([name], server.machines, secrets=secrets)
        try:
            code, stdout, stderr = utils_run.run_util(
                server.libraries_home, REMOTE_UTIL, ["queue", name, "--json"],
                timeout=timeout, policy=sandbox.base_policy(server), extra_secrets=env)
        except OSError as exc:
            out[name] = _record(server.routines_home, name, [], error=str(exc), was=was)
            continue
        if code != 0:
            out[name] = _record(server.routines_home, name, [], was=was,
                                error=(stderr.strip() or stdout.strip()
                                       or f"remote util exited {code}")[:300])
            continue
        try:
            payload = json.loads(stdout)
            tickets = payload.get("tickets") if isinstance(payload, dict) else None
        except ValueError:
            tickets = None
        if not isinstance(tickets, list):
            out[name] = _record(server.routines_home, name, [], was=was,
                                error="the remote util did not report a queue (is it new enough "
                                      "to support `remote queue`?)")
            continue
        out[name] = _record(server.routines_home, name, tickets, error="", was=was)
    return out
