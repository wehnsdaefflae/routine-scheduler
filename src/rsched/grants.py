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
      actions: [write_util, revise_util, shell]  # only GATED_KINDS count
      #   `revise_util` is a capability TOKEN here, never an action kind: the model emits
      #   write_util for both, and the engine picks create-vs-revise from whether the
      #   target name already exists in the library.
      utils: [discord, gmail:send]     # reserved utils switched on for this routine —
      #   a bare name grants every verb, `name:verb` grants that ONE subcommand (the
      #   call's first positional argument)
      confirm: always | creations | never       # write_util approval level
      rule_confirm: always | creations | never  # write_rule approval level
      remind_confirm: always | creations | never  # GLOBAL consequence-reminder approval
      runs: last | all                 # previous-run read depth — a SETTING, no doc needed
      reminders: none | local | global # consequence-reminder stores — a SETTING too

Which utils are "reserved" at all is library-defined: the union of every permission
doc's `requires.utils`, at the grain the doc names — a bare `gmail` reserves every verb of
the util, `gmail:send` reserves ONLY that verb, so reading a mailbox stays behind nothing but
its credential while sending needs the permission. Which action kinds are gateable is
engine-defined (GATED_KINDS) — a library edit can reserve a new util, but can never retract a
base action kind from every routine. Enforced per turn by `engine.actions.validate_action`
(allowed kinds = workflow `tools:` ∩ (base ∪ capabilities)) plus path gates for runs/ and the
routine's
recipe/config files — a rejected call is corrected inside the schema-retry cycle and
never becomes a turn. Base kinds — util, read_file, write_file, llm, spawn, … — stay
ungated.

