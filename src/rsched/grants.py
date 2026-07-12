"""Fragment grants — the machine-enforced permission side of fragments.

A library fragment's frontmatter may carry a `grants:` key that unlocks gated capabilities
for every routine that ACTIVATES the fragment (routine.yaml `fragments:` — the same switch
that inlines its prose). Grants are read ONLY from the LIBRARY copy under
`<libraries_home>/fragments/` — never from the routine-local editable copy under
`<routine>/fragments/` — so a routine can rewrite its own prose freely but can never grant
itself a capability.

Schema (fragment frontmatter):

    grants:
      actions: [util, write_util]      # action kinds; only GATED_KINDS are enforced
      utils: [discord]                 # utils reserved for routines carrying this grant
      confirm: true | false | revisions-only   # write_util approval policy

Only capabilities that were already gated or are outward-facing are enforced: the
`write_util` action, and any util named in some library fragment's `utils:` list (naming a
util anywhere in the library is what reserves it). Base kinds — util, read_file,
write_file, llm, spawn, … — stay ungated. A run's allowed action kinds are therefore
workflow `tools:` ∩ (base ∪ union of active grants), checked per turn by
`engine.actions.validate_action` so a rejected call is corrected inside the schema-retry
cycle and never becomes a turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import frontmatter
import yaml

from .engine.actions import KINDS
from .ids import is_slug

GATED_KINDS = ("write_util", "memory_read", "memory_write")
# When no library fragment grants a gated kind (e.g. the library predates it), denials
# still name the fragment that canonically carries it.
_DEFAULT_KIND_SOURCE = {"write_util": "util-authoring",
                        "memory_read": "memory", "memory_write": "memory"}
# write_util approval policy, least → most permissive. The raw `confirm:` vocabulary maps
# to it: true → "always" (user approves create AND revise), "revisions-only" → "creations"
# (revisions are autonomous once the selftest passes; NEW utils still ask), false → "never".
CONFIRM_LEVELS = ("always", "creations", "never")
_RAW_CONFIRM = {True: "always", "revisions-only": "creations", False: "never"}


def normalize_grants(raw: object) -> tuple[dict, list[str]]:
    """Validate + normalize one fragment's raw `grants:` value. Returns (grants, problems);
    invalid parts are dropped and reported, so a bad library edit degrades a capability
    instead of crashing a run. `confirm` comes back as a CONFIRM_LEVELS value."""
    if raw is None:
        return {}, []
    if not isinstance(raw, dict):
        return {}, ["grants must be a mapping (actions / utils / confirm)"]
    problems = [f"grants.{k}: unknown key (expected actions / utils / confirm)"
                for k in raw if k not in ("actions", "utils", "confirm")]
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
    return out, problems


def _parse(text: str) -> dict:
    """Lenient frontmatter meta: broken YAML reads as no frontmatter (mirrors
    fragments_lib), so a bad edit never takes grant loading down."""
    try:
        return frontmatter.parse(text)[0]
    except yaml.YAMLError:
        return {}


def read_library_grants(fragments_home: Path) -> dict[str, dict]:
    """slug → normalized grants for every LIBRARY fragment that declares any — the one
    authority; routine-local copies are never consulted."""
    out: dict[str, dict] = {}
    if not fragments_home.is_dir():
        return out
    for path in sorted(fragments_home.glob("*.md")):
        try:
            meta = _parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        grants, _ = normalize_grants(meta.get("grants"))
        if grants:
            out[path.stem] = grants
    return out


_PERMISSIVENESS = {level: n for n, level in enumerate(CONFIRM_LEVELS)}


@dataclass(frozen=True)
class GrantPolicy:
    """One run's enforcement view: what its active fragments unlock, plus (from the whole
    library) which capabilities exist to be granted — so a denial can name the fragment
    the user would have to activate."""

    active: tuple[str, ...] = ()               # the routine's active fragment slugs
    actions: frozenset = frozenset()           # action kinds granted by active fragments
    utils: frozenset = frozenset()             # gated utils granted by active fragments
    gated_utils: dict = field(default_factory=dict)   # util → library fragments naming it
    kind_sources: dict = field(default_factory=dict)  # gated kind → library fragments granting it
    confirm: str = "always"                    # effective write_util approval policy

    def allows_kind(self, kind: str) -> bool:
        return kind not in GATED_KINDS or kind in self.actions

    def needs_confirm(self, creating: bool) -> bool:
        """Must the user approve this write_util? (creating=False → revising an existing util)"""
        return self.confirm == "always" or (self.confirm == "creations" and creating)

    def deny(self, action: dict) -> str | None:
        """A precise, actionable rejection for a gated call — or None when permitted. Worded
        for the model inside the schema-retry cycle: name the granting fragment and route to
        ask_user, since only the user can activate fragments."""
        kind = action.get("kind")
        if kind in GATED_KINDS and kind not in self.actions:
            srcs = ", ".join(self.kind_sources.get(kind)
                             or [_DEFAULT_KIND_SOURCE.get(kind, "util-authoring")])
            return (f"kind={kind} is not granted to this routine: none of its active fragments "
                    f"carries a {kind} grant (the library fragment(s) {srcs} do, but only the "
                    f"user can activate one). Work with existing utils; if this capability is "
                    f"essential, file a deferred ask_user naming exactly what you need.")
        if kind == "util":
            name = str(action.get("name") or "")
            if name in self.gated_utils and name not in self.utils:
                frags = ", ".join(self.gated_utils[name])
                return (f"util {name!r} is reserved for routines with the {frags} fragment "
                        f"active — this routine does not have it, so this channel is off "
                        f"limits. Continue without it; if it seems essential, file a deferred "
                        f"ask_user so the user can activate the fragment.")
        return None


def load_policy(fragments_home: Path, active: list[str] | None) -> GrantPolicy:
    """Build the run policy: union the ACTIVE fragments' grants; index the whole library's
    grants so denials can point at the fragment that would unlock the capability. The most
    permissive confirm level among active write_util-granting fragments wins (grants are
    additive); a granting fragment that sets none confirms everything."""
    lib = read_library_grants(fragments_home)
    gated_utils: dict[str, list[str]] = {}
    kind_sources: dict[str, list[str]] = {}
    for slug, g in lib.items():
        for kind in g.get("actions") or []:
            if kind in GATED_KINDS:
                kind_sources.setdefault(kind, []).append(slug)
        for util in g.get("utils") or []:
            gated_utils.setdefault(util, []).append(slug)
    actions: set[str] = set()
    utils: set[str] = set()
    confirm = "always"
    for slug in active or []:
        g = lib.get(slug) or {}
        actions.update(g.get("actions") or [])
        utils.update(g.get("utils") or [])
        if "write_util" in (g.get("actions") or []):
            level = g.get("confirm") or "always"
            if _PERMISSIVENESS[level] > _PERMISSIVENESS[confirm]:
                confirm = level
    return GrantPolicy(active=tuple(active or []), actions=frozenset(actions),
                       utils=frozenset(utils),
                       gated_utils={k: tuple(v) for k, v in gated_utils.items()},
                       kind_sources={k: tuple(v) for k, v in kind_sources.items()},
                       confirm=confirm)
