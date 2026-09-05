"""Routine DOMAINS — the shared-surface axis: what a set of related routines has in common.

A domain is a NAME, a block of routine.yaml keys its members inherit (D82), and a shared
directory their runs can read and write. Those three are ONE object on purpose: they answer the
same question — which routines are close enough to share? — and separating them would dissolve
the argument that makes a domain note approval-free, which is that the store is in its members'
fs roots and nobody else's. A note cannot leave the domain because the domain IS the boundary.

**A routine belongs to at most one domain and it says so in its own routine.yaml** (`domain:`).
Both halves of that matter:

- *At most one* kills a merge that could not be made coherent. Two shared layers over one
  routine have to resolve every key they both set, so whichever rule decides it — first wins,
  last wins, union — what a routine inherits depends on the order rows happen to sit in a JSON
  file. With one layer there is no order to depend on.
- *In its own routine.yaml* is what makes the cardinality a fact of the file rather than a rule
  someone has to enforce across a list. It also puts the setting where every other per-routine
  setting is: which surface this routine shares is an ordinary config decision, user-only like
  every other key there and writable by no run.

That is the opposite of a [lane](lanes.py), which is daemon-owned instance state under
`.control/`, because a lane is about the ORDER several routines fire in and belongs to no single
one of them.

    <routines_home>/.control/domains.json

Shape (single document, atomic-written):

    {"domains": [{"id": "dom-3f5091c4", "name": "FAU",
                  "config": {"permissions": [...], "capabilities": {...}, ...},
                  "created": "2026-07-31T..."}]}

`CONFIG_KEYS` lists what may be shared and, just as deliberately, what may not:
slug/name/description/enabled/schedule/workflow/retention/triggers/improve say WHICH routine
this is and when it runs, so sharing them is meaningless or destructive.

This module owns the shared vocabulary and the file IO. It validates SHAPE only (types, the
config key set); that a shared permission slug names a real library doc is the API layer's job
(it holds the registry). One domain document must never be the place a stale reference takes
the store down.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import yaml

from .ids import now_iso
from .paths import atomic_write_json, read_json, read_yaml

# The routine.yaml keys a domain may set for its members (D82). Deliberately EXCLUDES the
# per-routine identity and lifecycle keys — slug/name/description/enabled/schedule/workflow/
# playbook/retention/triggers/improve — which say WHICH routine this is and when it runs, so
# sharing them would be meaningless (or destructive). What is left is the policy surface a set of
# related routines genuinely shares: what they may do, what they know, and where they may look.
CONFIG_KEYS = ("permissions", "capabilities", "rules", "machines", "tags",
               "models", "connections", "grants", "budgets",
               "fs_read_roots", "fs_write_roots")
# Merged as a UNION with the member's own (the domain is a floor a routine adds to); every other
# key merges per-key with the member's value winning (config/domainconfig.py).
CONFIG_LIST_KEYS = ("permissions", "rules", "machines", "tags",
                    "fs_read_roots", "fs_write_roots")

#: Where the shared stores live, under `.control/` like every other piece of run data.
#:
#: The directory name is FROZEN for one reason: routines address this path IN THEIR OWN MEMORY.
#: One live routine carries "READ /control/group-stores/grp-8bfd2aa6/fau-mark-preferences.md
#: before …" as a standing prevention rule it wrote for itself after an incident; several more
#: name a store id in a ledger. Renaming the directory would mean editing agent-authored memory
#: to keep it true, which is more than a rename. An id is an OPAQUE handle nothing parses: a
#: newly created domain gets `dom-`, a store addressed under any other prefix stays reachable
#: and an id naming both a domain and a lane names two unrelated records.
STORES_DIRNAME = "group-stores"


def new_id() -> str:
    """A stable domain handle — server-generated, never client-supplied."""
    return f"dom-{uuid.uuid4().hex[:8]}"


def domains_file(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / "domains.json"


# ---- the shared store --------------------------------------------------------------------
#
# Every run of a routine in a domain gets its domain's store dir injected into its fs read+write
# roots at boot (engine/runtime seeds RunContext.domain_store_roots) — an INJECTED FS ROOT, not
# a new action kind: the normal file actions and the util sandbox already honor the effective
# roots. Writers are whole-file atomic (the engine's write path) and collisions are
# last-write-wins PER FILE — concurrent members should write per-routine filenames
# (`<slug>-<topic>.md`) and treat shared files as read-mostly. The dir is created lazily at run
# boot; it is run data under .control/, not config — engine-side creation is fine.


def store_dir(routines_home: Path, domain_id: str) -> Path:
    return Path(routines_home) / ".control" / STORES_DIRNAME / domain_id


def member_store_roots(routines_home: Path, domain_id: str,
                       *, create: bool = False) -> list[Path]:
    """The shared-store dir for `domain_id` — a list of ZERO or ONE, because the cardinality
    is one. With `create`, it is made on the spot: the boot-time caller's job, so the root a
    run is told about always exists.

    A list rather than an Optional because every caller splices it into the run's fs roots:
    returning `[]` for "no domain" keeps those call sites a concatenation instead of a branch.
    """
    if not domain_id or not get(routines_home, domain_id):
        return []
    d = store_dir(routines_home, domain_id)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return [d]


def load(routines_home: Path) -> dict:
    """The whole store, normalized: {domains:[…]}. A missing or corrupt file reads as the
    empty store — never raises.
    """
    raw = read_json(domains_file(routines_home))
    if not isinstance(raw, dict):
        raw = {}
    return {"domains": [_normalize(d) for d in raw.get("domains") or []
                        if isinstance(d, dict)]}


def _normalize(d: dict) -> dict:
    return {"id": str(d.get("id") or ""), "name": str(d.get("name") or ""),
            "config": clean_config(d.get("config")),
            "created": str(d.get("created") or "")}


def clean_config(config: object) -> dict:
    """Keep only the known keys, each with the shape routine.yaml uses. SHAPE only — that a
    permission slug names a real library doc, or a machine a real catalog entry, is validated
    where the member's own config is (the API layer, which holds the registry): one domain
    document must never be the place a stale reference takes the whole store down.
    """
    if not isinstance(config, dict):
        return {}
    out: dict = {}
    for key in CONFIG_KEYS:
        if key not in config:
            continue
        val = config[key]
        if key in CONFIG_LIST_KEYS:
            if isinstance(val, list):
                out[key] = [str(v) for v in val if isinstance(v, str) and str(v).strip()]
        elif isinstance(val, dict):
            out[key] = val
    return {k: v for k, v in out.items() if v or v == {}}


def _save(routines_home: Path, data: dict) -> None:
    atomic_write_json(domains_file(routines_home), data)


def list_domains(routines_home: Path) -> list[dict]:
    return load(routines_home)["domains"]


def get(routines_home: Path, domain_id: str) -> dict | None:
    return next((d for d in list_domains(routines_home) if d["id"] == domain_id), None)


def create(routines_home: Path, *, name: str, config: dict | None = None,
           domain_id: str = "") -> dict:
    """Create a domain. `name` must be non-empty. `domain_id` lets a caller supply the id —
    used ONLY by the one-shot migration, which reuses the id the store it inherits is already
    addressed by, so no directory of shared files has to move.
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("domain name is required")
    if domain_id and get(routines_home, domain_id):
        raise ValueError(f"a domain with id {domain_id!r} already exists")
    rec = {"id": domain_id or new_id(), "name": name,
           "config": clean_config(config), "created": now_iso()}
    data = load(routines_home)
    data["domains"].append(rec)
    _save(routines_home, data)
    return rec