A run edits its own recipe (main.md, stages/, tuning.yaml) only when the routine holds the
`write_recipe` capability (through the recipe-authoring doc) or the run is an in-place
revise leg (engine/revise.py) — never because a write root happens to cover the routine's
dir, which is how it unlocked before 0.261.0. `routine.yaml` is NEVER writable by any run —
not even the improver, not even under an fs_write_root: config (permissions, capabilities,
budgets, roots) is the user's, changed only via the UI or a deferred ask_user.
"""

from __future__ import annotations

from pathlib import Path

from .engine.actionschema import KINDS
from .ids import is_slug
from .reminders import LEVELS as REMINDER_LEVELS

# `read_rule` is deliberately NOT gated: a routine must be able to read the general rules
# it holds, and reading library prose has no side effect worth a decision. The catalog
# (`name: "list"`) is open for the same reason.
# `memory_read`/`memory_write` and `script` are BASE kinds: every routine held them (35 of 35
# and 30 of 35) and none could act without them, so a switch nobody turns off was ceremony —
# and a script's blast radius is the routine's own jail, the same as a util's. `detach` stays
# gated but no permission grants it: it is STRUCTURAL, added to a root conversation's policy
# at setup (engine/loopsetup.build_base_policy) and to nothing else.
GATED_KINDS = ("write_util", "revise_util", "remove_util", "write_rule", "write_recipe",
               "detach", "schedule_run", "shell")
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
# Only a GATED kind is a capability: a kind every routine may use needs no switch; naming
# one anyway is config that says nothing, so it is refused and the file keeps saying what the
# routine can do. `detach` is gated but structural — added to a root conversation's policy at
# setup — so no config names it either.
CAPABILITY_ACTIONS = tuple(k for k in GATED_KINDS if k != "detach")
# When no library permission doc requires a gated kind (e.g. the library predates it),
# denials still name the doc that canonically covers its conduct.
_DEFAULT_KIND_SOURCE = {"write_util": "util-authoring", "revise_util": "util-authoring",
                        "write_recipe": "recipe-authoring",
                        "remove_util": "util-removal",
                        "write_rule": "rule-authoring",
                        "schedule_run": "scheduling", "shell": "shell"}
# write_util approval policy, least → most permissive: "always" (user approves create AND
# revise), "creations" (revisions are autonomous once the selftest passes; NEW utils ask),
# "never".
# Shared by ALL THREE approval dials: `confirm` (write_util), `rule_confirm` (write_rule)
# and `remind_confirm` (a GLOBAL consequence reminder). Same ladder, separate dials on purpose —
# a rule is held by many routines, so a revision lands in every one of them at their next run,
# and a global reminder starts HOLDING actions in routines that never asked for it. Each blast
# radius is a different decision from "may this routine author utils", and collapsing them would
# make a never-confirm util policy silently authorize the other two.
CONFIRM_LEVELS = ("always", "creations", "never")
APPROVAL_DIALS = ("confirm", "rule_confirm", "remind_confirm")
# runs: access to previous runs. `last` is every routine's floor (D96); `all` is the setting a
# longitudinal routine (self-audit, rules-review) is given. `none` exists for CHILD runs only,
# which read their brief rather than the archive (loopsetup).
RUN_HISTORY_LEVELS = ("none", "last", "all")
# The SETTINGS half of a capabilities mapping — the approval dials, the previous-run read depth
# and the reminder layer — each with its all-off value. The user's choice per routine: never a
# permission doc's requirement, and kept through the floor with no permission behind it. ONE
# vocabulary, read by the validator, the cascade and the floor alike.
SETTING_DEFAULTS = {**dict.fromkeys(APPROVAL_DIALS, "always"), "runs": "none",
                    "reminders": "none"}
# The routine's own recipe files — writable by the owning run only under the `write_recipe`
# capability or in a revise leg (see the module docstring). stages/ + main.md are the
# materialized workflow. The general RULES are not here at all: they live in the library, one
# copy, and no run writes them under any grant. routine.yaml (the user's config) is guarded
# separately: NEVER writable by any run, even the improver — see CONFIG_FILE and
# GrantPolicy.deny.
RECIPE_PREFIXES = ("main.md", "stages/", "tuning.yaml")
CONFIG_FILE = "routine.yaml"
# An all-off capabilities mapping — the base for cascades and the subrun/clarify default.
EMPTY_CAPABILITIES = {"actions": [], "utils": [], **SETTING_DEFAULTS}


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
        return {}, [(f"{label} must be a mapping of entity class → names "
                     f"({' / '.join(CLASSES)}; '*' means at least one)")]
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
    of crashing a run. A doc may require only ACTIONS and UTILS: the approval dials, the
    run-history depth and the reminder stores are SETTINGS the user chooses per routine,
    never something holding a doc switches on.
    """
    known = ("actions", "utils") if requires else ("actions", "utils", *SETTING_DEFAULTS)
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [f"{label} must be a mapping ({' / '.join(known)})"]
    problems = [f"{label}.{k}: unknown key (expected {' / '.join(known)})"
                + (" — that is a setting the user chooses per routine, not a requirement"
                   if requires and k in SETTING_DEFAULTS else "")
                for k in raw if k not in known]
    out: dict = {}
    for key, valid, kind_label in (("actions", lambda a: a in CAPABILITY_ACTIONS,
                                    "a capability"),
                                   ("utils", is_util_entry,
                                    "a kebab-case util name, optionally :verb-scoped")):
        if key not in raw:
            continue
        vals = raw[key]
        if not isinstance(vals, list):
            problems.append(f"{label}.{key} must be a list")
            continue
        problems += [f"{label}.{key}: {v!r} is not {kind_label}"
                     + (" — every routine may use it" if key == "actions" and v in KINDS
                        and v != "detach" else "")
                     for v in vals if not valid(v)]
        out[key] = [v for v in vals if valid(v)]
    if requires:
        return out, problems
    for dial in APPROVAL_DIALS:
        if dial in raw:
            if raw[dial] in CONFIRM_LEVELS:
                out[dial] = raw[dial]
            else:
                problems.append(f"{label}.{dial} must be always, creations or never")
    if "runs" in raw:
        if raw["runs"] in RUN_HISTORY_LEVELS:
            out["runs"] = raw["runs"]
        else:
            problems.append(f"{label}.runs must be {' or '.join(RUN_HISTORY_LEVELS)}")
    if "reminders" in raw:
        if raw["reminders"] in REMINDER_LEVELS:
            out["reminders"] = raw["reminders"]
        else:
            problems.append(f"{label}.reminders must be {' or '.join(REMINDER_LEVELS)}")
    return out, problems


