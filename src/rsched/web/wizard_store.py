"""On-disk helpers for the protected 'clarification' template routine and its clarify runs.

The standalone new-routine WIZARD (its page, setup panel, /api/wizard endpoints and the
in-flight `.wizard-*` session machinery) was retired in D59 — routine creation now happens
entirely from a conversation via the `create_routine` engine action. What remains here are the
clarification-template support symbols that survivor modules (api_questions, api_runs,
routines_common, api_routines) still import.
"""

from __future__ import annotations

from pathlib import Path

from ..paths import read_json

# The protected 'clarification' template routine: clarify runs live under this slug so the
# standard run page / SSE tail / registry all apply (D13=B). Survivors reference the slug to
# recognise a clarify run and route its inbox correctly.
TEMPLATE_SLUG = "clarification"


def read_meta(d: Path) -> dict:
    obj = read_json(d / "state" / "wizard_meta.json")
    return obj if isinstance(obj, dict) else {}


def latest_run_ts(d: Path) -> str | None:
    runs = sorted((d / "runs").glob("*")) if (d / "runs").is_dir() else []
    return runs[-1].name if runs else None


def clarify_run_dir(server, d: Path, ts: str) -> Path:
    """Where a session's clarify run lives. New sessions land it under the REAL clarification
    routine — `routines_home/clarification/runs/<ts>` — so the run has a valid
    `clarification:<ts>` id and every standard run surface (run page, SSE tail, registry,
    orphan recovery) applies with no bridge (D13=B). Legacy sessions, and deploys the
    template has not reached, keep the run session-local under `<session>/runs/<ts>`.
    """
    real = server.routines_home / TEMPLATE_SLUG / "runs" / ts
    return real if real.is_dir() else d / "runs" / ts


def clarify_run_id(server, d: Path, ts: str | None) -> str:
    """`clarification:<ts>` when this session's run lives under the template (D13=B) — the
    standard run page renders it, so every surface links there. Empty for a legacy
    session-local run (no navigable run page; the session can only be canceled).
    """
    if not ts:
        return ""
    rd = clarify_run_dir(server, d, ts)
    return f"{TEMPLATE_SLUG}:{ts}" if rd.parent.parent.name == TEMPLATE_SLUG else ""


def session_inbox_dir(server, run_dir: Path) -> Path:
    """The inbox a run-page message (inject/converse) must land in so a LIVE run actually
    polls it. For a D13=B clarify run the artifact dir is `clarification/runs/<ts>` but the
    engine executes the session in the hidden throwaway workspace `.wizard-<ts>` and polls
    THAT dir's inbox — so a message routed to `clarification/inbox` would never be seen.
    Redirect to the workspace inbox when this run is a clarify run (its artifact dir sits
    under the clarification template) and the `.wizard-<ts>` workspace still exists. Every
    other run — ordinary routines, and legacy session-local clarify runs whose run_dir is
    already under `.wizard-<ts>` — falls through to the normal `routine_dir/inbox`.
    """
    routine_dir = run_dir.parent.parent
    if routine_dir.name == TEMPLATE_SLUG:
        workspace = server.routines_home / f".wizard-{run_dir.name}"
        if workspace.is_dir():
            return workspace / "inbox"
    return routine_dir / "inbox"