def update(routines_home: Path, domain_id: str, *, name: str | None = None,
           config: dict | None = None) -> dict | None:
    """Patch a domain in place (only the fields passed are touched). `config` REPLACES the
    shared block wholesale — dropping a key there returns that setting to each member's own.
    Returns the updated record, or None if no domain has that id.
    """
    data = load(routines_home)
    for d in data["domains"]:
        if d["id"] != domain_id:
            continue
        if name is not None:
            nm = str(name).strip()
            if not nm:
                raise ValueError("domain name cannot be empty")
            d["name"] = nm
        if config is not None:
            d["config"] = clean_config(config)
        _save(routines_home, data)
        return d
    return None


def delete(routines_home: Path, domain_id: str) -> bool:
    """Delete a domain by id. Idempotent; returns True if one was removed.

    The STORE is deliberately left on disk. It holds files members wrote — conventions, shared
    state, notes in flight — and a config record disappearing is not consent to delete data
    nobody asked about. Members still naming the id stop inheriting and stop being handed the
    root; the directory stays until someone removes it knowingly.
    """
    data = load(routines_home)
    before = len(data["domains"])
    data["domains"] = [d for d in data["domains"] if d["id"] != domain_id]
    if len(data["domains"]) == before:
        return False
    _save(routines_home, data)
    return True


def members(routines_home: Path, domain_id: str) -> list[str]:
    """Every routine slug naming `domain_id` in its own routine.yaml, sorted.

    Read from the routines rather than kept as a list on the domain: the membership lives in
    exactly one place, so it cannot disagree with itself; a routine deleted from disk drops out
    of the domain by construction.

    Membership therefore costs a SCAN of every routine.yaml in the home — N chances to hit one
    mid-save, where reading it out of one membership document would offer none. So an unreadable
    file is a NON-MEMBER rather than an exception: the domain page, the record builder and the
    DELETE guard all run through here — and none of them may fail because somebody is halfway
    through saving an unrelated routine.
    """
    out: list[str] = []
    home = Path(routines_home)
    if not domain_id or not home.is_dir():
        return out
    for rdir in sorted(home.iterdir()):
        cfg = rdir / "routine.yaml"
        if rdir.name.startswith(".") or not cfg.is_file():
            continue
        try:
            raw = read_yaml(cfg, {})
        except (OSError, yaml.YAMLError):   # a broken file is not a member
            continue
        if isinstance(raw, dict) and str(raw.get("domain") or "") == domain_id:
            out.append(rdir.name)
    return out
