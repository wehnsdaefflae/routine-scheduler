"""Local Claude-subscription usage (D33): tokens burned through `claude-cli` endpoints
in the rolling last 5 hours (Anthropic's subscription quota window) and last 7 days.

Anthropic exposes no balance/quota API for subscriptions, so this is the honest local
proxy: fold every run served by a claude-cli endpoint (both homes, status.json usage —
running runs count live) into the two windows. Run start times come from the run-ts dir
name (`YYYYMMDD-HHMMSS`, server-local wall clock by ids.run_ts design), compared against
a local-naive `now` — injectable for tests. Attribution mirrors stats._run_ref: the
recorded "<endpoint>/<model>" wins, a legacy/empty field falls back to the routine's
main model.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import registry
from ..config import ServerConfig
from ..endpoints import EndpointError, EndpointRegistry
from .stats import _run_ref

WINDOWS = {"5h": timedelta(hours=5), "7d": timedelta(days=7)}


def _run_start(ts: str) -> datetime | None:
    try:
        return datetime.strptime(str(ts), "%Y%m%d-%H%M%S")  # noqa: DTZ007 — run-ts dir names are local-naive by design (ids.run_ts)
    except ValueError:
        return None


def claude_usage(server: ServerConfig, *, now: datetime | None = None) -> dict:
    """{"supported": False} when no claude-cli endpoint is configured; else per-window
    {"runs", "tokens_in", "tokens_out", "tokens_cached"} sums over runs STARTED inside
    the window (a still-running run counts — its status.json usage is live).
    """
    cli_eps = sorted(n for n, ep in server.endpoints.items() if ep.kind == "claude-cli")
    if not cli_eps:
        return {"supported": False}
    now = now or datetime.now()   # noqa: DTZ005 — local-naive, like the run-ts dir names it compares to
    cutoff = {k: now - d for k, d in WINDOWS.items()}
    oldest = min(cutoff.values())
    wins = {k: {"runs": 0, "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0}
            for k in WINDOWS}
    reg = EndpointRegistry(server)
    for home in (server.routines_home, server.conversations_home):
        for info in registry.scan(server, home).values():
            main_name = (info.cfg.models or {}).get("main") or server.system_model
            try:
                main_ref = reg.resolve(main_name)[1] if main_name else None
            except EndpointError:
                main_ref = None
            for r in info.runs:
                start = _run_start(r.ts)
                if start is None or start < oldest:
                    continue
                endpoint, _model = _run_ref(r.model, main_ref)
                if endpoint not in cli_eps:
                    continue
                usage = r.usage or {}
                for k, cut in cutoff.items():
                    if start >= cut:
                        w = wins[k]
                        w["runs"] += 1
                        w["tokens_in"] += int(usage.get("in") or 0)
                        w["tokens_out"] += int(usage.get("out") or 0)
                        w["tokens_cached"] += int(usage.get("cached_in") or 0)
    return {"supported": True, "endpoints": cli_eps, "generated": now.isoformat(),
            "windows": wins}
