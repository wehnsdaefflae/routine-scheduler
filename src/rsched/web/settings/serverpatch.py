"""`PATCH /api/settings/server` — the APPLY ROUTE for the instance config surface.

The third `config_patch` surface (operator, 2026-10-19: *"build the server surface + D191's
apply route: a decision's config_patch may name {"server": {...}} with its own declared field
list"*). Before it existed, a run holding his explicit approval for an instance-wide change had
no mechanism at all to land it: every `config_patch` was judged against the two routine-shaped
vocabularies, so a server key was refused at filing. Two approved changes sat 10 and 3 days in
config-optimizer's ledger (R2702) — not for want of consent, for want of a door.

**Why this is a PATCH beside the existing PUT.** `PUT /api/settings/server` is the settings
panel's save: it sends the whole knob block and omits nothing it means to keep. A decision's
patch names ONE or TWO fields, and the fields it may name span three existing writers (the
runtime knobs, the two catalog POINTERS, the decision defaults). PATCH semantics are what a
patch means — only the named fields change — and keeping it a separate verb leaves the panel's
contract untouched.

**The allow-list is enforced here, not in the caller's restraint.** `configflow.SERVER_PATCH_FIELDS`
is checked again at this seam even though `engine/config_bridge.server_shape` already refused a
stray key at filing time, because the filing check exists to give the refusal early and this one
exists to make it true: anything that can reach this route — the Decisions page's button today,
D191's autonomous apply next — passes through the same gate. R2702's caution is from the routine
that once made four real config.yaml writes believing it held a capability, when it had in fact
inherited the operator's admin token through a util's fallback: *"a second route to the same
boundary would be worse than the first, because it would look legitimate."*

**What it will not take, and why that is not an oversight.** The catalogs (`endpoints`, `models`,
`machines`, `decision_endpoints`, `decision_models`) are absent from the allow-list: an entry
carries credentials and key material, and each already has CRUD routes that validate an entry as
a whole. The auth tokens, the bind address, the homes and the source repo are install-time — they
decide where data lives and how the socket is served, so they move with a redeploy, not with a
decision card. A field added to `ServerConfig` tomorrow is sealed on the day it lands, because
the allow-list names fields rather than excluding them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from ...config import EFFORT_LEVELS, ServerConfig
from ...config.decisionconf import multimodal_effective
from ...configflow import SERVER_PATCH_FIELDS
from .common import reload_into, server_of, update_config
from .server import URL_FIELDS

router = APIRouter()


class ServerPatch(BaseModel):
    """The instance PATCH body — one optional field per patchable knob.

    `extra="forbid"` so a key outside the surface is a 422 naming it, never a silently dropped
    field the caller is then told was applied (the Decisions page verifies the `updated` list
    for exactly that reason, R102). `tests/test_configflow.py` pins these fields to
    `configflow.SERVER_PATCH_FIELDS` one-to-one, so the vocabulary the engine refuses against
    and the one this route accepts cannot drift apart.
    """

    model_config = ConfigDict(extra="forbid")

    sandbox: str | None = None
    max_concurrent_runs: int | None = None
    registry_rescan_s: int | None = None
    github_client_id: str | None = None
    browser_view_url: str | None = None
    desktop_broker_url: str | None = None
    desktop_view_url: str | None = None
    system_model: str | None = None
    system_effort: str | None = None
    compaction_model: str | None = None
    compaction_effort: str | None = None
    decision_model: str | None = None
    decision_media_model: str | None = None


#: Every patchable field → the live `ServerConfig` attribute `reload_into` must refresh. Spelled
#: out because a write that is not mirrored back is invisible to the running process until a
#: restart: the daemon holds ONE long-lived ServerConfig that every request and every scheduler
#: tick reads.
MIRROR = tuple(sorted(SERVER_PATCH_FIELDS))


def _catalog_problem(server, updates: dict) -> str:
    """Why these values would not load — the same guards the three settings routes apply.

    Judged BEFORE anything is written, because a config.yaml naming a model the catalog lacks
    loads with a problem line and the pointer silently reverting: the instance would be left
    with a decision recorded as applied and a value that is not in effect.

    A BLANK value always passes: clearing a pointer is a real intent (the operator's F7 answer
    was `decision_media_model` → blank), and `""` is what every one of these fields means by
    "unset".
    """
    for field in ("system_model", "compaction_model"):
        name = str(updates.get(field) or "")
        if name and name not in server.models:
            return f"{field}: {name!r} is not a catalog model — add it to the catalog first"
    for field in ("system_effort", "compaction_effort"):
        level = str(updates.get(field) or "")
        if level and level not in EFFORT_LEVELS:
            return f"{field}: must be one of {', '.join(EFFORT_LEVELS)} (or blank)"
    for field in ("decision_model", "decision_media_model"):
        name = str(updates.get(field) or "")
        if name and name not in server.decision_models:
            return (f"{field}: {name!r} is not a decision model — the decide catalog is its own "
                    f"(`decision_models`), separate from the chat catalog")
    media = server.decision_models.get(str(updates.get("decision_media_model") or ""))
    if media is not None and not multimodal_effective(
            media, server.decision_endpoints.get(media.endpoint)):
        return (f"decision_media_model: {updates['decision_media_model']!r} does not take "
                f"images — it is the default for a decide call that carries them")
    return ""


def _url_problem(updates: dict) -> str:
    """The address fields must be absolute: they are dialled by a server-side relay, so a bare
    host is a connection error at use time rather than a refusal at save time.
    """
    for field, example in URL_FIELDS.items():
        value = str(updates.get(field) or "").strip().rstrip("/")
        if not value:
            continue
        if not value.startswith(("http://", "https://")):
            return (f"{field} must start with http:// or https:// — it is an address this "
                    f"console dials, e.g. {example}")
        updates[field] = value
    return ""


@router.patch("/settings/server")
def patch_server(request: Request, body: ServerPatch) -> dict:
    """Apply the named instance config fields and mirror them onto the live ServerConfig.

    ALL OR NOTHING: every value is judged before anything is written. A half-applied config
    change would settle the decision that proposed it as handled while part of it silently did
    not land — the one state a decision surface must never be in.

    `updated` is the applied-field list the Decisions page's honesty gate compares against the
    keys it sent, so it must never under-report.
    """
    updates = body.model_dump(exclude_none=True)
    if stray := sorted(k for k in updates if k not in SERVER_PATCH_FIELDS):
        # Unreachable through `ServerPatch` (extra="forbid" and the pinning test), and checked
        # anyway: this route is the boundary, and a boundary that trusts its callers is not one.
        raise HTTPException(400, f"{', '.join(stray)}: not patchable instance config")
    if not updates:
        return {"ok": True, "updated": [], "problems": []}
    try:
        # ServerConfig's own fields judge each value — the sandbox vocabulary, every bound — so
        # the config a restart loads can never refuse what this route saved, and no bound is
        # restated here to drift from the one the loader enforces.
        ServerConfig.model_validate(updates)
    except ValidationError as exc:
        raise HTTPException(400, "; ".join(
            f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors())) from exc
    if problem := _url_problem(updates) or _catalog_problem(server_of(request), updates):
        raise HTTPException(400, problem)
    path = update_config(request, lambda raw: raw.update(updates))
    return {"ok": True, "updated": sorted(updates),
            "problems": reload_into(request, path, *MIRROR),
            "restart_for": ["max_concurrent_runs"] if "max_concurrent_runs" in updates else []}
