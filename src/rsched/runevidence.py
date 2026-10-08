"""What a PAST run's own files say about it — MIGRATION(expires=2026-11-30): only the one-shot
run-record backfill (`migrate_runrecords.py`) reads it, and it goes with that migration.

A run that finished before 0.398.0 recorded no `quality`; its `status.json` and transcript, where
retention kept them, still carry most of it. Each reading below matches what a live run counts
(engine/runrecord.quality), and each says UNKNOWN rather than zero for what a file cannot show:

- a status key the release did not write is absent, not zero;
- a transcript count the release could not have recorded (`RECORDED_SINCE`) is absent too — a
  run before the verifier existed challenged nothing because nothing could;
- the person's interventions are the injections a live run counts when it drains its inbox at a
  turn boundary, told apart by the payload's own markers (the engine stamps a `source` on
  everything it writes, a channel `via` on everything a machine files, and words the answer to
  the run's own question with `ANSWER_PREFIX`). Before `via` existed (0.365.0) a machine's
  message carried no channel, and the injections without one were surveyed over every surviving
  transcript on 2026-10-08: each was the person's own words.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .engine import runrecord
from .engine.hold import is_hold
from .engine.inbox import user_authored
from .engine.transcript import read_events
from .paths import read_json
from .readmodels.change_keys import version_key

#: The release from which a transcript RECORDS each count. The verifier (0.243.0) wrote its
#: challenges as `stopping_unsupported` until 0.369.0 renamed them `claims_unsupported`; the
#: first holds were a reminder's (0.304.0), a rule's joined them in 0.306.0.
RECORDED_SINCE = {"challenged": "0.243.0", "disputed": "0.243.0", "holds": "0.304.0"}
#: How the engine words the answer to the run's own deferred question (engine/control.py) —
#: input it asked for, which a live run does not count as an intervention.
ANSWER_PREFIX = "ANSWER to your deferred question"


def started(run_ts: str) -> datetime | None:
    """A run's start instant from its run_ts (`YYYYMMDD-HHMMSS`, UTC — `ids.run_ts`)."""
    try:
        return datetime.strptime(run_ts[:15], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _transcript(run_dir: Path) -> list[dict]:
    for name in ("transcript.jsonl", "transcript.jsonl.gz"):
        if (run_dir / name).is_file():
            return read_events(run_dir / name)[0]
    return []


def evidence(run_dirs: list[Path]) -> tuple[dict, list[dict]]:
    """(status, top-level transcript events) from the first of `run_dirs` holding either."""
    for run_dir in run_dirs:
        raw = read_json(run_dir / "status.json")
        status = raw if isinstance(raw, dict) else {}
        events = _transcript(run_dir)
        if status or events:
            return status, events
    return {}, []


def interventions(events: list[dict]) -> int:
    """The person's messages that reached the run WHILE it worked (module docstring)."""
    count, acting = 0, False
    for ev in events:
        kind, p = ev.get("type"), ev.get("payload") or {}
        if kind == "assistant_action":
            acting = True
        elif kind == "finish":
            acting = False
        elif kind == "user_injection" and acting:
            text = str(p.get("text") or "")
            if (text.strip() and not text.startswith(ANSWER_PREFIX) and not p.get("boot")
                    and not p.get("command") and not p.get("report") and "source" not in p
                    and user_authored(str(p.get("via") or ""))):
                count += 1
    return count


def quality_of(st: dict, events: list[dict], engine: str) -> dict | None:
    """What a run's status.json (`st`, {} when gone) and transcript say about how it went, on
    engine release `engine` ("" when unknown) — None when neither file survives.
    """
    if not st and not events:
        return None
    q: dict = {}
    if "accounting" in st:
        q.update(runrecord.accounting_counts(st["accounting"]))
    if isinstance(st.get("stages"), dict):
        q["stages_skipped"] = len(st["stages"].get("skipped") or [])
    for key in ("schema_retries", "elapsed_s"):
        if isinstance(st.get(key), int):
            q[key] = st[key]
    if isinstance(st.get("usage"), dict):
        q["cache_read"] = int(st["usage"].get("cached_in") or 0)
        q["cache_write"] = int(st["usage"].get("cache_write") or 0)
    if events:
        obs = [e.get("payload") or {} for e in events if e.get("type") == "observation"]
        counts = {
            "challenged": sum(len(p.get("claims_unsupported") or p.get("stopping_unsupported")
                                  or []) for p in obs),
            "disputed": sum(len((e.get("payload") or {}).get("disputed") or [])
                            for e in events if e.get("type") == "stopping_update"),
            "holds": sum(1 for p in obs if is_hold(p))}
        q.update({k: n for k, n in counts.items()
                  if engine and version_key(engine) >= version_key(RECORDED_SINCE[k])})
        q["interventions"] = interventions(events)
    return q


def model_id_of(status: dict, events: list[dict]) -> str:
    """The `endpoint/model` the run recorded itself running on — its transcript header's
    orchestrator, else its status's `model` — or "".
    """
    header: dict = next((e for e in events if e.get("type") == "header"), {})
    orch = header.get("orchestrator")
    if isinstance(orch, dict) and orch.get("endpoint") and orch.get("model"):
        return f"{orch['endpoint']}/{orch['model']}"
    model = str(status.get("model") or "")
    return model if "/" in model else ""