def _parse(text: str) -> dict:
    """Lenient frontmatter meta — the shared parser; a bad edit never takes policy
    loading down.
    """
    from .library_docs import parse_lenient
    return parse_lenient(text)[0]


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


def capabilities_for(active: list[str], lib: dict[str, dict],
                     base: dict | None = None) -> dict:
    """The activation cascade: raise `base` (an all-off mapping when None) until every
    active doc's requires are covered. The dials and the settings (`confirm`, `runs`,
    `reminders`, …) are untouched — they are the user's, not a requirement.
    """
    caps = {**EMPTY_CAPABILITIES, **(base or {})}
    actions = list(dict.fromkeys(caps.get("actions") or []))
    utils = list(dict.fromkeys(caps.get("utils") or []))
    for slug in active:
        req = lib.get(slug) or {}
        actions += [a for a in req.get("actions") or [] if a not in actions]
        utils += [u for u in req.get("utils") or [] if u not in utils]
    return {**_settings(caps), "actions": actions, "utils": utils}


def _settings(caps: dict) -> dict:
    return {key: caps.get(key) or default for key, default in SETTING_DEFAULTS.items()}


def floor_capabilities(active: list[str], lib: dict[str, dict], caps: dict) -> dict:
    """Bind the two layers so the permission is the switch and the capability is only the
    means of asking for it: a gated action or reserved util survives ONLY when some HELD
    conduct permission's `requires:` names it. The settings (approval dials, run-history
    depth, reminder stores) pass through untouched — they are the user's choice per routine
    and need no permission behind them.

    This is the complement of `capabilities_for`'s raise: apply raise THEN floor and the
    mapping becomes exactly the union of the active docs' requires (actions/utils) plus the
    user's settings — no orphan capability can contradict the held permissions. Enforcement
    still reads capabilities alone (fail-closed).
    """
    caps = {**EMPTY_CAPABILITIES, **(caps or {})}
    req_actions: set[str] = set()
    req_utils: set[str] = set()
    for slug in active:
        req = lib.get(slug) or {}
        req_actions.update(a for a in req.get("actions") or [] if a in GATED_KINDS)
        req_utils.update(req.get("utils") or [])
    active_set = set(active)
    # Fallback for the "library predates the kind" gap (see _DEFAULT_KIND_SOURCE): a gated
    # kind whose canonical SOURCE permission is HELD survives even when that permission's
    # doc requires: has not been updated to name it. capabilities_for's RAISE is unchanged,
    # so merely holding the permission does NOT auto-add the kind; only an explicit opt-in
    # survives the floor.
    actions = [a for a in caps.get("actions") or []
               if a in req_actions or _DEFAULT_KIND_SOURCE.get(a) in active_set]
    # A verb-scoped entry is NARROWER than the doc that reserves the util, so `signal:send`
    # survives under a doc requiring `signal` (and under one requiring `signal:send`). The
    # reverse never holds: a doc that only reserves `signal:send` cannot float a bare
    # `signal` past the floor.
    req_util_names = {split_util_verb(u)[0] for u in req_utils if not split_util_verb(u)[1]}
    utils = [u for u in caps.get("utils") or []
             if u in req_utils or split_util_verb(u)[0] in req_util_names]
    return {**_settings(caps), "actions": actions, "utils": utils}
