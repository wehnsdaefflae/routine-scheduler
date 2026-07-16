"""Capability permissions — the machine-enforced layer of the two-layer permission set.

Two layers, both user-changeable ONLY (the web UI blocks edits while a run is active,
and a routine can never write its own routine.yaml into effect):

- **Capabilities** (routine.yaml `capabilities:`) are the atomic, engine-enforced
  surface: gated action kinds, reserved utils, the write_util approval level, and the
  previous-run read depth. Enforcement reads the routine's OWN config — nothing else.
- **Conduct permissions** (`<libraries_home>/permissions/<slug>.md`, held via
  routine.yaml `permissions:`) are prose instructions that reach the prompt's
  CAPABILITIES section when held. Their frontmatter `requires:` declares which
  capabilities the instructions presume — it GRANTS nothing. The web layer cascades:
  activating a doc switches on its required capabilities; switching a capability off
  deactivates the docs that require it. The engine enforces from capabilities alone,
  so a doc-without-capability misconfiguration fails CLOSED.

Schema — routine.yaml `capabilities:` and permission-doc `requires:` share it, except
`confirm` is capabilities-only (the approval level is user policy, never a doc's demand):

    capabilities:
      actions: [write_util, memory_read, memory_write]   # only GATED_KINDS are enforced
      utils: [discord]                 # reserved utils switched on for this routine
      confirm: always | creations | never    # write_util approval level
      runs: none | last | all          # previous-run read depth (requires: last | all)

Which utils are "reserved" at all is library-defined: the union of every permission
doc's `requires.utils`. Which action kinds are gateable is engine-defined (GATED_KINDS)
— a library edit can reserve a new util, but can never retract a base action kind from
every routine. Enforced per turn by `engine.actions.validate_action` (allowed kinds =
workflow `tools:` ∩ (base ∪ capabilities)) plus path gates for runs/ and the routine's
recipe/config files — a rejected call is corrected inside the schema-retry cycle and
never becomes a turn. Base kinds — util, read_file, write_file, llm, spawn, … — stay
ungated.

Recipe writes are NOT a capability: a run never edits its own recipe (main.md, stages/,
traits/) — recipe improvement is the routine-improver meta routine's job. The single
override is the user-granted resource `fs_write_roots`: when a write root covers a
routine's dir (the improver's case), the engine unlocks the recipe files for that run.
`routine.yaml` is NEVER writable by any run — not even the improver, not even under an
fs_write_root: config (permissions, capabilities, budgets, roots) is the user's, changed
only via the UI or a deferred ask_user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import frontmatter
import yaml

from .engine.actions import KINDS
from .ids import is_slug

GATED_KINDS = ("write_util", "memory_read", "memory_write", "detach")
# When no library permission doc requires a gated kind (e.g. the library predates it),
# denials still name the doc that canonically covers its conduct.
_DEFAULT_KIND_SOURCE = {"write_util": "util-authoring",
                        "memory_read": "memory", "memory_write": "memory",
                        "detach": "background-tasks"}
_DEFAULT_RUNS_SOURCE = ("run-history",)
# write_util approval policy, least → most permissive: "always" (user approves create AND
# revise), "creations" (revisions are autonomous once the selftest passes; NEW utils ask),
# "never".
CONFIRM_LEVELS = ("always", "creations", "never")
# runs: access to previous runs, none → last (only the previous run) → all
RUN_HISTORY_LEVELS = ("none", "last", "all")
# workflows: how a run may source a child's pattern at decomposition. catalog = pick an
# existing library pattern only (the always-on baseline); generate = also DRAFT a new one
# on demand (workflows/generate.py, a system-model call) when none fits. A `requires:` doc
# demanding it names only "generate" (catalog is the absence of the requirement).
WORKFLOW_LEVELS = ("catalog", "generate")
# The routine's own recipe files — never writable by the owning run unless a user-granted
# fs_write_root covers the routine dir (the improver's case; see the module docstring).
# traits/ holds the routine's adapted practice copies; stages/ + main.md the materialized
# workflow. routine.yaml (the user's config) is guarded separately: NEVER writable by any
# run, even the improver — see CONFIG_FILE and GrantPolicy.deny.
RECIPE_PREFIXES = ("main.md", "stages/", "traits/", "tuning.yaml")
CONFIG_FILE = "routine.yaml"
# An all-off capabilities mapping — the base for cascades and the subrun/clarify default.
EMPTY_CAPABILITIES = {"actions": [], "utils": [], "confirm": "always", "runs": "none",
                      "workflows": "catalog"}


def normalize_capabilities(raw: object, *, label: str = "capabilities",
                           requires: bool = False) -> tuple[dict, list[str]]:
    """Validate + normalize one capabilities mapping (routine.yaml `capabilities:` or,
    with requires=True, a permission doc's `requires:`). Returns (mapping, problems);
    invalid parts are dropped and reported, so a bad edit degrades a capability instead
    of crashing a run. `confirm` comes back as a CONFIRM_LEVELS value and is rejected
    inside requires — the approval level is the user's policy, not a doc's demand.
    """
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [f"{label} must be a mapping (actions / utils"
                    + (" / runs)" if requires else " / confirm / runs)")]
    known = (("actions", "utils", "runs", "workflows") if requires
             else ("actions", "utils", "confirm", "runs", "workflows"))
    problems = [f"{label}.{k}: unknown key (expected {' / '.join(known)})"
                + (" — the approval level is a capability the user sets, not a requirement"
                   if requires and k == "confirm" else "")
                for k in raw if k not in known]
    out: dict = {}
    for key, valid, kind_label in (("actions", lambda a: a in KINDS, "an action kind"),
                                   ("utils", lambda u: isinstance(u, str) and is_slug(u),
                                    "a kebab-case util name")):
        if key not in raw:
            continue
        vals = raw[key]
        if not isinstance(vals, list):
            problems.append(f"{label}.{key} must be a list")
            continue
        problems += [f"{label}.{key}: {v!r} is not {kind_label}" for v in vals if not valid(v)]
        out[key] = [v for v in vals if valid(v)]
    if "confirm" in raw and not requires:
        if raw["confirm"] in CONFIRM_LEVELS:
            out["confirm"] = raw["confirm"]
        else:
            problems.append(f"{label}.confirm must be always, creations or never")
    runs_ok = ("last", "all") if requires else ("none", "last", "all")
    if "runs" in raw:
        if raw["runs"] in runs_ok:
            out["runs"] = raw["runs"]
        else:
            problems.append(f"{label}.runs must be {' or '.join(runs_ok)}")
    wf_ok = ("generate",) if requires else ("catalog", "generate")
    if "workflows" in raw:
        if raw["workflows"] in wf_ok:
            out["workflows"] = raw["workflows"]
        else:
            problems.append(f"{label}.workflows must be {' or '.join(wf_ok)}")
    return out, problems


def _parse(text: str) -> dict:
    """Lenient frontmatter meta: broken YAML reads as no frontmatter (mirrors
    library_docs), so a bad edit never takes policy loading down.
    """
    try:
        return frontmatter.parse(text)[0]
    except yaml.YAMLError:
        return {}


def read_library_requires(permissions_home: Path) -> dict[str, dict]:
    """Slug → normalized `requires:` for every LIBRARY permission doc that declares one —
    the vocabulary of reservable capabilities and the docs↔capabilities dependency map.
    Nothing under a routine dir is ever consulted.
    """
    out: dict[str, dict] = {}
    if not permissions_home.is_dir():
        return out
    for path in sorted(permissions_home.glob("*.md")):
        try:
            meta = _parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        req, _ = normalize_capabilities(meta.get("requires"), label="requires", requires=True)
        if req:
            out[path.stem] = req
    return out


_PERMISSIVENESS = {level: n for n, level in enumerate(CONFIRM_LEVELS)}
_RUNS_RANK = {level: n for n, level in enumerate(RUN_HISTORY_LEVELS)}
_WORKFLOW_RANK = {level: n for n, level in enumerate(WORKFLOW_LEVELS)}


def capabilities_for(active: list[str], lib: dict[str, dict],
                     base: dict | None = None) -> dict:
    """The activation cascade: raise `base` (an all-off mapping when None) until every
    active doc's requires are covered. `runs` rises to the highest required depth;
    `confirm` is untouched — it is user policy, not a requirement.
    """
    caps = {**EMPTY_CAPABILITIES, **(base or {})}
    actions = list(dict.fromkeys(caps.get("actions") or []))
    utils = list(dict.fromkeys(caps.get("utils") or []))
    runs = caps.get("runs") or "none"
    workflows = caps.get("workflows") or "catalog"
    for slug in active:
        req = lib.get(slug) or {}
        actions += [a for a in req.get("actions") or [] if a not in actions]
        utils += [u for u in req.get("utils") or [] if u not in utils]
        need = req.get("runs") or "none"
        if _RUNS_RANK.get(need, 0) > _RUNS_RANK.get(runs, 0):
            runs = need
        need_wf = req.get("workflows") or "catalog"
        if _WORKFLOW_RANK.get(need_wf, 0) > _WORKFLOW_RANK.get(workflows, 0):
            workflows = need_wf
    return {"actions": actions, "utils": utils,
            "confirm": caps.get("confirm") or "always", "runs": runs, "workflows": workflows}


def floor_capabilities(active: list[str], lib: dict[str, dict], caps: dict) -> dict:
    """Bind the two layers so the permission is the switch and the capability is only the
    means of asking for it (see the module docstring's two-layer model): a gated action or
    reserved util survives ONLY when some HELD conduct permission's `requires:` names it,
    and run access falls to `none` unless a held doc grants it. The policy DIALS that ride
    a capability — write_util's `confirm` level and the run-history depth — are preserved:
    they are user policy, meaningful only while their backing permission is held.

    This is the complement of `capabilities_for`'s raise: apply raise THEN floor and the
    mapping becomes exactly the union of the active docs' requires (actions/utils) plus the
    user's chosen depth/approval policy — no orphan capability can contradict the held
    permissions. Enforcement still reads capabilities alone (fail-closed); this keeps the
    saved mapping from ever expressing a capability its permissions did not ask for.
    """
    caps = {**EMPTY_CAPABILITIES, **(caps or {})}
    req_actions: set[str] = set()
    req_utils: set[str] = set()
    grants_runs = False
    grants_wf = False
    for slug in active:
        req = lib.get(slug) or {}
        req_actions.update(a for a in req.get("actions") or [] if a in GATED_KINDS)
        req_utils.update(req.get("utils") or [])
        if req.get("runs"):
            grants_runs = True
        if req.get("workflows"):
            grants_wf = True
    actions = [a for a in caps.get("actions") or [] if a in req_actions]
    utils = [u for u in caps.get("utils") or [] if u in req_utils]
    runs = (caps.get("runs") or "none") if grants_runs else "none"
    workflows = (caps.get("workflows") or "catalog") if grants_wf else "catalog"
    return {"actions": actions, "utils": utils,
            "confirm": caps.get("confirm") or "always", "runs": runs, "workflows": workflows}


def unsatisfied_requires(active: list[str], capabilities: dict,
                         lib: dict[str, dict]) -> dict[str, list[str]]:
    """Doc slug → the capabilities its requires: names that the mapping does NOT cover —
    the deactivation cascade's input (the UI drops these docs; enforcement doesn't care:
    it fails closed on capabilities alone).
    """
    caps, _ = normalize_capabilities(capabilities)
    actions = set(caps.get("actions") or [])
    utils = set(caps.get("utils") or [])
    runs = caps.get("runs") or "none"
    workflows = caps.get("workflows") or "catalog"
    out: dict[str, list[str]] = {}
    for slug in active:
        req = lib.get(slug) or {}
        missing = [a for a in req.get("actions") or [] if a not in actions]
        missing += [f"util:{u}" for u in req.get("utils") or [] if u not in utils]
        need = req.get("runs")
        if need and _RUNS_RANK.get(runs, 0) < _RUNS_RANK.get(need, 0):
            missing.append(f"runs:{need}")
        need_wf = req.get("workflows")
        if need_wf and _WORKFLOW_RANK.get(workflows, 0) < _WORKFLOW_RANK.get(need_wf, 0):
            missing.append(f"workflows:{need_wf}")
        if missing:
            out[slug] = missing
    return out


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
    """One run's enforcement view: the routine's enabled capabilities, plus (from the
    whole library) which docs cover each capability — so a denial can name the
    permission whose conduct prose the user would activate alongside it.
    """

    active: tuple[str, ...] = ()               # held conduct-permission slugs (prompt prose)
    actions: frozenset = frozenset()           # enabled gated action kinds
    utils: frozenset = frozenset()             # enabled reserved utils
    gated_utils: dict = field(default_factory=dict)   # util → library docs requiring it
    kind_sources: dict = field(default_factory=dict)  # gated kind → library docs requiring it
    confirm: str = "always"                    # write_util approval policy
    run_history: str = "none"                  # previous-runs read access: none | last | all
    workflows: str = "catalog"                 # child-pattern sourcing: catalog | generate
    # own recipe/config writable? True only when a user fs_write_root covers the routine
    # dir (the routine-improver's case) — computed at policy load, never a capability.
    # The recipe set includes tuning.yaml (machine-tunable behavior parameters, e.g.
    # deliberation) — the file boundary IS the permission boundary, no key-level gates.
    recipe_unlocked: bool = False
    runs_sources: tuple = _DEFAULT_RUNS_SOURCE            # docs covering runs access
    # The live run's ts: paths under runs/<current_run_ts>/ are the run's OWN tree (status,
    # archived history) and stay readable regardless of run_history — the engine itself
    # points the model there after compaction.
    current_run_ts: str = ""

    def allows_kind(self, kind: str) -> bool:
        return kind not in GATED_KINDS or kind in self.actions

    def may_generate_workflow(self) -> bool:
        """May a subtask DRAFT a new library pattern when none fits (vs pick from the catalog)?
        Off by default — a user-set capability, covered by the workflow-generation permission.
        """
        return self.workflows == "generate"

    def needs_confirm(self, creating: bool) -> bool:
        """Must the user approve this write_util? (creating=False → revising an existing util)"""
        return self.confirm == "always" or (self.confirm == "creations" and creating)

    def deny(self, action: dict) -> str | None:
        """A precise, actionable rejection for a gated call — or None when permitted. Worded
        for the model inside the schema-retry cycle: capabilities are switched by the USER
        (on the routine's Permissions panel), so route to ask_user.
        """
        kind = action.get("kind")
        if kind in GATED_KINDS and kind not in self.actions:
            srcs = ", ".join(self.kind_sources.get(kind)
                             or [_DEFAULT_KIND_SOURCE.get(kind, "util-authoring")])
            return (f"kind={kind} is switched OFF in this routine's capabilities — only the "
                    f"user can switch it on (the {srcs} permission covers its conduct). Work "
                    f"with what you have; if this capability is essential, file a deferred "
                    f"ask_user naming exactly what you need.")
        if kind == "util":
            name = str(action.get("name") or "")
            if name in self.gated_utils and name not in self.utils:
                perms = ", ".join(self.gated_utils[name])
                return (f"util {name!r} is a reserved capability switched OFF for this "
                        f"routine — this channel is off limits (the {perms} permission "
                        f"covers its conduct). Continue without it; if it seems essential, "
                        f"file a deferred ask_user so the user can switch it on.")
        if kind in ("read_file", "view_image", "write_file", "edit_file"):
            writes = kind in ("write_file", "edit_file")
            paths = [str(action.get("path") or "")]
            if kind in ("read_file", "view_image"):
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
                        return (f"reading previous runs under runs/ is switched OFF in this "
                                f"routine's capabilities (the user can raise the depth to the "
                                f"last run or all; the {srcs} permission covers the conduct). "
                                f"The state digest already carries the last run's result; if "
                                f"you need more, file a deferred ask_user.")
                if writes and _norm_rel(path).split("/")[-1] == CONFIG_FILE:
                    return (f"writing {_norm_rel(path)!r} would change routine config "
                            f"(routine.yaml — permissions, capabilities, budgets, roots). Config "
                            f"is the user's: NO run edits it, not even the routine-improver "
                            f"(machine-tunable knobs like deliberation live in tuning.yaml). "
                            f"File a deferred ask_user describing the change you need.")
                if writes and is_recipe_path(path) and not self.recipe_unlocked:
                    return (f"writing {_norm_rel(path)!r} would modify this routine's own recipe "
                            f"(main.md / stages/ / traits/ / tuning.yaml) — a run never edits its "
                            f"own recipe; the routine-improver meta routine refines it. File a "
                            f"deferred ask_user describing the change instead.")
        return None


def load_policy(permissions_home: Path, active: list[str] | None,
                capabilities: dict | None = None, current_run_ts: str = "",
                recipe_unlocked: bool = False) -> GrantPolicy:
    """Build the run policy from the routine's OWN capabilities mapping; the library's
    `requires:` declarations contribute only the reserved-util vocabulary and the
    capability→doc index that lets denials name the covering permission. `active` (the
    held conduct docs) is carried for the composer's prose — it unlocks nothing here.
    """
    lib = read_library_requires(permissions_home)
    gated_utils: dict[str, list[str]] = {}
    kind_sources: dict[str, list[str]] = {}
    runs_sources: list[str] = []
    for slug, req in lib.items():
        for kind in req.get("actions") or []:
            if kind in GATED_KINDS:
                kind_sources.setdefault(kind, []).append(slug)
        for util in req.get("utils") or []:
            gated_utils.setdefault(util, []).append(slug)
        if req.get("runs"):
            runs_sources.append(slug)
    caps, _ = normalize_capabilities(capabilities)
    return GrantPolicy(active=tuple(active or []),
                       actions=frozenset(k for k in caps.get("actions") or []
                                         if k in GATED_KINDS),
                       utils=frozenset(caps.get("utils") or []),
                       gated_utils={k: tuple(v) for k, v in gated_utils.items()},
                       kind_sources={k: tuple(v) for k, v in kind_sources.items()},
                       confirm=caps.get("confirm") or "always",
                       run_history=caps.get("runs") or "none",
                       workflows=caps.get("workflows") or "catalog",
                       recipe_unlocked=recipe_unlocked,
                       runs_sources=tuple(runs_sources) or _DEFAULT_RUNS_SOURCE,
                       current_run_ts=current_run_ts)
