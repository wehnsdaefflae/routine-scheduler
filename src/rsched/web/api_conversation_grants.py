"""A CONVERSATION's DECLINED access — reading the deny-forever tombstones and clearing one
(F783).

A Decisions-page `deny_forever` writes one uniform `{entity: false}` row into the answering
subject's own routine.yaml (`grants_apply.apply_forever` → `config.routine.record_grants`), and
the engine then refuses that entity and stops asking for it. The mechanism is identical for a
routine and for a conversation — but the only editor that ever REMOVED such a row was the
ROUTINE page's "Declined access" block (`static/views/routine-config-access.js`), which no
conversation has, and `grants` was absent from `CONVERSATION_PATCH_FIELDS` while
`ConversationPatch` forbids extras, so even an API caller was refused. One mis-click denied a
capability to that conversation for the life of the thread, with nothing in code or docs saying
why conversations were excluded — because nothing was: the exclusion was an omission.

Two shapes were available and this is the narrower one:

* A parallel ROUTER rather than a widening of `routines_common._info` across homes — the same
  split D216 rank 1 made for a conversation's own secrets (`api_conversation_secrets.py`).
  Widening the lookup would admit conversations to every `/routines/{slug}/*` surface at once.
* CLEAR ONE ENTITY rather than a wholesale `grants` replace (which is what the routine page's
  PATCH does). The routine page can replace the mapping safely because it is a DRAFT the
  operator accepts, re-read when a decision lands while the page is open (F193). A
  conversation's capabilities panel has no draft and no accept — every card writes on click —
  so a replace there would silently drop whatever row arrived in between, and the rows written
  into that same mapping include `secret:` ALLOW-forever grants (the one class with no native
  switch). Clearing one named row cannot do that.

The safety property of this surface is that it only ever LOOSENS a denial: a cleared row
returns the entity to UNDECIDED (asked on first use / requestable again), exactly as the
routine page's remove button does. It can grant nothing, which is why it needs no capability
cascade — and it is still operator-only, because `app.ROUTINE_TOKEN_MUTATIONS` is empty, so no
run can clear the denial the operator set on it.

A row is matched AS STORED, with the canonical spelling tried as well: `grants:` rows are
canonicalized where a request enters (`entities.parse_entity` expands fs-* paths), while a
hand-edited file may carry any spelling, and a row the operator can SEE has to be a row the
operator can remove.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from ..entities import parse_entity
from ..paths import atomic_write_yaml, read_yaml
from .conversations_common import conversation_info
from .routines_common import signal_config_change

router = APIRouter(tags=["conversation-config"])


class ClearBody(BaseModel):
    # forbid unknown keys, like every other config body here: a silently-dropped stray reads
    # as "cleared" while the tombstone is still in the file.
    model_config = ConfigDict(extra="forbid")

    entity: str


def _raw_grants(conv_dir: Path) -> dict:
    """The `grants:` mapping as routine.yaml literally carries it — never the parsed config.

    What the operator must be able to remove is the row that is THERE, including one a rename
    or a hand edit left in a spelling the validator would drop: a row dropped at load time is
    invisible to the engine but still in the file, and still the thing a later reader sees.
    """
    raw = read_yaml(conv_dir / "routine.yaml", {})
    grants = raw.get("grants") if isinstance(raw, dict) else None
    return dict(grants) if isinstance(grants, dict) else {}


def _declined(conv_dir: Path) -> list[str]:
    """The deny-forever tombstones: the FALSE rows, sorted. A true row is an allow-forever
    grant and is not this surface's business.

    `secret:` rows are INCLUDED here, unlike the routine page's `declinedIds` — which leaves
    them out because that page also carries a *Secret exposure* dial able to return a withheld
    name to "ask on first use". A conversation has no such dial (its own-secrets card is the
    private `secrets.d` store, a different thing), so excluding them would reproduce exactly
    this finding's defect for the secret class: `secretgate.secret_state` reads a
    `secret:NAME: false` row as denied, and nothing would ever clear it.
    """
    return sorted(k for k, v in _raw_grants(conv_dir).items() if v is False)


def _stored_key(grants: dict, entity: str) -> str | None:
    """The key this request names, as the file spells it — or None when there is no such
    DECLINED row. The canonical form is tried too, so a request naming `fs-read:~/x` reaches
    the expanded row a Decisions click wrote.
    """
    wanted = [entity]
    parsed = parse_entity(entity)
    if parsed is not None:
        wanted.append(f"{parsed[0]}:{parsed[1]}")
    for key in wanted:
        if grants.get(key) is False:
            return key
    return None


@router.get("/conversations/{slug}/declined")
def list_declined(request: Request, slug: str) -> dict:
    """This conversation's deny-forever tombstones — what it no longer asks for."""
    info = conversation_info(request, slug)
    return {"slug": info.slug, "declined": _declined(info.cfg.dir)}


@router.post("/conversations/{slug}/declined/clear")
def clear_declined(request: Request, slug: str, body: ClearBody) -> dict:
    """Remove ONE tombstone: the entity returns to undecided (requestable again).

    404 when the conversation has no such declined row — including when the row exists as an
    ALLOW grant, so this route can never be used to take a grant away. `grants` is classified
    LIVE (`configflow.CLASSIFICATION`: the policy is rebuilt and the action schema
    re-projected), so a reply already in flight is TOLD, the same way every other conversation
    config write tells it (F337).
    """
    info = conversation_info(request, slug)
    path = info.cfg.dir / "routine.yaml"
    raw = read_yaml(path, {})
    if not isinstance(raw, dict):
        raise HTTPException(500, f"{path}: expected a mapping at top level")
    grants = _raw_grants(info.cfg.dir)
    key = _stored_key(grants, body.entity.strip())
    if key is None:
        raise HTTPException(404, f"{info.slug} has no declined-access row for "
                                 f"{body.entity.strip()!r}")
    del grants[key]
    raw["grants"] = grants
    atomic_write_yaml(path, raw)
    live = signal_config_change(info, ["grants"], {"grants": grants})
    return {"ok": True, "cleared": key, "declined": _declined(info.cfg.dir),
            **({"told_live_run": True} if live else {})}
