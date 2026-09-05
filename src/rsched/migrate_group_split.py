"""MIGRATION(expires=2026-12-01): split every `group` into a LANE and a DOMAIN.

A `group` record in `.control/groups.json` is four things at once: a fire lane (cron + order +
on_failure), a shared config layer (D82), a TRUST BOUNDARY (the store injected into members' fs
roots, which domain notes rest on entirely) and a semantic bundle. The temporal axis DEMANDS
exclusivity — a routine in two scheduled groups fires twice — so the strictest axis quantizes
the other three and the dimensions with nowhere of their own to live end up hand-encoded in
NAMES.

The live document is exactly that shape: 14 groups, 31 memberships, ZERO routines in more than
one group. Four `Instance ·` groups carry byte-identical 294-char config blocks and two
`Professional ·` ones do the same — D82's own failure mode, recreated by the cadence split.

This is the one-shot that converts `.control/groups.json` into `.control/lanes.json` plus
`.control/domains.json` and writes `domain:` into each member's routine.yaml.

Three rules decide what becomes what:

1. **Identical config blocks become ONE domain.** Clustering is by the exact content of the
   block AFTER `domains.clean_config`, which is what every read of the store returns, so the
   four `Instance ·` copies collapse to one and two blocks differing only in a key the store
   drops collapse with them. Nothing has to be guessed about intent.
2. **A domain inherits the id of whichever contributing group has files in its store**, so no
   directory of shared files moves. Routines address these paths in their OWN memory — one
   carries "READ /control/group-stores/grp-8bfd2aa6/…" as a standing prevention rule — so a
   moved store would silently falsify agent-authored notes.
3. **Nothing that holds anything is dropped.** A group with no members and no config is dropped
   outright. A group with members and NO CRON becomes a TAG on its members rather than a lane
   that could never fire, so the user's own categorization survives on the axis meant to carry
   it. That branch reads the CLOCK alone: a group with members, a config block and no cron is
   BOTH — its block still joins a domain and its members are stamped `domain:` as well as
   tagged.

Two more things it does, neither of them a rule about what becomes what:

- **It enforces the lane's own invariant.** A slug two scheduled groups both hold keeps the
  FIRST lane and is dropped from the later one, named in `dropped` so the boot log says which.
  No live routine is in that position, but the store refuses such a document
  (`lanes._claimed_elsewhere`): a conversion that can emit one converges today and locks a lane
  tomorrow.
- **It reads the source defensively.** This runs on the daemon's upgrade boot, before the app
  exists and with nothing catching what it raises, so a record it cannot read is SKIPPED and
  NAMED in `dropped` rather than thrown on (`_read_groups`). Every store loader here has that
  same floor: one document must never be the place a stale reference takes the instance down.

**An id names neither a kind of object nor a store.** A migrated lane keeps its group's `grp-`
id exactly as the domain does, for the same reason: an id is an opaque handle nothing parses.
Five ids on the live instance therefore name a LANE and a DOMAIN both; every migrated object of
either kind carries the `grp-` prefix, while anything created afterwards gets `lane-` or `dom-`
(`lanes.new_id`, `domains.new_id`). Which store an id was read from is what says what it is —
never the prefix.

It is idempotent: it converts only while `groups.json` exists and removes that file at the end,
so a second boot is a no-op.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import yaml

from . import domains, lanes
from .ids import now_iso
from .paths import atomic_write_json, atomic_write_yaml, read_json, read_yaml

log = logging.getLogger("rsched.migrate")


def _config_key(config: dict) -> str:
    return json.dumps(config, sort_keys=True)


def _slugify(name: str) -> str:
    """A group name to a tag: 'On demand' -> 'on-demand'. Tags are lowercase kebab here."""
    out = "".join(c if c.isalnum() else "-" for c in name.lower())
    return "-".join(p for p in out.split("-") if p)


def _store_file_count(routines_home: Path, gid: str) -> int:
    d = domains.store_dir(routines_home, gid)
    return sum(1 for p in d.rglob("*") if p.is_file()) if d.is_dir() else 0


def _member_slugs(members: object) -> tuple[list[str], int]:
    """The member slugs of one source group, plus a count of the entries that held none.

    The coercion `lanes._clean_members` applies to the store's own document: a record with a
    non-blank slug, in order, deduplicated by slug. A membership list that is not a list at all
    counts as one unreadable entry, so the loss is reported rather than passed over.
    """
    if members is None:
        return [], 0
    if not isinstance(members, list):
        return [], 1
    out: list[str] = []
    unreadable = 0
    for m in members:
        slug = str(m.get("slug") or "").strip() if isinstance(m, dict) else ""
        if not slug:
            unreadable += 1
        elif slug not in out:
            out.append(slug)
    return out, unreadable


def _read_groups(routines_home: Path) -> tuple[str, list[dict], list[tuple[str, str, str]]]:
    """The source document coerced to the shape the conversion reads: the instance-wide failure
    default, one normalized record per group, and every record that could not be read.

    Nothing here raises. This is the boot path — a hand-edited `groups.json` must not become a
    daemon that refuses to start — so a record that is not an object, a record with no id, a
    membership entry that is a bare string: each is SKIPPED and NAMED, exactly as `lanes.load`
    and `domains.load` treat their own documents. The live file is written by a normalizing
    save, so anything skipped here was edited by hand.
    """
    raw = read_json(Path(routines_home) / ".control" / "groups.json")
    doc: dict = raw if isinstance(raw, dict) else {}
    rows = doc.get("groups")
    groups: list[dict] = []
    skipped: list[tuple[str, str, str]] = []
    for i, g in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(g, dict):
            skipped.append(("", f"record #{i}", "not a group object"))
            continue
        gid = str(g.get("id") or "").strip()
        name = str(g.get("name") or "")
        if not gid:
            skipped.append(("", name or f"record #{i}", "no id"))
            continue
        member_slugs, unreadable = _member_slugs(g.get("members"))
        if unreadable:
            noun = "entry" if unreadable == 1 else "entries"
            skipped.append((gid, name, f"{unreadable} membership {noun} named no routine"))
        config = domains.clean_config(g.get("config"))
        if g.get("config") and not config:
            skipped.append((gid, name, "its config block holds nothing a domain may share"))
        cron = g.get("cron")
        groups.append({"id": gid, "name": name, "members": member_slugs, "config": config,
                       "cron": cron.strip() if isinstance(cron, str) else "",
                       "tz": str(g.get("tz") or ""), "paused": bool(g.get("paused")),
                       "on_failure": g.get("on_failure"),
                       "created": str(g.get("created") or "")})
    default = doc.get("default_on_failure")
    return (default if default in lanes.ON_FAILURE else lanes.DEFAULT_ON_FAILURE,
            groups, skipped)


def plan(routines_home: Path) -> dict:
    """What the conversion WOULD do, computed without writing anything.

    Split out so the result can be read before it is applied and so the tests assert on a
    structure rather than on a directory tree.
    """
    default_on_failure, groups, dropped = _read_groups(routines_home)

    # --- domains: one per DISTINCT config block, as the store reads it back -----------------
    by_config: dict[str, list[dict]] = {}
    for g in groups:
        if g["config"]:
            by_config.setdefault(_config_key(g["config"]), []).append(g)

    stamp = now_iso()
    domain_recs: list[dict] = []
    domain_of: dict[str, str] = {}
    for cluster in by_config.values():
        # the id of whichever contributor actually has files, so no store moves
        with_files = sorted(cluster, key=lambda g: -_store_file_count(routines_home, g["id"]))
        did = with_files[0]["id"]
        # the name is the common prefix of the contributing group names ("Instance · Weekly ·
        # Research" + "Instance · Nightly · Maintenance" -> "Instance"), which is precisely the
        # dimension the source document has nowhere of its own to put
        names = [g["name"] for g in cluster]
        name = (_common_prefix(names) if len(names) > 1 else names[0]) or did
        # a domain is as old as the oldest record it inherits from; where the source carries no
        # date, this boot's — so every domain the store holds is stamped like the ones the web
        # creates and the page has a real date to sort on
        born = sorted(g["created"] for g in cluster if g["created"])
        domain_recs.append({"id": did, "name": name, "config": cluster[0]["config"],
                            "created": born[0] if born else stamp,
                            "from": [g["id"] for g in cluster]})
        for g in cluster:
            for slug in g["members"]:
                domain_of[slug] = did

    # --- lanes: every group with members AND a cron ----------------------------------------
    lane_recs: list[dict] = []
    tags: dict[str, list[str]] = {}
    claimed: dict[str, str] = {}          # slug -> the lane that already holds it
    for g in groups:
        name, member_slugs = g["name"], g["members"]
        if not member_slugs:
            dropped.append((g["id"], name, "no members"))
            continue
        if not g["cron"]:
            # nothing fires it, so it is not a lane — it is a name for a set of routines
            if tag := _slugify(name):
                for s in member_slugs:
                    tags.setdefault(s, []).append(tag)
                dropped.append((g["id"], name, "no cron — became a tag on its members"))
            else:
                dropped.append((g["id"], name, "no cron and no name to tag its members with"))
            continue
        # A routine belongs to AT MOST ONE lane and the store enforces it
        # (`lanes._claimed_elsewhere`), so the first lane in file order keeps a contested slug
        # and the later one loses it. Enforced here rather than assumed of the source: a
        # lanes.json the store would have refused to WRITE is a lane it then refuses to EDIT,
        # so the conversion converges and the next member change to that lane cannot be saved.
        keep = [s for s in member_slugs if s not in claimed]
        if taken := [s for s in member_slugs if s in claimed]:
            where = "; ".join(f"{s} stays in {claimed[s]!r}" for s in taken)
            tail = "" if keep else " — nothing left, so no lane"
            dropped.append((g["id"], name,
                            f"a routine belongs to at most one lane: {where}{tail}"))
        if not keep:
            continue
        claimed.update(dict.fromkeys(keep, name))
        # The lane keeps the group's id, exactly as the domain above does and for the same
        # reason: an id is an opaque handle nothing parses. So one id can name a lane AND a
        # domain while no prefix says which store an object lives in (module docstring).
        lane_recs.append({"id": g["id"], "name": name,
                          "members": [{"slug": s} for s in keep],
                          "on_failure": g["on_failure"], "cron": g["cron"],
                          "tz": g["tz"], "paused": g["paused"], "created": g["created"]})
    return {"default_on_failure": default_on_failure,
            "lanes": lane_recs, "domains": domain_recs, "domain_of": domain_of,
            "tags": tags, "dropped": dropped}


def _common_prefix(names: list[str]) -> str:
    """The shared leading segment of several ' · '-separated names, else the first name."""
    parts = [n.split(" · ") for n in names]
    shared: list[str] = []
    for i in range(min(len(p) for p in parts)):
        seg = parts[0][i]
        if all(p[i] == seg for p in parts):
            shared.append(seg)
        else:
            break
    return " · ".join(shared) if shared else names[0]


def _converge(home: Path) -> int:
    """Bring an ALREADY-split instance up to the document the stores would write themselves.

    An instance can be split without having been through the conversion above as it now stands:
    the daemon boots from a bind-mounted checkout, so a restart while this module was being
    written ran an earlier draft of it against the live stores. That draft wrote domain records
    with no `created` stamp and with each group's config block verbatim rather than through
    `domains.clean_config`, so those records read back one way and would have been written
    another — the file says something the store never surfaces, and every domain born since
    carries a timestamp the migrated ones lack.

    Repairing that is this function, not a hand-edit, for the reason every migration here
    exists: the fix has to be the same on every instance, and it has to be able to run twice.
    It is idempotent by construction — a record already carrying both comes out unchanged and
    is not rewritten — so a fresh instance reaches it, finds nothing, and writes nothing.
    """
    path = domains.domains_file(home)
    if not path.is_file():
        return 0
    raw = read_json(path)
    records = (raw or {}).get("domains") if isinstance(raw, dict) else None
    if not isinstance(records, list):
        return 0
    stamp, changed = now_iso(), []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        cleaned = domains.clean_config(rec.get("config"))
        if not rec.get("created"):
            rec["created"] = stamp
            changed.append(f"{rec.get('id')}: created stamped")
        if cleaned != rec.get("config"):
            dropped = sorted(set(rec.get("config") or {}) - set(cleaned))
            rec["config"] = cleaned
            changed.append(f"{rec.get('id')}: dropped unshareable {', '.join(dropped)}")
    if not changed:
        return 0
    atomic_write_json(path, {"domains": records})
    log.warning("group split: converged %d domain record(s) — %s", len(changed),
                "; ".join(changed))
    return len(changed)


def run(routines_home: Path) -> int:
    """Convert, write both stores, stamp `domain:` into each member, retire groups.json.

    Returns the number of objects written. A missing groups.json means the conversion already
    happened (or this instance never had groups); the stale chain-run directory is cleaned
    either way, and `_converge` finishes any record an earlier draft of this module wrote.
    """
    home = Path(routines_home)
    # An in-flight chain record lives under `.control/lane-runs/` with an `lr-` handle. The
    # records in `.control/group-runs/` are EPHEMERAL state the daemon has already drained —
    # it finishes every running chain before it restarts — so that directory is DELETED rather
    # than converted: a record nothing will ever read again is not state worth carrying over.
    stale_runs = home / ".control" / "group-runs"
    if stale_runs.is_dir():
        shutil.rmtree(stale_runs, ignore_errors=True)
    src = home / ".control" / "groups.json"
    if not src.is_file():
        return _converge(home)
    p = plan(home)

    atomic_write_json(lanes.lanes_file(home),
                      {"default_on_failure": p["default_on_failure"], "lanes": p["lanes"]})
    # `from` is the conversion's own bookkeeping — which groups contributed the block — and no
    # part of a domain record, so it is dropped on the way to disk.
    atomic_write_json(domains.domains_file(home),
                      {"domains": [{k: v for k, v in d.items() if k != "from"}
                                   for d in p["domains"]]})

    touched = 0
    for slug in sorted(set(p["domain_of"]) | set(p["tags"])):
        cfg = home / slug / "routine.yaml"
        if not cfg.is_file():
            log.warning("group split: %s is a member of a group but has no routine.yaml", slug)
            continue
        try:
            raw = read_yaml(cfg, {})
        except (OSError, yaml.YAMLError) as exc:
            log.warning("group split: %s unreadable — %s", slug, exc)
            continue
        if not isinstance(raw, dict):
            continue
        before = dict(raw)
        if did := p["domain_of"].get(slug):
            raw["domain"] = did
        if added := [t for t in p["tags"].get(slug, [])
                     if t not in (raw.get("tags") or [])]:
            raw["tags"] = [*(raw.get("tags") or []), *added]
        if raw == before:
            continue
        atomic_write_yaml(cfg, raw)
        touched += 1

    src.unlink()
    log.warning("group split: %d lanes, %d domains, %d routines stamped; dropped %s",
                len(p["lanes"]), len(p["domains"]), touched,
                "; ".join(f"{name} ({why})" for _, name, why in p["dropped"]) or "nothing")
    return len(p["lanes"]) + len(p["domains"]) + touched
