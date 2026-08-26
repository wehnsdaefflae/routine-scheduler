"""A routine's OWN secrets (D103, operator decision 2026-08-26 — R497): the write surface
for `secrets.d/<slug>.env`, the per-routine half of the two-scope store (rsched/secrets.py).

Why it is not just a naming convention in the central store: `SFTP_USER` means a different
thing to every routine that has one, and one flat namespace forces them either to collide or
to be spelled `EYESTAB_SFTP_USER` by discipline no mechanism enforces. A scoped secret is
owned by its routine — implicitly exposed to its runs, invisible to every other routine, and
shadowing a central value of the same name for that routine only.

Because ownership IS the grant, there is no `secret:<NAME>` decision here and the exposure
gate skips these names entirely (engine/interact.py). The declared-only invariant still
holds: a util receives the var only if its `secrets:` header declares it.

Values are write-only, exactly like the central store: this API returns NAMES.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import secrets as secret_store
from .routines_common import _info

router = APIRouter(tags=["routines"])


class ScopedSecretBody(BaseModel):
    key: str
    value: str


def _slug_of(request: Request, slug: str) -> str:
    """Resolve through the registry first: a scoped store may only be written for a routine
    that actually exists, so a typo cannot quietly create an orphan file full of credentials.
    """
    return _info(request, slug).slug


@router.get("/routines/{slug}/secrets")
def list_routine_secrets(request: Request, slug: str) -> dict:
    """This routine's own secret NAMES, plus the central names they shadow — the page shows
    the shadowing explicitly, because a value silently overriding a shared one is exactly
    the confusion the flat namespace used to cause.
    """
    slug = _slug_of(request, slug)
    own = secret_store.routine_secret_keys(slug)
    central = set(secret_store.load_secrets())
    return {"slug": slug, "keys": own,
            "shadowing": sorted(set(own) & central),
            "path": str(secret_store.scoped_path(slug))}


@router.put("/routines/{slug}/secrets")
def put_routine_secret(request: Request, slug: str, body: ScopedSecretBody) -> dict:
    slug = _slug_of(request, slug)
    try:
        secret_store.set_routine_secret(slug, body.key.strip(), body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"cannot write the routine's secrets store: {exc}") from exc
    return {"ok": True, "keys": secret_store.routine_secret_keys(slug)}


@router.delete("/routines/{slug}/secrets/{key}")
def remove_routine_secret(request: Request, slug: str, key: str) -> dict:
    slug = _slug_of(request, slug)
    if not secret_store.delete_routine_secret(slug, key):
        raise HTTPException(404, f"{slug} has no own secret {key!r}")
    return {"ok": True, "keys": secret_store.routine_secret_keys(slug)}
