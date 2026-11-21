"""A routine's or a conversation's OWN secrets (D103, operator decision 2026-08-26 — R497;
D216 rank 1 for conversations): the write surface for `secrets.d/<slug>.env`, the per-slug half
of the two-scope store (rsched/secrets.py). One router serves both homes, because the store is
keyed by the RUN's slug and a conversation's slug is its directory name exactly as a routine's
is — `engine/capabilities.py` and `engine/secretgate._own_secrets` read it the same way for both.

Why it is not just a naming convention in the central store: `SFTP_USER` means a different
thing to every routine that has one, and one flat namespace forces them either to collide or
to be spelled `EYESTAB_SFTP_USER` by discipline no mechanism enforces. A scoped secret is
owned by its routine — implicitly exposed to its runs, invisible to every other routine, and
shadowing a central value of the same name for that routine only.

Because ownership IS the grant, there is no `secret:<NAME>` decision here and the exposure
gate skips these names entirely (`engine/secretgate._own_secrets`). The declared-only invariant
still holds: a util receives the var only if its `secrets:` header declares it.

Each home resolves its slug through its OWN registry (`routines_common._info`,
`conversations_common.conversation_info`) before the store is touched, so a typo cannot quietly
create an orphan file full of credentials — and admitting conversations here never widens
`_info` across homes, which would open every `/routines/{slug}/*` surface to them at once.

Values are write-only, exactly like the central store: this API returns NAMES — and those to
the operator alone. The routine token rides into every util that declares RSCHED_API_TOKEN, so
the names read is refused to it like the central one (`app.ROUTINE_TOKEN_DENIED_READS` names
both prefixes); otherwise every routine's secret names would be one GET away from every other.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import secrets as secret_store
from .conversations_common import conversation_info
from .routines_common import _info

router = APIRouter(tags=["secrets"])


class ScopedSecretBody(BaseModel):
    key: str
    value: str


def _names(slug: str) -> dict:
    """The slug's own secret NAMES, plus the central names they shadow — the page shows the
    shadowing explicitly, because a value silently overriding a shared one is exactly the
    confusion the flat namespace used to cause.
    """
    own = secret_store.routine_secret_keys(slug)
    central = set(secret_store.load_secrets())
    return {"slug": slug, "keys": own,
            "shadowing": sorted(set(own) & central),
            "path": str(secret_store.scoped_path(slug))}


def _put(slug: str, body: ScopedSecretBody, home: str) -> dict:
    try:
        secret_store.set_routine_secret(slug, body.key.strip(), body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"cannot write the {home}'s secrets store: {exc}") from exc
    return {"ok": True, "keys": secret_store.routine_secret_keys(slug)}


def _delete(slug: str, key: str) -> dict:
    if not secret_store.delete_routine_secret(slug, key):
        raise HTTPException(404, f"{slug} has no own secret {key!r}")
    return {"ok": True, "keys": secret_store.routine_secret_keys(slug)}


@router.get("/routines/{slug}/secrets")
def list_routine_secrets(request: Request, slug: str) -> dict:
    return _names(_info(request, slug).slug)


@router.put("/routines/{slug}/secrets")
def put_routine_secret(request: Request, slug: str, body: ScopedSecretBody) -> dict:
    return _put(_info(request, slug).slug, body, "routine")


@router.delete("/routines/{slug}/secrets/{key}")
def remove_routine_secret(request: Request, slug: str, key: str) -> dict:
    return _delete(_info(request, slug).slug, key)


@router.get("/conversations/{slug}/secrets")
def list_conversation_secrets(request: Request, slug: str) -> dict:
    return _names(conversation_info(request, slug).slug)


@router.put("/conversations/{slug}/secrets")
def put_conversation_secret(request: Request, slug: str, body: ScopedSecretBody) -> dict:
    return _put(conversation_info(request, slug).slug, body, "conversation")


@router.delete("/conversations/{slug}/secrets/{key}")
def remove_conversation_secret(request: Request, slug: str, key: str) -> dict:
    return _delete(conversation_info(request, slug).slug, key)
