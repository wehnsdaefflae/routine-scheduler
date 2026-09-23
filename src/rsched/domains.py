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

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml

from .ids import now_iso, run_ts
from .paths import atomic_write_json, file_lock, read_json, read_yaml

log = logging.getLogger("rsched.domains")

# The routine.yaml keys a domain may set for its members (D82). Deliberately EXCLUDES the
# per-routine identity and lifecycle keys — slug/name/description/enabled/schedule/workflow/
# playbook/retention/triggers/improve — which say WHICH routine this is and when it runs, so
# sharing them would be meaningless (or destructive). What is left is the policy surface a set of
# related routines genuinely shares: what they may do, what they know, and where they may look.
#
# A key added here needs a CONTROL in `static/components/domainconfig.js`, or a domain can carry
# it while nobody can see or change it — which is what happened to four of these for a release.
# `tests/ui/test_lanes.py::test_every_shareable_key_has_an_editor_block` asserts the two agree,
# so the omission is caught here rather than by someone eventually looking.
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


#: How many previous versions of the domain store to keep beside it.
BACKUP_KEEP = 20


def backups_dir(routines_home: Path) -> Path:
    return Path(routines_home) / ".control" / "domains-history"


def _snapshot(routines_home: Path) -> None:
    """Keep the CURRENT bytes before overwriting them (D140).

    The routines home is not a git repository, so a bad write here had no undo at all: when a
    partial patch wiped a domain's whole shared block (R1745), it came back only because the
    routine that made the call happened to have snapshotted its own payload first. A shared
    config block is the kind of thing several routines inherit and nobody re-derives, so the
    file needs a history of its own rather than the hope of one.

    Best-effort by construction: failing to keep a backup must never stop a save the user
    asked for, and a missing backup dir is not an error — it is simply the first write.
    """
    src = domains_file(routines_home)
    if not src.is_file():
        return
    try:
        d = backups_dir(routines_home)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"domains-{run_ts()}.json").write_bytes(src.read_bytes())
        old = sorted(d.glob("domains-*.json"))[:-BACKUP_KEEP]
        for p in old:
            p.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("domains: could not keep a backup before saving: %s", exc)


def _save(routines_home: Path, data: dict) -> None:
    _snapshot(routines_home)
    atomic_write_json(domains_file(routines_home), data)


@contextmanager
def _exclusive(routines_home: Path) -> Iterator[None]:
    """Hold the store's lock across a read-modify-write.

    `domains.json` is ONE file rewritten whole, and it is written from the web layer's sync
    handlers on FastAPI's threadpool as well as from a conversation's engine process. Two
    overlapping patches each read the store and each write their own version, and one is
    silently lost — which for this file means a domain's shared config block, the thing a
    merge-not-replace patch semantics was already built to protect (R1745/D140).
    """
    with file_lock(domains_file(routines_home).with_suffix(".lock")):
        yield


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
    with _exclusive(routines_home):
        data = load(routines_home)
        data["domains"].append(rec)
        _save(routines_home, data)
    return rec


def update(routines_home: Path, domain_id: str, *, name: str | None = None,
           config: dict | None = None,
           remove: list[str] | None = None) -> dict | None:
    """Patch a domain in place (only the fields passed are touched).

    `config` MERGES over the stored block, key by key: a key the patch does not mention is
    left exactly as it was. It used to REPLACE wholesale, which made every partial patch a
    silent deletion of everything it failed to mention — one such patch dropped a domain's 12
    shared rules, 4 secret grants, 8 budget dials, 3 fs_read_roots and rule_confirm in a single
    call (R1745/D140). The routine PATCH beside it was already field-wise, so one verb meant
    opposite things on the two surfaces, and the routine one is what every caller learns first.

    Removal is therefore SAID rather than implied, in either of two forms:
      * `remove=["grants", "budgets"]` — drop these keys, idempotently;
      * an explicit `None` under a key in `config` — so one payload can set and clear together.

    Both exist because the domain editor PATCHes the whole block on every control and used
    omission AS its removal mechanism: under merge alone, unticking a rule would have become a
    silent no-op. That is why the semantics and the removal form had to land in one change.

    Returns the updated record, or None if no domain has that id.
    """
    with _exclusive(routines_home):
        return _update_locked(routines_home, domain_id, name=name, config=config,
                              remove=remove)


def _update_locked(routines_home: Path, domain_id: str, *, name: str | None,
                   config: dict | None, remove: list[str] | None) -> dict | None:
    """`update`'s body, under the store's flock — see `_exclusive`."""
    data = load(routines_home)
    for d in data["domains"]:
        if d["id"] != domain_id:
            continue
        if name is not None:
            nm = str(name).strip()
            if not nm:
                raise ValueError("domain name cannot be empty")
            d["name"] = nm
        if config is not None or remove:
            merged = dict(d.get("config") or {})
            for key in remove or []:
                merged.pop(str(key), None)          # idempotent: unsetting twice is not an error
            for key, val in (config or {}).items():
                if val is None:
                    merged.pop(str(key), None)
                else:
                    merged[str(key)] = val
            d["config"] = clean_config(merged)
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
    with _exclusive(routines_home):
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
        if domain_of(cfg) == domain_id:
            out.append(rdir.name)
    return out


def domain_of(cfg: Path) -> str:
    """The `domain:` a routine.yaml names, "" when it names none or cannot be read. Memoized
    on the file's stat fingerprint: the dashboard asks for every domain's members on every
    refresh, and parsing 33 routine files six times over was 4 seconds of a request that
    the console fired several times a minute while runs were active (2026-09-12). A saved
    file changes inode+mtime+size, so an edit is never served stale; an unreadable or
    non-mapping file is a NON-member, as before, and stays memoized as one until it changes.
    """
    from .readmodels import memo

    def read() -> str:
        try:
            raw = read_yaml(cfg, {})
        except (OSError, yaml.YAMLError):   # a broken file is not a member
            return ""
        return str(raw.get("domain") or "") if isinstance(raw, dict) else ""

    return memo.memoized(f"domain-of:{cfg}", [cfg], read)
