"""The LLM task center: the daemon's single source of truth for what LLM work is in flight.

Records from in-process web calls (via `DaemonSink`) and from tailed engine subprocesses (via
`daemon.llm_tailer`) both funnel through `ingest()`, which updates live state AND publishes an
`llm_task` bus event. Frontend-initiated multi-call work (routine creation) is an
`llm_process` — a parent that holds its calls as children and is removed only once it is closed
and all its children are terminal. The overlay mirrors this live and reconciles against
`snapshot()`.

All mutations run on the daemon's event loop: `DaemonSink` marshals cross-thread calls there
(complete() runs in threadpool/`to_thread` workers, and `asyncio.Queue` is not thread-safe), and
the tailer already runs as a loop coroutine. So the center itself needs no lock.
"""

from __future__ import annotations

import asyncio
import time

from .ids import now_iso

LINGER_S = 12.0          # keep a terminal task / closed process this long (shows "done" briefly)
MAX_TASKS = 1000         # hard cap on retained tasks (drop oldest terminal first)

#: How long a task may stay `running` before it is treated as abandoned rather than in flight.
#: A task prunes only once `done_at` is set, and only a finished/failed record or `close_process`
#: sets it — so a call whose owning process never closes was retained for the DAEMON'S LIFETIME
#: (F572: six abandoned `Compaction · archival` tasks visible in one conversation's panel, with
#: the group row rendering them as `0/24`). A caller that gives a call up ON PURPOSE says why
#: through the ordinary channel — `endpoints/instrument.abandon_open_calls` writes the call's
#: `failed` record, which `ingest` folds like any other. The ceiling is the backstop for every
#: path that does not, or cannot, report back: well above the slowest real call — archival
#: itself waits 180-600s — so it never reaps live work, and far below "forever".
MAX_RUNNING_S = 3600.0

_STATUS = {"started": "running", "finished": "done", "failed": "error"}


class TaskCenter:
    """In-memory registry of open processes + live/recently-finished tasks. One per daemon."""

    def __init__(self, bus):
        self.bus = bus
        self.processes: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}

    # --- processes -----------------------------------------------------------
    def open_process(self, id: str, *, kind: str, label: str, run_id: str | None = None) -> None:
        self.processes[id] = {"id": id, "kind": kind, "label": label, "run_id": run_id,
                              "opened": now_iso(), "closed": False}
        self.bus.publish({"event": "llm_process", "phase": "opened", "id": id,
                          "kind": kind, "label": label, "run_id": run_id})

    def close_process(self, id: str, *, error: str | None = None) -> None:
        pr = self.processes.get(id)
        if pr is None:
            return
        pr["closed"] = True
        pr["closed_at"] = time.monotonic()
        if error:
            pr["error"] = error
        # A closed process's calls can't still be running — the run/subprocess ended, possibly
        # killed mid-call (an abort leaves a `started` sidecar record with no `finished`). Orphan
        # any in-flight child so it, and then this process, can prune instead of leaking forever.
        for t in self.tasks.values():
            if t.get("process_id") == id and t.get("status") == "running":
                t.update(status="error", error=t.get("error") or "ended before completion",
                         done_at=time.monotonic())
        self._prune()
        self.bus.publish({"event": "llm_process", "phase": "closed", "id": id, "error": error})

    # --- tasks ---------------------------------------------------------------
    def ingest(self, rec: dict) -> None:
        """Fold one lifecycle record into live state and broadcast it. Idempotent-ish: a
        record for a known id updates that task (started → finished/failed).
        """
        tid = rec.get("id")
        if not tid:
            return
        entry = self.tasks.get(tid, {})
        entry.setdefault("started_at", time.monotonic())   # the age backstop's clock (F572)
        entry.update(rec)
        entry["status"] = _STATUS.get(str(rec.get("phase") or ""),
                                      entry.get("status", "running"))
        if entry["status"] in ("done", "error"):
            entry["done_at"] = time.monotonic()
        self.tasks[tid] = entry
        self._prune()
        self.bus.publish({"event": "llm_task", "status": entry["status"], **rec})

    # --- reconcile snapshot --------------------------------------------------
    def snapshot(self) -> dict:
        self._prune()
        return {"processes": [self._pub_process(p) for p in self.processes.values()],
                "tasks": [self._pub_task(t) for t in self.tasks.values()]}

    _INTERNAL = ("done_at", "started_at")   # monotonic bookkeeping, meaningless to a client

    @classmethod
    def _pub_task(cls, t: dict) -> dict:
        return {k: v for k, v in t.items() if k not in cls._INTERNAL}

    @staticmethod
    def _pub_process(p: dict) -> dict:
        return {k: v for k, v in p.items() if k != "closed_at"}

    # --- housekeeping --------------------------------------------------------
    def _prune(self) -> None:
        now = time.monotonic()
        # A task `running` past the ceiling is not in flight — it is the residue of a process
        # that never reported back, and nothing else would ever set its `done_at` (F572). Mark it
        # terminal rather than deleting it, so it prunes by the ordinary linger below and the
        # panel can show for one linger WHY the row went away.
        for entry in self.tasks.values():
            if entry.get("status") != "running":
                continue
            started = entry.get("started_at")
            if started is None:
                entry["started_at"] = started = now
            if now - started > MAX_RUNNING_S:
                entry.update(status="error", done_at=now,
                             error=entry.get("error")
                             or f"abandoned: no result after {int(MAX_RUNNING_S)}s")
        # drop terminal tasks past the linger (done_at may legitimately be 0.0 → test `is not None`)
        for tid in [t for t, e in self.tasks.items()
                    if e.get("done_at") is not None and now - e["done_at"] > LINGER_S]:
            del self.tasks[tid]
        # bound memory: over the cap, evict oldest terminal tasks first
        if len(self.tasks) > MAX_TASKS:
            terminal = sorted((e["done_at"], t) for t, e in self.tasks.items()
                              if e.get("done_at") is not None)
            for _, tid in terminal[: len(self.tasks) - MAX_TASKS]:
                self.tasks.pop(tid, None)
        # remove a closed process once its children are all terminal and the linger elapsed
        for pid in list(self.processes):
            pr = self.processes[pid]
            if not pr.get("closed"):
                continue
            live = any(e.get("status") == "running" and e.get("process_id") == pid
                       for e in self.tasks.values())
            if not live and now - pr.get("closed_at", now) > LINGER_S:
                del self.processes[pid]


class DaemonSink:
    """The web/daemon process's sink: marshal each record onto the event loop, where the
    TaskCenter (and the non-thread-safe bus) live. `complete()` runs in threadpool / to_thread
    workers, so a direct call would touch asyncio state from the wrong thread.
    """

    def __init__(self, center: TaskCenter, loop: asyncio.AbstractEventLoop):
        self.center = center
        self.loop = loop

    def record(self, rec: dict) -> None:
        self.loop.call_soon_threadsafe(self.center.ingest, rec)
