"""`GrantPolicy` — what a run may actually DO, and the one place that decides it.

Split out of `grants.py` (F393) along the line that already existed conceptually: `grants.py`
is the capability VOCABULARY and how config is read into it; this is ENFORCEMENT. Every gate in
the engine asks this object — may this action kind be emitted, may this util run, may this path
be written, may this run read an earlier one — and it answers from the routine's OWN
capabilities plus the run's one-time grant overlay, never from a permission doc. A doc held
without its capability therefore fails CLOSED, which is the whole reason the two layers are
separate.

The four-state grant model (allow/deny x now/forever, plus allow-once for turn-action classes)
lives here as `entity_state`; the WEB layer writes forever-decisions to routine.yaml at click
time and the engine only ever bridges now-decisions into the live overlay. No run writes its
own config, so this object is read-only with respect to what created it.
"""

# path questions that are POLICY, not filesystem: is this the routine's own recipe (a write there
# unlocks self-editing), and is this under runs/ (engine-owned).
from __future__ import annotations

from dataclasses import dataclass, field

from .grants import (
    _DEFAULT_KIND_SOURCE,
    _DEFAULT_RUNS_SOURCE,
    _RUNS_RANK,
    CONFIG_FILE,
    GATED_KINDS,
    RECIPE_PREFIXES,
    split_util_verb,
)


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
    # Enabled util TAG CLASSES. A permission doc gates a whole class with `util_tags:`, so a
    # NEWLY ADDED util carrying a gated tag is closed by default instead of silently open —
    # the name list alone was fail-open, and every util the library gains is a new hole.
    util_tags: frozenset = frozenset()
    gated_utils: dict = field(default_factory=dict)   # util → library docs requiring it
    # Gated util → its declared tags, for the tag-class check in deny(). Only gated utils are
    # indexed; the catalog is read at policy load ONLY when some doc declares `util_tags`.
    util_tag_index: dict = field(default_factory=dict)
    # Util names the library already has, for the create-vs-revise split. Loaded ONLY when
    # the routine holds exactly one half (holding both, or neither, settles the call without
    # knowing) — so the common policies cost no catalog read.
    known_utils: frozenset = frozenset()
    kind_sources: dict = field(default_factory=dict)  # gated kind → library docs requiring it
    confirm: str = "always"                    # write_util approval policy
    rule_confirm: str = "always"               # write_rule approval policy (own blast radius)
    run_history: str = "none"                  # previous-runs read access: none | last | all
    workflows: str = "catalog"                 # child-pattern sourcing: catalog | generate
    # The four-state grant model's persistent NO: entity ids (entities.py) the user has
    # denied FOREVER (routine.yaml `grants:` false rows). deny() stops routing these to a
    # request — the answer is already given.
    denied: frozenset = frozenset()
    # Run-scoped overlay (with_overlay): one-time user decisions for THIS run only —
    # in-memory on the RunContext, folded in here so every consumer reads ONE policy.
    granted_now: frozenset = frozenset()
    denied_now: frozenset = frozenset()
    # own recipe/config writable? True only when a user fs_write_root covers the routine
    # dir (the routine-improver's case) — computed at policy load, never a capability.
    # The recipe set includes tuning.yaml (machine-tunable behavior parameters, e.g.
    # deliberation) — the file boundary IS the permission boundary, no key-level gates.
    recipe_unlocked: bool = False
    # D62 admin conversation: the operator authenticated this leg with RSCHED_ADMIN_TOKEN,
    # so CAPABILITY gating is lifted (gated kinds, reserved utils, previous-run read depth).
    # STRUCTURAL / ownership gates STILL apply — runs/ write, routine.yaml config, the recipe
    # seal, and the root-conversation-only handler gates. Never persisted, never inherited by
    # a subrun (see engine/admin.py). Enforced in allows_kind() and deny() below.
    admin: bool = False
    runs_sources: tuple = _DEFAULT_RUNS_SOURCE            # docs covering runs access
    # The live run's ts: paths under runs/<current_run_ts>/ are the run's OWN tree (status,
    # archived history) and stay readable regardless of run_history — the engine itself
    # points the model there after compaction.
    current_run_ts: str = ""
    # True for a spawned/subtask child run: sub-workflows run with capabilities OFF by
    # design (childrun._sub_routine), independent of what the PARENT routine holds. It
    # only reshapes denial WORDING — a child's gated-kind denial must name the child
    # workflow as the scope that lacks the kind, not claim the routine lacks it (R46).
    is_subrun: bool = False

    def allows_kind(self, kind: str) -> bool:
        if self.admin or kind not in GATED_KINDS:
            return True
        if kind == "write_util":
            # EITHER half offers the kind — the model emits write_util for both create and
            # revise, and deny() decides which one this call is once the name is known.
            # Projecting the kind away when only one half is held would hide the capability
            # the routine does have.
            return bool({"write_util", "revise_util"} & set(self.actions))
        return kind in self.actions

    def with_overlay(self, granted_now: set[str], denied_now: set[str]) -> GrantPolicy:
        """This policy plus the run's one-time decisions: capability-class granted
        entities are folded into the enforced sets (so validate_action, the schema
        projection and the prompt all see them), resource-class ones ride in
        `granted_now` for their own consumers (env injection, fs roots, the secrets
        gate). Always applied over the CONFIG-derived base policy, never stacked.
        """
        from dataclasses import replace

        actions, utils = set(self.actions), set(self.utils)
        run_history, workflows = self.run_history, self.workflows
        for eid in granted_now:
            cls, _, name = eid.partition(":")
            if cls == "action":
                actions.add(name)
            elif cls == "util":
                utils.add(name)
            elif cls == "runs" and _RUNS_RANK.get(name, 0) > _RUNS_RANK.get(run_history, 0):
                run_history = name
            elif cls == "workflows":
                workflows = name
        return replace(self, actions=frozenset(actions), utils=frozenset(utils),
                       run_history=run_history, workflows=workflows,
                       granted_now=frozenset(granted_now), denied_now=frozenset(denied_now))

    def entity_state(self, eid: str) -> str:
        """The four-state verdict for one entity id: 'denied_forever' | 'denied_now' |
        'granted_now' | 'undecided'. (Allowed-forever lives in the native config keys,
        already folded into this policy's sets — callers check those first.)
        """
        if eid in self.denied:
            return "denied_forever"
        if eid in self.denied_now:
            return "denied_now"
        if eid in self.granted_now:
            return "granted_now"
        return "undecided"

    def request_route(self, eid: str, *, blocking_hint: bool = True) -> str:
        """The way out of a denial, per the entity's decision state: an access request
        for an undecided entity, or a firm 'do not re-request' for a declined one. The
        ONE wording source every denial ends with (docs/prompt-anatomy.md pins it).
        """
        state = self.entity_state(eid)
        if state == "denied_forever":
            return (f"The user has PERMANENTLY declined {eid} for this routine — do not "
                    f"request it again; work without it and note the limitation in your "
                    f"summary if it matters.")
        if state == "denied_now":
            return (f"The user declined {eid} for THIS RUN — do not re-request it now; "
                    f"work without it.")
        if self.is_subrun:
            # R404/F351: a child cannot file access requests (availability.request_denial
            # refuses them), so hinting `ask_user with request:` here sent children into
            # a dead end that ended as a false "weak model" forced-finish verdict.
            return (f"Sub-workflows cannot request access. If {eid} is essential, name it "
                    "in your finish summary so the top-level run can request it.")
        hint = (' with mode "blocking" if you cannot proceed without it (deferred '
                "otherwise)" if blocking_hint else "")
        return (f'If it is essential, request it: ask_user with request: "{eid}" and a '
                f"question saying what you need it for{hint}. The user decides: allow/deny, "
                f"once or forever.")

    def may_generate_workflow(self) -> bool:
        """May a subtask DRAFT a new library pattern when none fits (vs pick from the catalog)?
        Off by default — a user-set capability, covered by the workflow-generation permission.
        """
        return self.workflows == "generate"

    def needs_confirm(self, creating: bool) -> bool:
        """Must the user approve this write_util? (creating=False → revising an existing util)"""
        return self.confirm == "always" or (self.confirm == "creations" and creating)

    def needs_rule_confirm(self, creating: bool) -> bool:
        """Must the user approve this write_rule? (creating=False → revising an existing rule)

        Its own dial, deliberately: a rule revision reaches every routine holding it, so the
        decision is not the same one as authoring a util for yourself.
        """
        return (self.rule_confirm == "always"
                or (self.rule_confirm == "creations" and creating))

    def _deny_util(self, action: dict) -> str | None:
        """The reserved-util gate. A util is granted BY NAME (`capabilities.utils`), BY TAG
        CLASS (`util_tags` — covers every util in the class, including ones the library gains
        later), or BY VERB (`name:verb` — that one subcommand, matched against the call's
        first positional argument, which is how read-only access to a channel is expressed).
        """
        name = str(action.get("name") or "")
        if name not in self.gated_utils or name in self.utils or self.admin:
            return None
        if set(self.util_tag_index.get(name, ())) & self.util_tags:
            return None
        args = action.get("args") or []
        verb = str(args[0]) if args and isinstance(args[0], str) else ""
        scoped = {v for u in self.utils
                  if (n := split_util_verb(u))[0] == name and (v := n[1])}
        if scoped:
            if verb in scoped:
                return None
            miss = f"{verb!r} is not one of those" if verb else "this call names no verb"
            return (f"util {name!r} is granted to this routine only for: "
                    f"{', '.join(sorted(scoped))}. {miss} — a read-only channel is not a "
                    f"write one. {self.request_route(f'util:{name}')}")
        perms = ", ".join(self.gated_utils[name])
        return (f"util {name!r} is a reserved capability switched OFF for this "
                f"routine — this channel is off limits (the {perms} permission "
                f"covers its conduct). {self.request_route(f'util:{name}')}")

    def deny(self, action: dict) -> str | None:
        """A precise, actionable rejection for a gated call — or None when permitted. Worded
        for the model inside the schema-retry cycle: capabilities are switched by the USER
        (on the routine's Permissions panel), so route to ask_user.
        """
        kind = action.get("kind")
        # Create and revise are ONE action kind but two permissions: writing a NEW util adds
        # a capability nobody had, revising an existing one changes what every caller already
        # gets. Which act this is depends on the target, not on the call — so the capability
        # the call actually needs is resolved here, then gated like any other.
        need = kind
        mode = ""
        if kind == "write_util":
            name = str(action.get("name") or "")
            revising = name in self.known_utils
            need = "revise_util" if revising else "write_util"
            mode = (f"util {name!r} {'already exists' if revising else 'does not exist yet'}, "
                    f"so this is a {'REVISION' if revising else 'CREATION'}. ")
        if need in GATED_KINDS and need not in self.actions and not self.admin:
            srcs = ", ".join(self.kind_sources.get(need)
                             or [_DEFAULT_KIND_SOURCE.get(need, "util-authoring")])
            kind = need
            if self.is_subrun:
                # A spawned/subtask child runs with capabilities OFF by design, regardless
                # of what the parent routine holds — so the limit is the CHILD's scope, not
                # the routine's. Route the work back to the parent, which may hold the kind.
                return (f"kind={kind} is not available to this child sub-workflow — spawned "
                        f"and subtask children run with capabilities switched off (the "
                        f"{srcs} permission is enforced on the parent run, not inherited). "
                        f"Do the work that needs {kind} in the PARENT run, or return the "
                        f"material it needs in your finish summary so the parent can.")
            return (f"{mode}kind={kind} is switched OFF in this routine's capabilities — "
                    f"only the user can switch it on (the {srcs} permission covers its "
                    f"conduct). Work with what you have. "
                    f"{self.request_route(f'action:{kind}')}")
        if kind == "util":
            refusal = self._deny_util(action)
            if refusal is not None:
                return refusal
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
                    if self.run_history == "none" and not self.admin:
                        # post-D96 a routine's own policy floors at "last" — this fires
                        # only for scopes without history (sub-workflow children).
                        return ("previous runs under runs/ are not readable in this "
                                "scope — a routine reads its own last run by default, "
                                "but sub-workflows run on their brief alone. "
                                f"{self.request_route('runs:all')}")
                if writes and _norm_rel(path).split("/")[-1] == CONFIG_FILE:
                    return (f"writing {_norm_rel(path)!r} would change routine config "
                            f"(routine.yaml — permissions, capabilities, budgets, roots). Config "
                            f"is the user's: NO run edits it, not even the routine-improver "
                            f"(machine-tunable knobs like deliberation live in tuning.yaml). "
                            f"File a deferred ask_user describing the change you need.")
                if writes and is_recipe_path(path) and not self.recipe_unlocked:
                    return (f"writing {_norm_rel(path)!r} would modify this routine's own recipe "
                            f"(main.md / stages/ / tuning.yaml) — a run never edits its own "
                            f"recipe; the routine-improver meta routine refines it. File a "
                            f"deferred ask_user describing the change instead.")
        return None


