"""One-shot boot migration to SETTINGS PATTERNS — MIGRATION(expires=2026-10-20).

Runs once at daemon boot (before the library seed sync) and records what it did in
`.control/migrations/settings-patterns.json`; while that record exists it does nothing. In order:

1. **The library.** The sixteen general rules and seventeen permission docs replace the old
   sets (retired files deleted, rewritten ones overwritten — the seed sync alone never
   overwrites), the twelve abstract workflows land beside the two that stay, the nine they
   supersede go, the fourteen patterns land, the templates directory goes, and the Steward
   kit gains its publishing contract. One library commit.
2. **Every routine** follows its pattern (`migrate_settings_patterns_spec.ROUTINES`): its
   conduct settings become the pattern's plus what the routine is seen to use beyond it; its
   domain's live values fold into its own file; gates are written; retired rule tails are cut
   from `main.md`; reminders written against the old `script name=` form are rewritten to the
   form the engine now renders; its stopping conditions become its finish line and its
   recipe's `## Done when` (`migrate_settings_patterns_goal`); a reminder that can never fire
   goes and the routine is told; the retired `output_compression` key goes (lossless
   compression is engine behaviour now). The file is validated after the write and restored from its
   backup if it no longer loads. Judgment calls (unused secrets, a budget, a model) become the
   routine's PENDING changes for the person to accept, never writes.
3. **Every conversation** keeps its own choices, translated: retired rules become their
   successors, retired permission docs and capability keys go, a bare reserved util becomes the
   verb that is reserved now.
4. **Domains** end (`migrate_settings_patterns_domains`): `domains.json` is kept beside itself
   as a retired copy, the five notes that were written to routines outside their store are
   delivered to those routines' inboxes, a store's `steward-hub-tab.txt` gives way to each
   routine's own `hub_tab`, and a retired routine leaves its lane.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

import yaml

from . import libgit
from .ids import now_iso
from .migrate_settings_patterns_domains import end_domains
from .migrate_settings_patterns_goal import convert as convert_goal
from .migrate_settings_patterns_spec import (
    PERMISSION_MAP,
    ROUTINES,
    RULE_MAP,
    SUPERSEDED_WORKFLOWS,
    UTIL_VERBS,
)
from .paths import atomic_write, atomic_write_json, atomic_write_yaml, read_json, read_yaml

log = logging.getLogger("rsched.migrate_settings_patterns")

RECORD = Path(".control") / "migrations" / "settings-patterns.json"
BACKUPS = Path(".control") / "migrations" / "settings-patterns-backup"
CONTRACT = Path(__file__).with_name("migrate_settings_patterns_contract.md")
MESSAGE = "check the changes i recommend."
_PRACTICES = re.compile(r"^## standing practices\s*$", re.IGNORECASE | re.MULTILINE)


def run_migration(server) -> dict:
    """Migrate this instance once. Returns the record (empty when it already ran)."""
    home = Path(server.routines_home)
    record_path = home / RECORD
    if record_path.exists() or not home.is_dir():
        return {}
    from .grants import read_library_requires
    from .patterns import store

    record: dict = {"started": now_iso(),
                    "library": _library(Path(server.libraries_home)), "routines": {},
                    "failed": {}}
    requires = read_library_requires(Path(server.permissions_home))
    patterns = {p["slug"]: p for p in store.list_all(Path(server.libraries_home))}
    for slug, spec in ROUTINES.items():
        try:
            record["routines"][slug] = _routine(home, slug, spec, patterns, requires)
        except Exception as exc:
            log.exception("settings-patterns: %s failed", slug)
            record["failed"][slug] = f"{type(exc).__name__}: {exc}"
    record["conversations"] = _conversations(Path(server.conversations_home), requires)
    record.update(end_domains(home))
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("settings-patterns: migrated %d routine(s), %d failed",
                len(record["routines"]), len(record["failed"]))
    return record


# ---------------------------------------------------------------------------------- library


def _library(lib: Path) -> list[str]:
    from .bootstrap import repo_root

    seed = repo_root() / "library-seed"
    changed: list[str] = []
    for kind, suffix, retired in (("rules", ".md", tuple(RULE_MAP)),
                                  ("permissions", ".md", tuple(PERMISSION_MAP)),
                                  ("workflows", ".py", SUPERSEDED_WORKFLOWS)):
        dest = lib / kind
        dest.mkdir(parents=True, exist_ok=True)
        for slug in retired:
            if (dest / f"{slug}{suffix}").exists():
                (dest / f"{slug}{suffix}").unlink()
                changed.append(f"-{kind}/{slug}{suffix}")
        for src in sorted((seed / kind).glob(f"*{suffix}")):
            target = dest / src.name
            # workflows that stay (converse, general-task) may carry live edits: add only
            if kind == "workflows" and target.exists():
                continue
            if not target.exists() or target.read_bytes() != src.read_bytes():
                shutil.copy(src, target)
                changed.append(f"{kind}/{src.name}")
    (lib / "patterns").mkdir(exist_ok=True)
    for src in sorted((seed / "patterns").glob("*.yaml")):
        if not (lib / "patterns" / src.name).exists():
            shutil.copy(src, lib / "patterns" / src.name)
            changed.append(f"patterns/{src.name}")
    readme = seed / "reminders" / "README.md"
    live = lib / "reminders" / "README.md"
    if not live.is_file() or live.read_bytes() != readme.read_bytes():
        live.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(readme, live)
        changed.append("reminders/README.md")
    if (lib / "templates").is_dir():
        shutil.rmtree(lib / "templates")
        changed.append("-templates/")
    kit = lib / "web" / "steward"
    if kit.is_dir() and CONTRACT.is_file():
        atomic_write(kit / "CONTRACT.md", CONTRACT.read_text(encoding="utf-8"))
        changed.append("web/steward/CONTRACT.md")
    if changed and (lib / ".git").is_dir():
        libgit.commit(lib, "settings patterns: sixteen rules, seventeen permissions, twelve "
                           "workflows, fourteen patterns; templates retired")
    return changed


# ---------------------------------------------------------------------------------- routines


def _routine(home: Path, slug: str, spec: dict, patterns: dict, requires: dict) -> dict:
    from .config import load_routine
    from .grants import floor_capabilities

    rdir = home / slug
    path = rdir / "routine.yaml"
    if not path.is_file():
        return {"skipped": "no routine.yaml"}
    backup = home / BACKUPS / f"{slug}.yaml"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(path, backup)
    raw = read_yaml(path, {})
    pattern = patterns[spec["pattern"]]["settings"]
    raw.pop("domain", None)
    raw["pattern"] = spec["pattern"]

    perms = _edit(pattern["permissions"], spec.get("drop_perms"), spec.get("add_perms"))
    caps = dict(pattern["capabilities"])     # canonical: an empty list is simply absent
    caps["actions"] = _edit(caps.get("actions"), spec.get("drop_actions"),
                            spec.get("add_actions"))
    caps["utils"] = _edit(caps.get("utils"), spec.get("drop_utils"), spec.get("add_utils"))
    caps.update(spec.get("caps_settings") or {})
    floored = floor_capabilities(perms, requires, caps)
    raw["permissions"] = perms
    raw["capabilities"] = {k: (sorted(v) if isinstance(v, list) else v)
                           for k, v in floored.items()}
    raw["rules"] = _edit(pattern["rules"], spec.get("drop_rules"), spec.get("add_rules"))
    raw["shared_reminders"] = list(pattern["reminders"])
    raw["improve"] = bool(spec.get("improve", pattern["improve"]))
    raw["retention"] = {**(raw.get("retention") or {}), "keep_runs": pattern["keep_runs"]}
    raw.pop("output_compression", None)   # lossless minification is engine behaviour now
    if "gate" in spec:
        raw["run_gate"] = spec["gate"]
    else:
        raw.pop("run_gate", None)

    grants = dict(raw.get("grants") or {})
    for name in spec.get("add_grants") or []:
        grants.setdefault(f"secret:{name}", True)   # a decision already on file stands
    raw["grants"] = grants
    raw["fs_read_roots"] = _edit(raw.get("fs_read_roots"), spec.get("drop_read_roots"),
                                 spec.get("add_read_roots"), keep_order=True)
    raw["fs_write_roots"] = _edit(raw.get("fs_write_roots"), spec.get("drop_write_roots"),
                                  spec.get("add_write_roots"), keep_order=True)
    raw["machines"] = _edit(raw.get("machines"), spec.get("drop_machines"), None,
                            keep_order=True)
    raw["triggers"] = [t for t in raw.get("triggers") or []
                       if not (isinstance(t, dict) and t.get("id") in
                               set(spec.get("drop_triggers") or []))]
    models = dict(raw.get("models") or {})
    for kind, name in (spec.get("models") or {}).items():
        models.setdefault(kind, name)
    raw["models"] = models
    raw["budgets"] = {**(raw.get("budgets") or {}), **(spec.get("budgets") or {})}
    if "cron" in spec:
        raw["schedule"] = {**(raw.get("schedule") or {}), "cron": spec["cron"]}
    if spec.get("hub_tab"):
        raw["hub_tab"] = spec["hub_tab"]

    atomic_write_yaml(path, raw)
    cfg, problems = load_routine(rdir)
    if cfg is None:
        shutil.copy(backup, path)
        raise ValueError(f"the migrated routine.yaml does not load: {'; '.join(problems)}")
    touched = [*_strip_practices(rdir), *_rewrite_reminders(rdir)]
    goal_files, goal_notes = convert_goal(rdir, slug, now=now_iso())
    touched += goal_files
    for rel in spec.get("delete_files") or []:
        if (rdir / rel).is_file():
            (rdir / rel).unlink()
            touched.append(f"-{rel}")
    drafted = _draft(home, slug, raw, spec)
    libgit.commit(rdir, f"settings pattern: follows {spec['pattern']}")
    return {"pattern": spec["pattern"], "problems": problems, "files": touched,
            "drafted": drafted, **({"goal": goal_notes} if goal_notes else {})}


def _edit(values, drop, add, *, keep_order: bool = False) -> list:
    gone = set(drop or [])
    out = [v for v in values or [] if v not in gone]
    out += [v for v in add or [] if v not in out]
    return out if keep_order else sorted(dict.fromkeys(out))


def _strip_practices(rdir: Path) -> list[str]:
    """The `## Standing practices` tail was an index DERIVED from `rules:`; the prompt now names
    each held rule itself, so the copy goes (it was stale in 27 of 35 recipes).
    """
    main = rdir / "main.md"
    if not main.is_file():
        return []
    text = main.read_text(encoding="utf-8")
    m = _PRACTICES.search(text)
    if not m:
        return []
    atomic_write(main, text[:m.start()].rstrip() + "\n")
    return ["main.md"]


def _rewrite_reminders(rdir: Path) -> list[str]:
    """Reminders written against the old `script name=` form are rewritten to the form the
    engine renders now; one whose pattern still matches a rendering no action has can never
    fire, so it goes — and the routine is told, in its own words, so it can re-author it.
    """
    from .engine.inbox import file_message
    from .reminder_checks import regex_problem

    path = rdir / "state" / "reminders.json"
    doc = read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("reminders"), list):
        return []
    changed = False
    kept, dead = [], []
    for rem in doc["reminders"]:
        if isinstance(rem, dict) and "script name=" in str(rem.get("regex") or ""):
            rem["regex"] = script_regex(str(rem["regex"]))
            changed = True
        if isinstance(rem, dict) and regex_problem(str(rem.get("regex") or "")):
            dead.append(rem)
        else:
            kept.append(rem)
    if dead:
        doc["reminders"] = kept
        changed = True
        file_message(rdir, "These reminders could never fire — each pattern matches a rendering "
                           "no action has — so they were removed. Re-add any that still matters "
                           "with a pattern over the rendered form:\n"
                           + "\n".join(f"- /{r.get('regex')}/ {r.get('description') or ''}"
                                        f" ({regex_problem(str(r.get('regex') or ''))})"
                                        for r in dead),
                     source="settings-patterns migration", via="report")
    if changed:
        atomic_write_json(path, doc)
    return ["state/reminders.json"] if changed else []


def script_regex(regex: str) -> str:
    r"""`^script name=store args=\\["set-field", "x"` → `^script:store set-field x` — the
    canonical string of a script call now carries its arguments the way a util call's does.
    """
    out = regex.replace("script name=", "script:")
    head, sep, tail = out.partition(r" args=\[")
    if not sep:
        return out
    words = [w.strip().strip('"') for w in tail.split(",")]
    return head + " " + " ".join(w for w in words if w)


def _draft(home: Path, slug: str, raw: dict, spec: dict) -> list[str]:
    from .config.base import DEFAULT_BUDGETS
    from .patterns import drafts

    changes: dict[str, dict] = {}
    drop = set(spec.get("draft_drop_grants") or [])
    grants = raw.get("grants") or {}
    kept = {k: v for k, v in grants.items() if k.removeprefix("secret:") not in drop}
    if kept != grants:
        changes["grants"] = {"value": kept, "reason": "no run in the last thirty used "
                             + ", ".join(sorted(k.removeprefix("secret:")
                                                for k in set(grants) - set(kept)))}
    if spec.get("draft_budgets"):
        changes["budgets"] = {"value": {**DEFAULT_BUDGETS, **raw["budgets"],
                                        **spec["draft_budgets"]},
                              "reason": "the turn budget ends runs before their last stages"}
    if spec.get("draft_models"):
        changes["models"] = {"value": {**raw["models"], **spec["draft_models"]},
                             "reason": "it ran well on this model before a change made on a "
                                       "mistaken premise"}
    if spec.get("draft_drop_write_roots"):
        roots = [r for r in raw["fs_write_roots"] if r not in spec["draft_drop_write_roots"]]
        changes["fs_write_roots"] = {"value": roots,
                                     "reason": "no run in the last thirty wrote there"}
    if spec.get("draft_drop_read_roots"):
        roots = [r for r in raw["fs_read_roots"] if r not in spec["draft_drop_read_roots"]]
        changes["fs_read_roots"] = {"value": roots,
                                    "reason": "a credential store (the console token and every "
                                              "central secret) that the recipe never reads"}
    if changes:
        drafts.write(home, slug, changes=changes, pattern=None, message=MESSAGE,
                     source="migration", reason="what the last thirty runs show")
    return sorted(changes)


# ----------------------------------------------------------------------------- conversations


def _conversations(home: Path, requires: dict) -> int:
    """A conversation keeps its own choices — its rules and permissions are translated, never
    replaced by a pattern (a conversation follows none).
    """
    from .grants import floor_capabilities

    n = 0
    for path in sorted(home.glob("*/routine.yaml")) if home.is_dir() else []:
        try:
            raw = read_yaml(path, {})
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(raw, dict):
            continue
        before = json.dumps(raw, sort_keys=True, default=str)
        raw.pop("domain", None)
        raw.pop("output_compression", None)   # engine behaviour now, not a setting
        raw["rules"] = list(dict.fromkeys(
            r2 for r in raw.get("rules") or [] for r2 in RULE_MAP.get(r, [r])))
        raw["permissions"] = list(dict.fromkeys(
            p2 for p in raw.get("permissions") or [] for p2 in PERMISSION_MAP.get(p, [p])))
        caps = {k: v for k, v in (raw.get("capabilities") or {}).items()
                if k not in ("util_tags", "workflows")}
        caps["actions"] = [a for a in caps.get("actions") or []
                           if a not in ("memory_read", "memory_write", "script", "detach")]
        caps["utils"] = list(dict.fromkeys(
            u2 for u in caps.get("utils") or [] for u2 in UTIL_VERBS.get(u, [u])))
        raw["capabilities"] = floor_capabilities(raw["permissions"], requires, caps)
        # a model role naming nothing was never valid; it goes rather than being read around
        raw["models"] = {k: v for k, v in (raw.get("models") or {}).items()
                         if isinstance(v, str) and v.strip()}
        if json.dumps(raw, sort_keys=True, default=str) != before:
            atomic_write_yaml(path, raw)
            n += 1
    return n
