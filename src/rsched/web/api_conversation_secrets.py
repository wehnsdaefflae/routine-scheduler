"""A CONVERSATION's own secrets — D216 rank 1, the first of the six conversation-settings
gaps the operator decided as one parity campaign ("All six, as one parity campaign").

The engine half has always worked: `engine/capabilities.py` and `engine/secretgate._own_secrets`
read the scoped store by the RUN's slug, and a conversation's slug is its directory name exactly
as a routine's is, so a value in `secrets.d/<conversation-slug>.env` is already injected into its
util calls and already skips the exposure gate. What was missing was any way to PUT one there:
the write surface (`api_routine_secrets.py`) resolves its slug through `routines_common._info`,
which asks `registry.info` about `routines_home` alone — so every conversation slug 404'd on a
store that would have served it.

So this is deliberately a parallel ROUTER over the same store rather than a widening of
`_info` across homes. Widening the lookup would admit conversations to every `/routines/{slug}/*`
surface at once — the schedule, the lane, the recipe-revision spool — most of which mean nothing
for a conversation and none of which were decided here. One gap, one surface.

Values are write-only and the names read is refused to the routine token, exactly as on the
routine side (`app.ROUTINE_TOKEN_DENIED_READS` names both prefixes): otherwise a run could read
every conversation's secret names with the token it already carries.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import secrets as secret_store
from .api_routine_secrets import ScopedSecretBody
from .conversations_common import conversation_info

router = APIRouter(tags=["conversations"])


def _slug_of(request: Request, slug: str) -> str:
    """Resolve through the conversation registry first, so a typo cannot create an orphan
    `secrets.d/*.env` full of credentials for a conversation that does not exist.
    """
    return conversation_info(request, slug).slug


@router.get("/conversations/{slug}/secrets")
def list_conversation_secrets(request: Request, slug: str) -> dict:
    """This conversation's own secret NAMES, plus the central names they shadow."""
    slug = _slug_of(request, slug)
    own = secret_store.routine_secret_keys(slug)
    central = set(secret_store.load_secrets())
    return {"slug": slug, "keys": own,
            "shadowing": sorted(set(own) & central),
            "path": str(secret_store.scoped_path(slug))}


@router.put("/conversations/{slug}/secrets")
def put_conversation_secret(request: Request, slug: str, body: ScopedSecretBody) -> dict:
    slug = _slug_of(request, slug)
    try:
        secret_store.set_routine_secret(slug, body.key.strip(), body.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"cannot write the conversation's secrets store: {exc}") from exc
    return {"ok": True, "keys": secret_store.routine_secret_keys(slug)}


@router.delete("/conversations/{slug}/secrets/{key}")
def remove_conversation_secret(request: Request, slug: str, key: str) -> dict:
    slug = _slug_of(request, slug)
    if not secret_store.delete_routine_secret(slug, key):
        raise HTTPException(404, f"{slug} has no own secret {key!r}")
    return {"ok": True, "keys": secret_store.routine_secret_keys(slug)}
