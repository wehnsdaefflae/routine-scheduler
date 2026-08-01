"""Admin conversation — a run-scoped bypass of the CAPABILITY-gating layer, unlocked ONLY
when the incoming web request carried a valid RSCHED_ADMIN_TOKEN (D62).

The shape mirrors engine/revise.py exactly: the web layer validates the admin token
(constant-time, fail-closed) and, on a match, drops a one-shot marker in the run dir before
resuming the conversation. The turn loop reads the marker ONCE at init and, for that leg
only, builds the run's GrantPolicy with admin=True — which lifts CAPABILITY gating (gated
action kinds, reserved utils, previous-run read depth) so an admin conversation can reach
every tool the operator holds.

What admin does NOT lift — the STRUCTURAL / ownership gates stay in force, by design:
- the workflow `tools:` allowlist (a kind still has to be surfaced to the run),
- the root-conversation-only gate on create_routine / manage_group / detach,
- runs/ being engine-owned and read-only,
- routine.yaml (config) being the user's — NO run writes it, admin included,
- the own-recipe write seal (unless a user fs_write_root already covers the dir).

The two are separable on purpose: admin means "the operator is driving this conversation
by hand and wants the full toolset", not "structural invariants are suspended".

The token NEVER reaches the engine — the web layer is the only place it is compared, and
the engine sees only the boolean marker. The marker is never persisted to routine.yaml and
never inherited by sub-workflows (a child builds its own policy with capabilities off), so
a later non-admin request to the same conversation is an ordinary run again. Every action a
run takes under admin appends one line to <routines_home>/.control/admin-audit.jsonl so the
bypass is auditable and never silent.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from ..ids import now_iso
from ..paths import atomic_write_json, read_json

ADMIN_MARKER = "admin.json"
ADMIN_TOKEN_ENV = "RSCHED_ADMIN_TOKEN"  # noqa: S105 — an env-var NAME, not a secret value
ADMIN_AUDIT_FILE = "admin-audit.jsonl"
# The request header the web UI sends alongside the normal bearer to request admin for a
# conversation leg — an ADDITIONAL factor, never a bearer substitute (require_auth still runs).
ADMIN_HEADER = "x-admin-token"


def admin_token_valid(presented: str | None) -> bool:
    """True when `presented` matches the configured RSCHED_ADMIN_TOKEN, compared in
    constant time. Fail-closed: an unset or empty env token means admin is DISABLED for
    the whole instance, so no request can ever obtain it (a dummy compare still runs so the
    branch is not a timing oracle for token presence).
    """
    configured = os.environ.get(ADMIN_TOKEN_ENV) or ""
    presented = presented or ""
    if not configured:
        secrets.compare_digest("x" * 32, presented or "y")
        return False
    return secrets.compare_digest(configured, presented)


def write_admin_marker(run_dir: Path) -> None:
    """Drop the one-shot marker the loop reads at init (web layer, before resume) — only
    ever called once the presented token has already passed admin_token_valid.
    """
    atomic_write_json(Path(run_dir) / ADMIN_MARKER, {"admin": True, "ts": now_iso()})


def admin_marker(run_dir: Path) -> bool:
    """True if this leg was unlocked as an admin conversation."""
    obj = read_json(Path(run_dir) / ADMIN_MARKER)
    return bool(obj.get("admin")) if isinstance(obj, dict) else False


def clear_admin_marker(run_dir: Path) -> None:
    """One-shot: admin covers exactly the leg the operator authenticated, nothing after."""
    try:
        (Path(run_dir) / ADMIN_MARKER).unlink(missing_ok=True)
    except OSError:
        pass


def log_admin_action(routines_home: Path, *, run_id: str, kind: str, brief: str = "") -> None:
    """Append one line per action taken under admin to <routines_home>/.control/admin-audit.jsonl
    so the capability bypass is never silent. Best-effort — an I/O error never blocks the run
    (mirrors health_events.log_health_event).
    """
    path = Path(routines_home) / ".control" / ADMIN_AUDIT_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": now_iso(),
                "run_id": run_id,
                "kind": kind,
                "brief": brief[:200],
            }) + "\n")
    except OSError:
        pass
