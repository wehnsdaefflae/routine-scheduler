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
the approval dials are capabilities-only (an approval level is user policy, never a doc's
demand):

    capabilities:
      actions: [write_util, write_rule, memory_read, memory_write]  # only GATED_KINDS count
      #   `revise_util` is a capability TOKEN here, never an action kind: the model emits
      #   write_util for both, and the engine picks create-vs-revise from whether the
      #   target name already exists in the library.
      utils: [discord, signal:read]    # reserved utils switched on for this routine —
      #   a bare name grants every verb, `name:verb` grants that ONE subcommand (the
      #   call's first positional argument), which is how read-only access is expressed
      confirm: always | creations | never       # write_util approval level
      rule_confirm: always | creations | never  # write_rule approval level
      runs: none | last | all          # previous-run read depth (requires: last | all)

Which utils are "reserved" at all is library-defined: the union of every permission
doc's `requires.utils`. Which action kinds are gateable is engine-defined (GATED_KINDS)
— a library edit can reserve a new util, but can never retract a base action kind from
every routine. Enforced per turn by `engine.actions.validate_action` (allowed kinds =
workflow `tools:` ∩ (base ∪ capabilities)) plus path gates for runs/ and the routine's
recipe/config files — a rejected call is corrected inside the schema-retry cycle and
never becomes a turn. Base kinds — util, read_file, write_file, llm, spawn, … — stay
ungated.

