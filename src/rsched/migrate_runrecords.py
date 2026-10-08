"""One-shot boot migration: every past run's usage record gains the `fingerprint` and `quality`
a live run has recorded since 0.398.0 — MIGRATION(expires=2026-11-30).

Without it the change measurement (readmodels/change_effects.py) starts empty: every change
before 0.398.0 — each recipe edit, config change, rule revision, model switch and engine release
since the stream began in July — would never be judged, because the runs on either side of it
carry no fingerprint. Most of what a fingerprint needs is still on the instance, so it is REBUILT
for each depth-0 run of a routine — live or archived, whichever INCARNATION of the slug it
belonged to (readmodels/incarnations.py) — as of the instant the run STARTED:

- engine — the release `main` held (runhistory.engine_timeline: the branch's reflog);
- config, deliberation, held rules, named model — the routine's `routine.yaml` and `tuning.yaml`
  as committed then, loaded by today's loader and hashed by the live hasher
  (`runrecord.config_hash`), so a rebuilt hash and a live one agree wherever the file did; the
  run's own `status.json` overrides the deliberation it actually used;
- each held rule's text hash — the library's rule file as committed then;
- `model_id` — what the run itself recorded: its transcript header's orchestrator, else its
  status's `model`. Never the catalog: config.yaml is unversioned, so today's entry under a name
  says nothing about what the name meant in August;
- `model` — the catalog name, known only where the routine NAMED its model; a routine on the
  system model ran on whatever that was, which is on record nowhere;
- `effort` — never known: the catalog that set it is unversioned, and until 0.396.0 the
  subscription transport dropped it anyway (a forced tool choice strips thinking);
- recipe — the record's own `recipe_commit`, else the recipe version committed then.

`quality` comes from the run's `status.json` and its top-level transcript where retention kept
them (runevidence.py says what each file can and cannot show). A run whose dir retention removed
may have its two files STAGED by an operator from a backup under
`.control/migrations/run-records-evidence/<slug>/<run_ts>/` before the boot that runs this; the
dir is removed once the rewrite lands. A run with neither keeps no `quality` — its status,
spend and util outcomes are still read, the rest is unknown, never zero.

A rebuilt fingerprint says so: `source: "reconstructed"`, and `unknown` names every component
it could not recover — an unknown component never reads as a change (change_keys.components).
Every leg record of a run gets the same values (the stream's fold keeps the newest leg's).

The runs fingerprinted LIVE since 0.398.0 keep their fingerprint, corrected where 0.398.0 got
it wrong (`amend`): the fields its config hash covers, and the `model_id` it did not record.

Runs at daemon boot, before anything starts a run, so nothing appends to the stream while it
is rewritten (atomically, after checking it did not grow); records what it did in
`.control/migrations/run-records.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import yaml

from . import runhistory
from .bootstrap import repo_root
from .config import DEFAULT_DELIBERATION, load_routine
from .engine import runrecord
from .ids import now_iso
from .paths import atomic_write_json
from .readmodels.incarnations import Incarnation, archived_dirs, incarnation_of, incarnations
from .readmodels.usage_stream import stream_path
from .runevidence import evidence, model_id_of, quality_of, started

log = logging.getLogger("rsched.migrate_runrecords")

RECORD = Path(".control") / "migrations" / "run-records.json"
EVIDENCE = Path(".control") / "migrations" / "run-records-evidence"


def _yaml(text: str) -> dict:
    try:
        raw = yaml.safe_load(text) if text else {}
    except yaml.YAMLError:
        return {}
    return raw if isinstance(raw, dict) else {}


class Rebuilder:
    """The timelines a rebuild reads, each built once and shared by every run."""

    def __init__(self, server) -> None:
        self.server = server
        self.home = Path(server.routines_home)
        self.engine = runhistory.engine_timeline(repo_root())
        self._archived = archived_dirs(self.home)
        self._incs: dict[str, list[Incarnation]] = {}
        self._routines: dict[Path, tuple[runhistory.Timeline, runhistory.Timeline]] = {}
        self._rules: dict[str, runhistory.Timeline] = {}
        self._configs: dict[tuple[str, runhistory.RoutineState], tuple] = {}

    def runs(self, records: list[dict]) -> Iterator[tuple[dict, Incarnation, str, datetime]]:
        """`(record, incarnation, run_ts, start)` for the FIRST record of every depth-0 run of a
        versioned routine incarnation, in stream order.
        """
        seen: set[str] = set()
        for rec in records:
            slug, run_id = str(rec.get("routine") or ""), str(rec.get("run_id") or "")
            if rec.get("depth") != 0 or not slug or run_id in seen:
                continue
            seen.add(run_id)
            if slug not in self._incs:
                self._incs[slug] = incarnations(self.home, slug, self._archived)
            run_ts = run_id.rsplit(":", 1)[-1]
            inc, when = incarnation_of(self._incs[slug], run_ts), started(run_ts)
            if inc is not None and when is not None and (inc.dir / ".git").is_dir():
                yield rec, inc, run_ts, when

    def state(self, inc: Incarnation, when: datetime) -> runhistory.RoutineState | None:
        state = self._timelines(inc.dir)[0].at(when)
        return state if isinstance(state, runhistory.RoutineState) else None

    def _timelines(self, d: Path) -> tuple[runhistory.Timeline, runhistory.Timeline]:
        if d not in self._routines:
            self._routines[d] = (runhistory.routine_timeline(d), runhistory.recipe_timeline(d))
        return self._routines[d]

    def _rule(self, slug: str) -> runhistory.Timeline:
        if slug not in self._rules:
            self._rules[slug] = runhistory.rule_timeline(
                Path(self.server.libraries_home), slug, runrecord.short_hash)
        return self._rules[slug]

    def config(self, slug: str, state: runhistory.RoutineState) -> tuple:
        """(config hash, held rules, named main model, deliberation) of one committed state;
        the first two None when today's loader refuses the file.
        """
        if (slug, state) not in self._configs:
            with tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp) / slug
                d.mkdir()
                (d / "routine.yaml").write_text(state.routine_yaml, encoding="utf-8")
                if state.tuning_yaml is not None:
                    (d / "tuning.yaml").write_text(state.tuning_yaml, encoding="utf-8")
                cfg, _problems = load_routine(d)
            raw, tuning = _yaml(state.routine_yaml), _yaml(state.tuning_yaml or "")
            delib = str(tuning.get("deliberation") or raw.get("deliberation")
                        or DEFAULT_DELIBERATION)
            self._configs[(slug, state)] = (
                None if cfg is None else runrecord.config_hash(cfg),
                None if cfg is None else list(cfg.rules),
                "" if cfg is None else str(cfg.models.get("main") or ""), delib)
        return self._configs[(slug, state)]

    def fingerprint(self, rec: dict, inc: Incarnation, when: datetime, status: dict,
                    events: list[dict]) -> dict:
        unknown = ["effort"]
        fp: dict = {"source": "reconstructed", "engine": str(self.engine.at(when) or "")}
        if not fp["engine"]:
            unknown.append("engine")
        state = self.state(inc, when)
        conf = self.config(str(rec["routine"]), state) if state is not None else None
        if conf is None or conf[0] is None:
            unknown += ["config", "rules", "deliberation", "model"]
        else:
            fp["config"], held, named, fp["deliberation"] = conf
            fp["rules"] = {s: str(self._rule(s).at(when) or "") for s in held or []}
            if named:
                fp["model"] = named
            else:
                unknown.append("model")
        if status.get("deliberation"):
            fp["deliberation"] = str(status["deliberation"])      # what the run actually used
            unknown = [k for k in unknown if k != "deliberation"]
        fp["model_id"] = model_id_of(status, events)
        if not fp["model_id"]:
            unknown.append("model_id")
        if not rec.get("recipe_commit"):
            fp["recipe"] = str(self._timelines(inc.dir)[1].at(when) or "")
            if not fp["recipe"]:
                unknown.append("recipe")
        fp["unknown"] = sorted(set(unknown))
        return fp


def rebuild(builder: Rebuilder, records: list[dict]) -> dict[str, tuple[dict, dict | None]]:
    """`{run_id: (fingerprint, quality)}` for every run no record of which carries a
    fingerprint — the migration's whole computation, apart from writing it.
    """
    # a run that resumed across the 0.398.0 deploy has a live leg: its own record wins
    live = {str(r.get("run_id")) for r in records if r.get("fingerprint")}
    out: dict[str, tuple[dict, dict | None]] = {}
    for rec, inc, run_ts, when in builder.runs(records):
        run_id = str(rec["run_id"])
        if run_id in live:
            continue
        status, events = evidence([inc.dir / "runs" / run_ts,
                                   builder.home / EVIDENCE / str(rec["routine"]) / run_ts])
        fp = builder.fingerprint(rec, inc, when, status, events)
        out[run_id] = (fp, quality_of(status, events, fp["engine"]))
    return out


def amend(builder: Rebuilder, records: list[dict]) -> dict[str, dict]:
    """`{run_id: fingerprint fields}` for the runs fingerprinted LIVE before this release, which
    keep their fingerprint but take two corrections:

    - `config`, hashed again under today's definition (`runrecord.BEHAVIOUR`): 0.398.0 left
      `cron`, `tz` and the other fields its exclusions named by the wrong key in the hash, so its
      runs would otherwise differ from every run after them. Read back from git as of the run's
      start, as for a rebuilt run — compared field by field on the live runs there were, the
      two agreed;
    - `model_id`, which 0.398.0 did not record: from the run's own status or transcript, or
      named in `unknown` — a missing key would read as a KNOWN empty id, a model change.
    """
    live = [r for r in records
            if r.get("fingerprint") and r["fingerprint"].get("source") != "reconstructed"]
    out: dict[str, dict] = {}
    for rec, inc, run_ts, when in builder.runs(live):
        fp, fields = rec["fingerprint"], {}
        state = builder.state(inc, when)
        config = builder.config(str(rec["routine"]), state)[0] if state is not None else None
        if config is not None and config != fp.get("config"):
            fields["config"] = config
        if "model_id" not in fp:
            status, events = evidence([inc.dir / "runs" / run_ts])
            if model_id := model_id_of(status, events):
                fields["model_id"] = model_id
            else:
                fields["unknown"] = sorted({*(fp.get("unknown") or ()), "model_id"})
        if fields:
            out[str(rec["run_id"])] = fields
    return out


def _rewritten(rec: dict, built: dict, amended: dict) -> dict | None:
    """The record as the migration writes it back, or None when it stays as it is."""
    run_id = str(rec.get("run_id") or "")
    fp = rec.get("fingerprint")
    if not fp and run_id in built:
        new_fp, q = built[run_id]
        return {**rec, "fingerprint": new_fp, **({"quality": q} if q else {})}
    if fp and run_id in amended and fp.get("source") != "reconstructed":
        return {**rec, "fingerprint": {**fp, **amended[run_id]}}
    return None


def run_migration(server) -> dict:
    """Rebuild once. Returns the record (empty when it already ran)."""
    home = Path(server.routines_home)
    record_path = home / RECORD
    stream = stream_path(home)
    if record_path.exists() or not stream.is_file():
        return {}
    began = time.monotonic()
    before = stream.stat().st_size
    lines = stream.read_bytes().split(b"\n")
    records: list[dict | None] = []
    for raw in lines:
        try:
            rec = json.loads(raw) if raw.strip() else None
        except ValueError:
            rec = None
        records.append(rec if isinstance(rec, dict) else None)
    parsed = [r for r in records if r is not None]
    builder = Rebuilder(server)
    built, amended = rebuild(builder, parsed), amend(builder, parsed)
    out_lines = []
    for raw, rec in zip(lines, records, strict=True):
        new = _rewritten(rec, built, amended) if rec is not None else None
        out_lines.append(raw if new is None else json.dumps(new, ensure_ascii=False).encode())
    record: dict = {"started": now_iso(), "runs": len(built),
                    "with_quality": sum(1 for _fp, q in built.values() if q),
                    "amended": len(amended), "unknown": _tally(built)}
    if stream.stat().st_size != before:          # something appended: never lose a record
        record["skipped"] = "the stream grew while it was rebuilt — retried at the next boot"
        log.warning("run records: %s", record["skipped"])
        return record
    tmp = stream.with_name(stream.name + ".rebuild")
    tmp.write_bytes(b"\n".join(out_lines))
    tmp.replace(stream)
    shutil.rmtree(home / EVIDENCE, ignore_errors=True)
    record["seconds"] = round(time.monotonic() - began, 1)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("run records: rebuilt %d runs (%d with quality) in %.1fs", record["runs"],
                record["with_quality"], record["seconds"])
    return record


def _tally(built: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fp, _q in built.values():
        for key in fp.get("unknown") or []:
            counts[key] = counts.get(key, 0) + 1
    return counts
