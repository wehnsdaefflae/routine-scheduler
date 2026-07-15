"""Usage statistics aggregation — time, tokens, and cost rolled up across every run in
the routines AND conversations homes, sliced by routine, model, endpoint, day, kind, and
run-state. The filesystem (each run's status.json) is the source of truth: no database, no
cache — a routine dropped in appears on the next call, one deleted disappears.

Powers the Stats tab (/api/stats). Kept a pure function of a ServerConfig so it is fully
unit-testable without a running server.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime

from .config import ServerConfig
from .daemon import registry
from .endpoints import EndpointError, EndpointRegistry
from .paths import read_json

# run-state buckets rolled into a single success/failure health read for the tab
_OK_STATES = {"finished"}
_BAD_STATES = {"failed", "aborted"}


def _empty() -> dict:
    return {"runs": 0, "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0,
            "cost": 0.0, "elapsed_s": 0}


def _add(acc: dict, usage: dict, elapsed_s) -> None:
    acc["runs"] += 1
    acc["tokens_in"] += int((usage or {}).get("in") or 0)
    acc["tokens_out"] += int((usage or {}).get("out") or 0)
    # prompt-cache reads (~0.1x price) — separate so cache hit rates are visible
    acc["tokens_cached"] += int((usage or {}).get("cached_in") or 0)
    if (usage or {}).get("cost"):
        acc["cost"] = round(acc["cost"] + float(usage["cost"]), 6)
    acc["elapsed_s"] += int(elapsed_s or 0)


def _run_day(ts: str) -> str:
    """A run dir name is `YYYYMMDD-HHMMSS`; the day is its date part. A pure string
    reformat of the (local-time) dir name — no timezone semantics, so no tz to attach.
    """
    raw = str(ts)[:8]
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])).isoformat()
    except ValueError:
        return "unknown"


def _run_ref(recorded, main_ref) -> tuple[str, str]:
    """(endpoint, model) attribution for one run. status.json's `model` is the engine's
    resolved `<endpoint>/<model>` for that run — authoritative, it survives a mid-run
    switch_model. The routine's main ref (already system_model-backed by the caller,
    mirroring `EndpointRegistry.for_model`) covers legacy runs that predate the field.
    """
    endpoint, sep, model = str(recorded or "").partition("/")
    if sep:
        return endpoint, model
    fallback = main_ref.endpoint if main_ref else "unknown"
    if endpoint:  # a bare model name with no endpoint prefix (old status.json shape)
        return fallback, endpoint
    return fallback, (main_ref.model if main_ref else "unknown")


def aggregate(server: ServerConfig, *, now: datetime | None = None) -> dict:
    """Walk both homes and roll up usage into every slice the Stats tab renders."""
    homes = [("routine", server.routines_home), ("conversation", server.conversations_home)]
    totals = _empty()
    by_routine: dict[str, dict] = {}
    by_model: dict[str, dict] = defaultdict(_empty)
    by_endpoint: dict[str, dict] = defaultdict(_empty)
    by_day: dict[str, dict] = defaultdict(_empty)
    by_kind = {"routine": _empty(), "conversation": _empty()}
    by_state: dict[str, int] = defaultdict(int)

    runs: list[dict] = []   # per-run records — the raw series the configurable charts slice
    reg = EndpointRegistry(server)

    for kind, home in homes:
        catalog = registry.scan(server, home)
        for slug, info in catalog.items():
            # the engine's role resolution (EndpointRegistry.for_model): a routine that leaves
            # models.main unset runs on the server's system_model. Resolve the catalog NAME to a
            # ModelRef so _run_ref can attribute legacy runs whose status.json lacks the model.
            main_name = (info.cfg.models or {}).get("main") or server.system_model
            try:
                main_ref = reg.resolve(main_name)[1] if main_name else None
            except EndpointError:
                main_ref = None
            racc = _empty()
            for r in info.runs:
                st = read_json(r.dir / "status.json")
                endpoint, model = _run_ref(
                    st.get("model") if isinstance(st, dict) else "", main_ref)
                _add(totals, r.usage, r.elapsed_s)
                _add(racc, r.usage, r.elapsed_s)
                _add(by_model[model], r.usage, r.elapsed_s)
                _add(by_endpoint[endpoint], r.usage, r.elapsed_s)
                _add(by_day[_run_day(r.ts)], r.usage, r.elapsed_s)
                _add(by_kind[kind], r.usage, r.elapsed_s)
                by_state[r.state] = by_state.get(r.state, 0) + 1
                runs.append({"day": _run_day(r.ts), "routine": slug, "kind": kind,
                             "state": r.state, "model": model, "endpoint": endpoint,
                             "tokens_in": int((r.usage or {}).get("in") or 0),
                             "tokens_out": int((r.usage or {}).get("out") or 0),
                             "tokens_cached": int((r.usage or {}).get("cached_in") or 0),
                             "cost": float((r.usage or {}).get("cost") or 0.0),
                             "elapsed_s": int(r.elapsed_s or 0)})
            if info.runs:
                by_routine[slug] = {**racc, "kind": kind,
                                    "endpoint": main_ref.endpoint if main_ref else "unknown",
                                    "model": main_ref.model if main_ref else "unknown"}

    def _tok(d: dict) -> int:
        return d["tokens_in"] + d["tokens_out"]

    ok = sum(v for k, v in by_state.items() if k in _OK_STATES)
    bad = sum(v for k, v in by_state.items() if k in _BAD_STATES)
    graded = ok + bad
    return {
        "generated": (now or datetime.now(UTC)).isoformat(),
        "totals": {**totals,
                   "routines": sum(1 for v in by_routine.values() if v["kind"] == "routine"),
                   "conversations": sum(1 for v in by_routine.values()
                                        if v["kind"] == "conversation"),
                   "success_rate": round(ok / graded, 4) if graded else None},
        "by_routine": dict(sorted(by_routine.items(), key=lambda kv: _tok(kv[1]), reverse=True)),
        "by_model": dict(sorted(by_model.items(), key=lambda kv: _tok(kv[1]), reverse=True)),
        "by_endpoint": dict(sorted(by_endpoint.items(), key=lambda kv: _tok(kv[1]), reverse=True)),
        "by_day": dict(sorted(by_day.items())),
        "by_kind": by_kind,
        "by_state": dict(by_state),
        # per-run records (bounded by retention: keep_runs per routine) — the Stats tab's
        # configurable charts bucket these client-side by any dimension × metric
        "runs": sorted(runs, key=lambda r: r["day"]),
    }
