"""Permission grants — the machine-enforced side of the permission set.

A **permission** is a library markdown doc (`<libraries_home>/permissions/<slug>.md`) whose
frontmatter carries a `grants:` key. A routine holds a permission when its routine.yaml
`permissions:` list names it — an edit only the USER makes (the web UI blocks it while a
run is active, and a routine can never write its own routine.yaml grants into effect:
grants are read ONLY from the LIBRARY copy, never from anything under the routine dir).
Traits — the prose practice set — carry no grants and are invisible to this module.

Schema (permission frontmatter):

    grants:
      actions: [write_util]            # action kinds; only GATED_KINDS are enforced
      utils: [discord]                 # utils reserved for routines carrying this grant
      confirm: true | false | revisions-only   # write_util approval policy
      runs: last | all                 # read access to previous runs under runs/

Enforced per turn by `engine.actions.validate_action` (a run's allowed action kinds are
workflow `tools:` ∩ (base ∪ union of active grants)) plus path gates for runs/ and the
routine's recipe/config files — a rejected call is corrected inside the schema-retry cycle
and never becomes a turn. Base kinds — util, read_file, write_file, llm, spawn, … — stay
ungated.

Recipe writes are NOT a permission: a run never edits its own recipe (main.md, steps/,
traits/, instruction.md) or its own routine.yaml — recipe improvement is the
routine-improver meta routine's job, and config is the user's. The single override is the
user-granted resource `fs_write_roots`: when a write root covers the routine's own dir
(the improver's case), the engine unlocks these paths for that run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import frontmatter
import yaml

from .engine.actions import KINDS
from .ids import is_slug

GATED_KINDS = ("write_util", "memory_read", "memory_write")
# When no library permission grants a gated kind (e.g. the library predates it), denials
# still name the permission that canonically carries it.
_DEFAULT_KIND_SOURCE = {"write_util": "util-authoring",
                        "memory_read": "memory", "memory_write": "memory"}
_DEFAULT_RUNS_SOURCE = ("run-history", "run-history-full")
# write_util approval policy, least → most permissive. The raw `confirm:` vocabulary maps
# to it: true → "always" (user approves create AND revise), "revisions-only" → "creations"
# (revisions are autonomous once the selftest passes; NEW utils still ask), false → "never".
CONFIRM_LEVELS = ("always", "creations", "never")
_RAW_CONFIRM = {True: "always", "revisions-only": "creations", False: "never"}
# runs: access to previous runs, none → last (only the previous run) → all
RUN_HISTORY_LEVELS = ("none", "last", "all")
# The routine's own recipe + config files — never writable by the owning run unless a
# user-granted fs_write_root covers the routine dir (see the module docstring). traits/
# holds the routine's adapted practice copies; steps/ + main.md the materialized workflow;
# routine.yaml is the user's config (permissions held, budgets, roots).
RECIPE_PREFIXES = ("main.md", "instruction.md", "steps/", "traits/", "routine.yaml")


def normalize_grants(raw: object) -> tuple[dict, list[str]]:
    """Validate + normalize one permission's raw `grants:` value. Returns (grants, problems);
    invalid parts are dropped and reported, so a bad library edit degrades a capability
    instead of crashing a run. `confirm` comes back as a CONFIRM_LEVELS value."""
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, ["grants must be a mapping (actions / utils / confirm / runs)"]
    known = ("actions", "utils", "confirm", "runs")
    problems = [f"grants.{k}: unknown key (expected {' / '.join(known)})"
                for k in raw if k not in known]
    out: dict = {}
    for key, valid, label in (("actions", lambda a: a in KINDS, "an action kind"),
                              ("utils", lambda u: isinstance(u, str) and is_slug(u),
                               "a kebab-case util name")):
        if key not in raw:
            continue
        vals = raw[key]
        if not isinstance(vals, list):
            problems.append(f"grants.{key} must be a list")
            continue
        problems += [f"grants.{key}: {v!r} is not {label}" for v in vals if not valid(v)]
        out[key] = [v for v in vals if valid(v)]
    if "confirm" in raw:
        if raw["confirm"] in _RAW_CONFIRM:
            out["confirm"] = _RAW_CONFIRM[raw["confirm"]]
        else:
            problems.append("grants.confirm must be true, false or revisions-only")
    if "runs" in raw:
        if raw["runs"] in ("last", "all"):
            out["runs"] = raw["runs"]
        else:
            problems.append("grants.runs must be last or all")
    return out, problems


def _parse(text: str) -> dict:
    """Lenient frontmatter meta: broken YAML reads as no frontmatter (mirrors
    library_docs), so a bad edit never takes grant loading down."""
    try:
        return frontmatter.parse(text)[0]
    except yaml.YAMLError:
        return {}


def read_library_grants(permissions_home: Path) -> dict[str, dict]:
    """slug → normalized grants for every LIBRARY permission that declares any — the one
    authority; nothing under a routine dir is ever consulted."""
    out: dict[str, dict] = {}
    if not permissions_home.is_dir():
        return out
    for path in sorted(permissions_home.glob("*.md")):
        try:
            meta = _parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        grants, _ = normalize_grants(meta.get("grants"))
        if grants:
            out[path.stem] = grants
    return out


_PERMISSIVENESS = {level: n for n, level in enumerate(CONFIRM_LEVELS)}
_RUNS_RANK = {level: n for n, level in enumerate(RUN_HISTORY_LEVELS)}


def _norm_rel(path: str) -> str:
    p = str(path or "").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def is_recipe_path(path: str) -> bool:
    p = _norm_rel(path)
    return any(p == pre.rstrip("/") or p.startswith(pre) for pre in RECIPE_PREFIXES)


def is_runs_path(path: str) -> bool:
    p = _norm_rel(path)
    return p == "runs" or p.startswith("runs/")


@dataclass(frozen=True)
class GrantPolicy:
    """One run's enforcement view: what its permissions unlock, plus (from the whole
    library) which capabilities exist to be granted — so a denial can name the permission
    the user would have to activate."""

    active: tuple[str, ...] = ()               # the routine's permission slugs
    actions: frozenset = frozenset()           # action kinds granted by active permissions
    utils: frozenset = frozenset()             # gated utils granted by active permissions
    gated_utils: dict = field(default_factory=dict)   # util → library permissions naming it
    kind_sources: dict = field(default_factory=dict)  # gated kind → library permissions granting it
    confirm: str = "always"                    # effective write_util approval policy
    run_history: str = "none"                  # previous-runs read access: none | last | all
    # own recipe/config writable? True only when a user fs_write_root covers the routine
    # dir (the routine-improver's case) — computed at policy load, never a permission.
    recipe_unlocked: bool = False
    runs_sources: tuple = _DEFAULT_RUNS_SOURCE            # permissions granting runs access
    # The live run's ts: paths under runs/<current_run_ts>/ are the run's OWN tree (status,
    # archived history) and stay readable regardless of run_history — the engine itself
    # points the model there after compaction.
    current_run_ts: str = ""

    def allows_kind(self, kind: str) -> bool:
        return kind not in GATED_KINDS or kind in self.actions

    def needs_confirm(self, creating: bool) -> bool:
        """Must the user approve this write_util? (creating=False → revising an existing util)"""
        return self.confirm == "always" or (self.confirm == "creations" and creating)

    def deny(self, action: dict) -> str | None:
        """A precise, actionable rejection for a gated call — or None when permitted. Worded
        for the model inside the schema-retry cycle: name the granting permission and route
        to ask_user, since only the user can change permissions."""
        kind = action.get("kind")
        if kind in GATED_KINDS and kind not in self.actions:
            srcs = ", ".join(self.kind_sources.get(kind)
                             or [_DEFAULT_KIND_SOURCE.get(kind, "util-authoring")])
            return (f"kind={kind} is not granted to this routine: it does not hold a "
                    f"permission carrying a {kind} grant (the library permission(s) {srcs} "
                    f"do, but only the user can activate one). Work with existing utils; if "
                    f"this capability is essential, file a deferred ask_user naming exactly "
                    f"what you need.")
        if kind == "util":
            name = str(action.get("name") or "")
            if name in self.gated_utils and name not in self.utils:
                perms = ", ".join(self.gated_utils[name])
                return (f"util {name!r} is reserved for routines holding the {perms} "
                        f"permission — this routine does not, so this channel is off "
                        f"limits. Continue without it; if it seems essential, file a "
                        f"deferred ask_user so the user can grant the permission.")
        if kind in ("read_file", "write_file", "edit_file"):
            writes = kind in ("write_file", "edit_file")
            paths = [str(action.get("path") or "")]
            if kind == "read_file":
                paths += [str(p) for p in action.get("paths") or []]
            for path in paths:
                if not path:
                    continue
                own_run = bool(self.current_run_ts) and _norm_rel(path).startswith(
                    f"runs/{self.current_run_ts}/")
                if is_runs_path(path) and not own_run:
                    if writes:
                        return ("runs/ is engine-owned and read-only — transcripts and results "
                                "are written by the engine, never by the run.")
                    if self.run_history == "none":
                        srcs = ", ".join(self.runs_sources)
                        return (f"reading previous runs under runs/ is not granted to this "
                                f"routine (the {srcs} permissions unlock it — last run only, or "
                                f"all). The state digest already carries the last run's result; "
                                f"if you need more, file a deferred ask_user.")
                if writes and is_recipe_path(path) and not self.recipe_unlocked:
                    return (f"writing {_norm_rel(path)!r} would modify this routine's own recipe "
                            f"or config (main.md / steps/ / traits/ / instruction.md / "
                            f"routine.yaml) — a run never edits its own: recipes are refined by "
                            f"the routine-improver meta routine, config by the user. File a "
                            f"deferred ask_user describing the change instead.")
        return None


def load_policy(permissions_home: Path, active: list[str] | None,
                current_run_ts: str = "", recipe_unlocked: bool = False) -> GrantPolicy:
    """Build the run policy: union the held permissions' grants; index the whole library's
    grants so denials can point at the permission that would unlock the capability. The most
    permissive confirm level among held write_util-granting permissions wins (grants are
    additive); a granting permission that sets none confirms everything."""
    lib = read_library_grants(permissions_home)
    gated_utils: dict[str, list[str]] = {}
    kind_sources: dict[str, list[str]] = {}
    runs_sources: list[str] = []
    for slug, g in lib.items():
        for kind in g.get("actions") or []:
            if kind in GATED_KINDS:
                kind_sources.setdefault(kind, []).append(slug)
        for util in g.get("utils") or []:
            gated_utils.setdefault(util, []).append(slug)
        if g.get("runs"):
            runs_sources.append(slug)
    actions: set[str] = set()
    utils: set[str] = set()
    confirm = "always"
    run_history = "none"
    for slug in active or []:
        g = lib.get(slug) or {}
        actions.update(g.get("actions") or [])
        utils.update(g.get("utils") or [])
        if "write_util" in (g.get("actions") or []):
            level = g.get("confirm") or "always"
            if _PERMISSIVENESS[level] > _PERMISSIVENESS[confirm]:
                confirm = level
        if _RUNS_RANK.get(g.get("runs") or "none", 0) > _RUNS_RANK[run_history]:
            run_history = g["runs"]
    return GrantPolicy(active=tuple(active or []), actions=frozenset(actions),
                       utils=frozenset(utils),
                       gated_utils={k: tuple(v) for k, v in gated_utils.items()},
                       kind_sources={k: tuple(v) for k, v in kind_sources.items()},
                       confirm=confirm, run_history=run_history,
                       recipe_unlocked=recipe_unlocked,
                       runs_sources=tuple(runs_sources) or _DEFAULT_RUNS_SOURCE,
                       current_run_ts=current_run_ts)
