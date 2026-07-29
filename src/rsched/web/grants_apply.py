"""Persist a FOREVER grant decision into routine.yaml — the web half of the four-state
grant model (engine/requests.py is the run half; entities.py the vocabulary). Called
exactly when the user clicks allow-forever / deny-forever on an access request
(api_questions.answer): every write here records an explicit user decision, through the
same raise-then-floor cascade the routine page's permission editor runs. The ENGINE
never writes this file — a run's once-grants live in memory on its RunContext.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import HTTPException

from .. import entities
from ..config.routine import record_grants
from ..paths import atomic_write


def resolve_account(provider: str) -> str:
    """The single connected account for a provider. A connection entity is requestable
    only when exactly one account exists (requests._availability enforces it run-side);
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
    from ..grants import _DEFAULT_KIND_SOURCE, read_library_requires

    lib = read_library_requires(server.permissions_home)
    docs = []
    for slug, req in lib.items():
        if ((cls == "action" and name in (req.get("actions") or []))
                or (cls == "util" and name in (req.get("utils") or []))
                or (cls == "runs" and req.get("runs"))
                or (cls == "workflows" and req.get("workflows"))):
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
        RUN_HISTORY_LEVELS,
        capabilities_for,
        floor_capabilities,
        normalize_capabilities,
        read_library_requires,
    )

    docs = _covering_docs(server, cls, name)
    if not docs:
        raise HTTPException(409, f"no permission doc in the library covers {cls}:{name} "
                                 "— add a `requires:` entry to a conduct doc first "
                                 "(Library → Permissions)")
    active = [str(p) for p in raw.get("permissions") or []]
    active += [d for d in docs if d not in active]
    base, _ = normalize_capabilities(raw.get("capabilities"))
    if cls == "action":
        base["actions"] = [*base.get("actions", []), name]
    elif cls == "util":
        base["utils"] = [*base.get("utils", []), name]
    elif cls == "runs":
        current = base.get("runs") or "none"
        if RUN_HISTORY_LEVELS.index(name) > RUN_HISTORY_LEVELS.index(current):
            base["runs"] = name
    elif cls == "workflows":
        base["workflows"] = "generate"
    lib = read_library_requires(server.permissions_home)
    raw["permissions"] = active
    raw["capabilities"] = floor_capabilities(active, lib,
                                             capabilities_for(active, lib, base))


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
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
        else:   # action / util / runs / workflows — the two-layer cascade
            _apply_capability(server, raw, cls, name)
    atomic_write(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    if grant_rows:
        record_grants(routine_dir, grant_rows)   # the ONE writer for grants: rows
    return extra
