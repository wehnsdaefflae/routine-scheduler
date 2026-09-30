"""Persist a FOREVER grant decision into routine.yaml — the web half of the four-state
grant model (engine/requests.py is the run half; entities.py the vocabulary). Called
exactly when the user clicks allow-forever / deny-forever on an access request
(api_questions.answer): every write here records an explicit user decision, through the
same raise-then-floor cascade the routine page's permission editor runs. The ENGINE
never writes this file — a run's once-grants live in memory on its RunContext.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from .. import entities
from ..config.routine import record_grants
from ..paths import atomic_write_yaml, read_yaml


def resolve_account(provider: str) -> str:
    """The single connected account for a provider. A connection entity is requestable
    only when exactly one account exists (availability._availability enforces it run-side);
    this guards the race where accounts changed between the ask and the click.
    """
    from ..oauth import store as oauth_store

    accounts = sorted({str(c.get("account")) for c in oauth_store.list_connections()
                       if c.get("provider") == provider})
    if len(accounts) != 1:
        raise HTTPException(409, f"{provider}: expected exactly one connected account, "
                                 f"found {len(accounts)} — bind the account on the "
                                 "routine page instead")
    return accounts[0]


def _covering_docs(server, cls: str, name: str) -> list[str]:
    """The permission doc(s) whose `requires:` covers this capability entity — one is
    enough to carry it through the floor. Falls back to the canonical source for gated
    kinds the library predates (the same fallback floor_capabilities honors).
    """
    from ..grants import _DEFAULT_KIND_SOURCE, split_util_verb
    from ..readmodels import library_reads

    lib = library_reads.requires(server.permissions_home)
    bare, verb = split_util_verb(name)

    def covers(entry: str) -> bool:
        # A bare request is covered by any reservation of the util; a verb request only by
        # one reserving the whole util or exactly that verb — a verb nobody reserves is open
        # already; granting a sibling verb in its place would widen what was asked for.
        e_bare, e_verb = split_util_verb(entry)
        return e_bare == bare and (not verb or not e_verb or e_verb == verb)

    docs = []
    for slug, req in lib.items():
        if ((cls == "action" and name in (req.get("actions") or []))
                or (cls == "util" and any(covers(e) for e in req.get("utils") or []))):
            docs.append(slug)
    if not docs and cls == "action":
        docs = [_DEFAULT_KIND_SOURCE.get(name, "util-authoring")]
    return sorted(docs)[:1]


def _apply_capability(server, raw: dict, cls: str, name: str) -> None:
    """Fold one capability entity into the two permission layers, exactly as the routine
    page's save does: activate a covering conduct doc, raise the capabilities mapping,
    then floor it — so the saved mapping can never contradict the held permissions.
    """
    from ..grants import (
        REMINDER_LEVELS,
        RUN_HISTORY_LEVELS,
        capabilities_for,
        floor_capabilities,
        normalize_capabilities,
        split_util_verb,
    )
    from ..readmodels import library_reads

    base, _ = normalize_capabilities(raw.get("capabilities"))
    # Run-history depth and the reminder stores are SETTINGS: no doc covers them and none is
    # needed, so a forever-decision writes the setting itself.
    if cls in ("runs", "reminders"):
        levels = RUN_HISTORY_LEVELS if cls == "runs" else REMINDER_LEVELS
        current = base.get(cls) or "none"
        if levels.index(name) > levels.index(current):
            base[cls] = name
        raw["capabilities"] = base
        return
    docs = _covering_docs(server, cls, name)
    if not docs:
        raise HTTPException(409, f"no permission doc in the library covers {cls}:{name} "
                                 "— add a `requires:` entry to a conduct doc first "
                                 "(Library → Permissions)")
    active = [str(p) for p in raw.get("permissions") or []]
    active += [d for d in docs if d not in active]
    lib = library_reads.requires(server.permissions_home)
    if cls == "action":
        base["actions"] = [*base.get("actions", []), name]
    elif cls == "util":
        # Grant at the grain the covering doc reserves: a doc that reserves only `gmail:send`
        # is satisfied by that verb entry — a bare `gmail` would not survive its floor.
        bare = split_util_verb(name)[0]
        reserved = [u for u in lib.get(docs[0], {}).get("utils") or []
                    if split_util_verb(u)[0] == bare]
        wanted = [name] if name in reserved or bare in reserved else reserved
        base["utils"] = [*base.get("utils", []), *wanted]
    raw["permissions"] = active
    if cls == "util":
        # D97=B (user decision 2026-08-20, F360): a forever-grant for ONE util activates
        # the covering conduct doc but floors capabilities to base + the NAMED util only —
        # the doc's sibling utils stay OFF, each requestable separately.
        # The capabilities_for raise cascade would grant the whole class: one
        # `allow_forever util:signal` click handed sprind AND uncensored-model-radar all
        # four personal messengers + chat/messaging tags (commits 30e1894, df2b944).
        # The routine page's full permission save keeps the raise — that surface SHOWS
        # the whole class before writing it.
        raw["capabilities"] = floor_capabilities(active, lib, base)
    else:
        raised = capabilities_for(active, lib, base)
        # Access decisions are not full permission-editor saves. Previously activated
        # conduct docs may cover only a narrowly granted util, not their whole class.
        raised["utils"] = base.get("utils", [])
        raw["capabilities"] = floor_capabilities(active, lib, raised)


def apply_forever(server, routine_dir: Path, ids: list[str],
                  decision: str) -> dict[str, str]:
    """Write one forever-decision over `ids` into routine.yaml. Returns extra fields the
    answer file should carry (a connection grant's resolved account). deny_forever is one
    uniform tombstone row per entity (record_grants — the one writer for `grants:` rows,
    which also carries allow-forever for secret:*, the class with no native switch);
    every other allow_forever lands in the entity's NATIVE key.
    """
    if decision == "deny_forever":
        record_grants(routine_dir, dict.fromkeys(ids, False))
        return {}
    path = routine_dir / "routine.yaml"
    raw = read_yaml(path, {})
    if not isinstance(raw, dict):
        raise HTTPException(500, f"{path}: expected a mapping at top level")
    extra: dict[str, str] = {}
    grant_rows: dict[str, bool] = {}
    for eid in ids:
        cls, _, name = eid.partition(":")
        if cls in entities.NO_FOREVER_CLASSES:
            raise HTTPException(400, "recreating a deleted util is granted per run "
                                     "only (allow now) — a fresh deletion must always "
                                     "outrank an old grant")
        if cls == "secret":
            grant_rows[eid] = True
        elif cls == "connection":
            account = resolve_account(name)
            raw["connections"] = {**(raw.get("connections") or {}), name: account}
            extra["account"] = account
        elif cls == "machine":
            bound = list(raw.get("machines") or [])
            if name not in bound:
                raw["machines"] = [*bound, name]
        elif cls in ("fs-read", "fs-write"):
            key = "fs_read_roots" if cls == "fs-read" else "fs_write_roots"
            roots = [str(r) for r in raw.get(key) or []]
            if name not in roots:
                raw[key] = [*roots, name]
        else:   # action / util — the two-layer cascade; runs / reminders — a setting
            _apply_capability(server, raw, cls, name)
    atomic_write_yaml(path, raw)
    if grant_rows:
        record_grants(routine_dir, grant_rows)   # the ONE writer for grants: rows
    return extra