Recipe writes are NOT a capability: a run never edits its own recipe (main.md, stages/)
— recipe improvement is the routine-improver meta routine's job. The single
override is the user-granted resource `fs_write_roots`: when a write root covers a
routine's dir (the improver's case), the engine unlocks the recipe files for that run.
`routine.yaml` is NEVER writable by any run — not even the improver, not even under an
fs_write_root: config (permissions, capabilities, budgets, roots) is the user's, changed
only via the UI or a deferred ask_user.
"""

from __future__ import annotations

from pathlib import Path

from .engine.actionschema import KINDS
from .ids import is_slug

# `read_rule` is deliberately NOT gated: a routine must be able to read the general rules
# it holds, and reading library prose has no side effect worth a decision. The catalog
# (`name: "list"`) is open for the same reason.
GATED_KINDS = ("write_util", "revise_util", "remove_util", "write_rule", "write_recipe",
               "memory_read", "memory_write", "detach", "schedule_run", "script")
# `write_recipe` is a capability TOKEN too, on the same terms: the model emits write_file /
# edit_file and the engine decides from the PATH whether the target is this routine's own
# recipe (grantpolicy.is_recipe_path). Before 0.261.0 that was not a capability at all — it
# unlocked as a SIDE EFFECT of a user-granted fs_write_root covering the routine's own dir,
# which meant granting a routine write access to its own working directory silently handed it
# the right to rewrite its own instructions. Now it is a switch you throw on purpose.
# `revise_util` is a CAPABILITY token, NOT an action kind: the model always emits
# kind=write_util, and the engine decides create-vs-revise from whether the target slug
# already exists in the library (see GrantPolicy.deny). Keeping it out of the action schema
# is deliberate — the flat kind surface is what weak models and Ollama grammars handle well,
# and the model has no reliable way to know which mode it is in before it looks.
# The two capability TOKENS (`revise_util`, `write_recipe`) are not emittable kinds, so
# they join the vocabulary here rather than in KINDS.
CAPABILITY_ACTIONS = (*KINDS, "revise_util", "write_recipe")
# When no library permission doc requires a gated kind (e.g. the library predates it),
# denials still name the doc that canonically covers its conduct.
_DEFAULT_KIND_SOURCE = {"write_util": "util-authoring", "revise_util": "util-revision",
                        "write_recipe": "recipe-authoring",
                        "remove_util": "util-removal",
                        "memory_read": "memory", "memory_write": "memory",
                        "write_rule": "rule-authoring",
                        "detach": "background-tasks", "schedule_run": "scheduling",
                        "script": "scripts"}
# A capabilities `utils:` entry is either a bare util name (every verb) or `name:verb`
# (that ONE subcommand — the util's first positional argument). Verb-scoping is how a
# routine gets read-only access to a channel it must not write to.
def split_util_verb(entry: str) -> tuple[str, str]:
    """`"signal:read"` → `("signal", "read")`; `"signal"` → `("signal", "")`."""
    name, _, verb = str(entry or "").partition(":")
    return name, verb


def is_util_entry(entry: object) -> bool:
    if not isinstance(entry, str):
        return False
    name, verb = split_util_verb(entry)
    return is_slug(name) and (not verb or is_slug(verb))
_DEFAULT_RUNS_SOURCE = ("run-history",)
# write_util approval policy, least → most permissive: "always" (user approves create AND
# revise), "creations" (revisions are autonomous once the selftest passes; NEW utils ask),
# "never".
# Shared by BOTH approval dials: `confirm` (write_util) and `rule_confirm` (write_rule). Same
# ladder, separate dials on purpose — a rule is held by many routines, so a revision lands in
# every one of them at their next run. That blast radius is a different decision from "may this
# routine author utils", and collapsing the two would make a never-confirm util policy silently
# authorize it.
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
# stages/ + main.md are the materialized workflow. The general RULES are not here at all:
# they live in the library, one copy, and no run writes them under any grant. routine.yaml
# (the user's config) is guarded separately: NEVER writable by any run, even the improver —
# see CONFIG_FILE and GrantPolicy.deny.
RECIPE_PREFIXES = ("main.md", "stages/", "tuning.yaml")
CONFIG_FILE = "routine.yaml"
# An all-off capabilities mapping — the base for cascades and the subrun/clarify default.
EMPTY_CAPABILITIES = {"actions": [], "utils": [], "util_tags": [], "confirm": "always",
                      "rule_confirm": "always", "runs": "none", "workflows": "catalog"}


# The SOFT edge (`expects:`), the counterpart to `requires:`. A permission doc's `requires:`
# names capabilities the cascade SWITCHES ON and the floor keeps on — necessary, enforced.
# `expects:` names entities the doc's (or rule's) instructions PRESUME but the engine will
# never force: a bound machine, an exposed secret, a write root to publish into. It grants
# nothing, blocks nothing and is legal on a RULE, where `requires:` stays a lint error —
# a rule may say what it presumes, it may never switch a capability on.
#
# Shape: entity CLASS → names (entities.py), where "*" means "at least one of this class".
# The prose explaining WHICH one belongs in the doc body, never here: this key is joined
# against declarations, never read for meaning.
def normalize_expects(raw: object, *, label: str = "expects") -> tuple[dict, list[str]]:
    """Validate + normalize an `expects:` mapping. Returns (mapping, problems); an invalid
    row is dropped and reported, never raised — a soft edge must not be able to break a run.
    """
    from .entities import CLASSES, parse_entity

    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [f"{label} must be a mapping of entity class → names "
                    f"({' / '.join(CLASSES)}; '*' means at least one)"]
    out: dict[str, list[str]] = {}
    problems: list[str] = []
    for cls, vals in raw.items():
        if cls not in CLASSES:
            problems.append(f"{label}.{cls}: unknown entity class "
                            f"(expected {' / '.join(CLASSES)})")
            continue
        items = vals if isinstance(vals, list) else [vals]
        keep: list[str] = []
        for item in items:
            if not isinstance(item, str) or not item.strip():
                problems.append(f"{label}.{cls}: entries must be non-empty strings")
                continue
            name = item.strip()
            if name != "*" and parse_entity(f"{cls}:{name}") is None:
                problems.append(f"{label}.{cls}: {name!r} is not a valid {cls} entity name "
                                f"(or use '*' for 'at least one')")
                continue
            if name not in keep:
                keep.append(name)
        if keep:
            out[cls] = keep
    return out, problems


def normalize_capabilities(raw: object, *, label: str = "capabilities",
                           requires: bool = False) -> tuple[dict, list[str]]:
    """Validate + normalize one capabilities mapping (routine.yaml `capabilities:` or,
    with requires=True, a permission doc's `requires:`). Returns (mapping, problems);
    invalid parts are dropped and reported, so a bad edit degrades a capability instead
    of crashing a run. `confirm` / `rule_confirm` come back as CONFIRM_LEVELS values and are
    rejected inside requires — an approval level is the user's policy, not a doc's demand.
    """
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [f"{label} must be a mapping (actions / utils / util_tags"
                    + (" / runs)" if requires else " / confirm / runs)")]
    known = (("actions", "utils", "util_tags", "runs", "workflows") if requires
             else ("actions", "utils", "util_tags", "confirm", "rule_confirm",
                   "runs", "workflows"))
    problems = [f"{label}.{k}: unknown key (expected {' / '.join(known)})"
                + (" — the approval level is a capability the user sets, not a requirement"
                   if requires and k in ("confirm", "rule_confirm") else "")
                for k in raw if k not in known]
    out: dict = {}
    for key, valid, kind_label in (("actions", lambda a: a in CAPABILITY_ACTIONS,
                                    "an action kind"),
                                   ("utils", is_util_entry,
                                    "a kebab-case util name, optionally :verb-scoped"),
                                   ("util_tags", lambda t: isinstance(t, str) and t.strip()
                                    and t == t.strip().lower(), "a lowercase util tag")):
        if key not in raw:
            continue
        vals = raw[key]
        if not isinstance(vals, list):
            problems.append(f"{label}.{key} must be a list")
            continue
        problems += [f"{label}.{key}: {v!r} is not {kind_label}" for v in vals if not valid(v)]
        out[key] = [v for v in vals if valid(v)]
    for dial in ("confirm", "rule_confirm"):
        if dial in raw and not requires:
            if raw[dial] in CONFIRM_LEVELS:
                out[dial] = raw[dial]
            else:
                problems.append(f"{label}.{dial} must be always, creations or never")
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
    """Lenient frontmatter meta — the shared parser; a bad edit never takes policy
    loading down.
    """
    from .library_docs import parse_lenient
    return parse_lenient(text)[0]


def _catalog_tags(permissions_home: Path) -> list[dict]:
    """The live util catalog as `[{name, tags}]`, for expanding a doc's `util_tags:` gate.

    `permissions_home` is `<libraries_home>/permissions` by construction (ServerConfig), so the
    catalog sits beside it. A missing or unreadable library yields an empty catalog: tag gating
    then matches nothing, exactly as if no doc declared a tag — a broken library must not
    silently CLOSE every util either.
    """
    from . import utils_lib
    try:
        return [{"name": u["name"], "tags": list(u.get("tags") or [])}
                for u in utils_lib.list_utils(Path(permissions_home).parent)]
    except OSError:
        return []


def read_library_expects(docs_home: Path) -> dict[str, dict]:
    """Slug → normalized `expects:` for every doc in `docs_home` that declares one — the SOFT
    half of the dependency map, read from permissions/ and rules/ alike (a rule may expect,
    it may never require). Nothing under a routine dir is ever consulted.
    """
    out: dict[str, dict] = {}
    if not docs_home.is_dir():
        return out
    for path in sorted(docs_home.glob("*.md")):
        try:
            meta = _parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        exp, _ = normalize_expects(meta.get("expects"))
        if exp:
            out[path.stem] = exp
    return out


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
    util_tags = list(dict.fromkeys(caps.get("util_tags") or []))
    runs = caps.get("runs") or "none"
    workflows = caps.get("workflows") or "catalog"
    for slug in active:
        req = lib.get(slug) or {}
        actions += [a for a in req.get("actions") or [] if a not in actions]
        utils += [u for u in req.get("utils") or [] if u not in utils]
        util_tags += [t for t in req.get("util_tags") or [] if t not in util_tags]
        need = req.get("runs") or "none"
        if _RUNS_RANK.get(need, 0) > _RUNS_RANK.get(runs, 0):
            runs = need
        need_wf = req.get("workflows") or "catalog"
        if _WORKFLOW_RANK.get(need_wf, 0) > _WORKFLOW_RANK.get(workflows, 0):
            workflows = need_wf
    return {"actions": actions, "utils": utils, "util_tags": util_tags,
            "confirm": caps.get("confirm") or "always",
            "rule_confirm": caps.get("rule_confirm") or "always",
            "runs": runs, "workflows": workflows}


def floor_capabilities(active: list[str], lib: dict[str, dict], caps: dict) -> dict:
    """Bind the two layers so the permission is the switch and the capability is only the
    means of asking for it (see the module docstring's two-layer model): a gated action or
    reserved util survives ONLY when some HELD conduct permission's `requires:` names it,
    and run access falls to `none` unless a held doc grants it. The policy DIALS that ride
    a capability — the two approval levels and the run-history depth — are preserved:
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
    req_util_tags: set[str] = set()
    grants_runs = False
    grants_wf = False
    for slug in active:
        req = lib.get(slug) or {}
        req_actions.update(a for a in req.get("actions") or [] if a in GATED_KINDS)
        req_utils.update(req.get("utils") or [])
        req_util_tags.update(req.get("util_tags") or [])
        if req.get("runs"):
            grants_runs = True
        if req.get("workflows"):
            grants_wf = True
    active_set = set(active)
    # Fallback for the "library predates the kind" gap (see _DEFAULT_KIND_SOURCE): a gated
    # kind whose canonical SOURCE permission is HELD survives even when that permission's
    # doc requires: has not been updated to name it — otherwise e.g. remove_util can never
    # persist under util-authoring (util-authoring.md predates the kind), so the UI toggle
    # silently reverts on save. capabilities_for's RAISE is unchanged, so merely holding the
    # permission does NOT auto-add the kind; only an explicit user opt-in survives the floor.
    actions = [a for a in caps.get("actions") or []
               if a in req_actions or _DEFAULT_KIND_SOURCE.get(a) in active_set]
    # A verb-scoped entry is NARROWER than the doc that reserves the util, so `signal:read`
    # survives under a doc requiring `signal` (and under one requiring `signal:read`). The
    # reverse never holds: a doc that only reserves `signal:read` cannot float a bare
    # `signal` past the floor.
    req_util_names = {split_util_verb(u)[0] for u in req_utils if not split_util_verb(u)[1]}
    utils = [u for u in caps.get("utils") or []
             if u in req_utils or split_util_verb(u)[0] in req_util_names]
    util_tags = [t for t in caps.get("util_tags") or [] if t in req_util_tags]
    runs = (caps.get("runs") or "none") if grants_runs else "none"
    workflows = (caps.get("workflows") or "catalog") if grants_wf else "catalog"
    return {"actions": actions, "utils": utils, "util_tags": util_tags,
            "confirm": caps.get("confirm") or "always",
            "rule_confirm": caps.get("rule_confirm") or "always",
            "runs": runs, "workflows": workflows}


