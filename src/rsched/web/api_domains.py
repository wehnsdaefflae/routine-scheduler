"""The DOMAIN surface — CRUD over the shared config block, its store, and who is in it.

The line between this surface and the lane API (web/api_lanes.py) is the axis each one owns: a
shared `config` block is a DOMAIN's, while `members` and `cron` are a LANE's, because those are
the temporal axis and a lane is daemon-owned instance state. Keeping the two apart is what stops
a scheduling decision from also being a permissions decision (docs/lanes-domains.md).

Membership is deliberately NOT a field on this surface. A routine names its domain in its own
routine.yaml, so joining or leaving one is an ordinary routine config save (`PATCH
/api/routines/<slug>`) and this endpoint only READS the membership back. That is what makes
"at most one domain" a fact of the routine's file rather than a rule someone has to enforce
across a list — and it puts the decision where every other per-routine setting is, user-only,
writable by no run.

Validating that block lives here too (`_validate_config` and its two helpers). Whether its
permission and rule slugs resolve, whether its machines are in the catalog and whether its
capability mapping is well-formed are questions about a DOMAIN's document — and the lane API
holds no such document.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import domains
from .routines_common import _state

router = APIRouter()


def _routines_home(request: Request):
    return _state(request).server.routines_home


def _config_layers(request: Request, config: dict) -> dict:
    """A domain's shared permissions/capabilities in the routine-detail shape. A tiny stand-in
    carrying just the two fields `permission_layers_detail` reads keeps ONE implementation of
    that shape for both surfaces.
    """
    from types import SimpleNamespace

    from .routines_common import permission_layers_detail

    shared = SimpleNamespace(permissions=list(config.get("permissions") or []),
                             capabilities=dict(config.get("capabilities") or {}))
    perms, caps = permission_layers_detail(_state(request).server, shared)
    return {"permissions": perms, "capabilities": caps}


def _orphan_capabilities(server, config: dict) -> list[str]:
    """Capabilities a domain switches on that none of its OWN permissions requires.

    Not an error: a member may hold the covering doc itself, which is the arrangement the
    floor cannot see and must not break. But it is nearly always a mistake — the domain grants
    the means without the conduct — so whoever saves it is told, by name.
    """
    from ..grants import _DEFAULT_KIND_SOURCE, read_library_requires, split_util_verb

    caps = config.get("capabilities") or {}
    if not caps:
        return []
    lib = read_library_requires(server.permissions_home)
    held = list(config.get("permissions") or [])
    req_utils = {u for slug in held for u in (lib.get(slug) or {}).get("utils") or []}
    req_names = {split_util_verb(u)[0] for u in req_utils}
    req_actions = {a for slug in held for a in (lib.get(slug) or {}).get("actions") or []}
    out = [f"util {u!r}" for u in caps.get("utils") or []
           if u not in req_utils and split_util_verb(u)[0] not in req_names]
    out += [f"action {a!r}" for a in caps.get("actions") or []
            if a not in req_actions and _DEFAULT_KIND_SOURCE.get(a) not in held]
    return out


def _validate_config(request: Request, config: dict | None) -> dict | None:
    """Validate a domain's SHARED routine config (D82) exactly as the routine save path
    validates a member's own: unknown permission/rule slugs and unknown machines are
    rejected by name; the capability mapping is normalized. `domains.clean_config` keeps
    shape; existence is ours, because we hold the library and the machine catalog.

    Deliberately NOT floored against the domain's permissions: the floor binds a ROUTINE's two
    layers, so flooring the domain document in isolation would delete a capability whose
    covering permission the member holds itself.

    Nothing floors the MERGED config either, which is deliberate too — enforcement reads
    capabilities ONLY (test_policy_enforces_capabilities_not_docs), precisely so the doc layer
    can never widen what a run may do. The cost is that a domain CAN hand its members a reserved
    util or gated kind with no conduct doc behind it, which the three invariants above each
    correctly decline to catch. So it is REPORTED instead, twice: `_orphan_capabilities`
    above warns whoever saves the domain, while `readmodels/surface.py` shows it per routine
    however it got there — a hand-edited file and a restored backup arrive with no save at all.
    """
    if config is None:
        return None
    from .. import library_docs
    from ..grants import normalize_capabilities

    server = _state(request).server
    unknown: list[str] = []
    for key, home in (("permissions", server.permissions_home), ("rules", server.rules_home)):
        known = set(library_docs.slugs(home))
        unknown += [f"{key[:-1]} {s!r}" for s in config.get(key) or [] if s not in known]
    catalog = server.machines
    unknown += [f"machine {m!r}" for m in config.get("machines") or [] if m not in catalog]
    if unknown:
        raise HTTPException(400, f"unknown {', '.join(sorted(unknown))}")
    if "capabilities" in config:
        caps, problems = normalize_capabilities(config["capabilities"], label="config.capabilities")
        if problems:
            raise HTTPException(422, "; ".join(problems))
        config = {**config, "capabilities": caps}
    return config


class DomainCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    config: dict | None = None


class DomainPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    config: dict | None = None


def _record(request: Request, d: dict) -> dict:
    home = _routines_home(request)
    config = d.get("config") or {}
    return {**d, "members": domains.members(home, d["id"]),
            "layers": _config_layers(request, config),
            "orphan_capabilities": _orphan_capabilities(_state(request).server, config),
            "store": str(domains.store_dir(home, d["id"]))}


@router.get("/domains")
def list_domains(request: Request) -> dict:
    """Every domain, each with the members that NAME it and what its shared block grants.

    `members` is read from the routines rather than stored here, so it cannot disagree with
    itself and a routine deleted from disk is out of the domain by construction.
    """
    return {"domains": [_record(request, d)
                        for d in domains.list_domains(_routines_home(request))]}


@router.post("/domains")
def create_domain(request: Request, body: DomainCreate) -> dict:
    config = _validate_config(request, body.config)
    try:
        rec = domains.create(_routines_home(request), name=body.name, config=config or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _record(request, rec)


@router.patch("/domains/{domain_id}")
def update_domain(request: Request, domain_id: str, body: DomainPatch) -> dict:
    config = _validate_config(request, body.config)
    try:
        rec = domains.update(_routines_home(request), domain_id, name=body.name, config=config)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if rec is None:
        raise HTTPException(404, f"no domain {domain_id!r}")
    return _record(request, rec)


@router.delete("/domains/{domain_id}")
def delete_domain(request: Request, domain_id: str) -> dict:
    """Delete a domain. Its members stop inheriting at their next run.

    Refused while routines still name it: deleting one out from under its members would leave
    every one of them pointing at nothing and silently narrow what they may do, discovered at
    3am by a run that can no longer reach a root. Empty it first — which is a routine config
    save each member's own page makes, exactly as joining is.

    The STORE is left on disk either way. It holds files members wrote, so a config record
    disappearing is not consent to delete data nobody asked about.
    """
    home = _routines_home(request)
    if held := domains.members(home, domain_id):
        raise HTTPException(409, "still named by " + ", ".join(held)
                            + " — clear `domain` on those routines first")
    if not domains.delete(home, domain_id):
        raise HTTPException(404, f"no domain {domain_id!r}")
    return {"ok": True}
