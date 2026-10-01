"""`GrantPolicy` — what a run may actually DO, and the one place that decides it.

Split out of `grants.py` (F393) along the line that already existed conceptually: `grants.py`
is the capability VOCABULARY and how config is read into it; this is ENFORCEMENT. Every gate in
the engine asks this object — may this action kind be emitted, may this util run, may this path
be written, may this run read an earlier one — and it answers from the routine's OWN
capabilities plus the run's one-time grant overlay, never from a permission doc. A doc held
without its capability therefore fails CLOSED, which is the whole reason the two layers are
separate.

The four-state grant model (allow/deny x now/forever, plus allow-once for the once-grantable
classes, `entities.ONCE_CLASSES`) lives here as `entity_state`; the WEB layer writes
forever-decisions to routine.yaml at click time and the engine only ever bridges now-decisions
into the live overlay. No run writes its own config, so this object is read-only with respect
to what created it.

It also owns the two path questions that are POLICY rather than filesystem (`is_recipe_path`,
`is_runs_path`): is this the routine's own recipe (a write there needs `write_recipe`), and is
this under runs/ (engine-owned, read-only to the run).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from . import utilgate
from .grants import (
    _DEFAULT_KIND_SOURCE,
    CONFIG_FILE,
    GATED_KINDS,
    RECIPE_PREFIXES,
    RUN_HISTORY_LEVELS,
)
from .reminders import LEVELS as REMINDER_LEVELS

_RUNS_RANK = {level: n for n, level in enumerate(RUN_HISTORY_LEVELS)}
_REMINDER_RANK = {level: n for n, level in enumerate(REMINDER_LEVELS)}


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


#: The words every denial of an UNDECIDED entity ends with (`GrantPolicy.request_route`).
#: One constant because two readers depend on it: the prompt-anatomy pin and the
#: `capability-denied` assist predicate, which recognises a refusal the run could still ask
#: for by exactly this phrase.
REQUEST_ROUTE_MARK = "request it: ask_user with request:"


@dataclass(frozen=True)
class GrantPolicy:
    """One run's enforcement view: the routine's enabled capabilities, plus (from the
    whole library) which docs cover each capability — so a denial can name the
    permission whose conduct prose the user would activate alongside it.
    """

    active: tuple[str, ...] = ()               # held conduct-permission slugs (prompt prose)
    actions: frozenset = frozenset()           # enabled gated action kinds
    utils: frozenset = frozenset()             # enabled reserved utils (`name` or `name:verb`)
    gated_utils: dict = field(default_factory=dict)   # util → library docs reserving ALL of it
    # util → {verb: docs} for utils a doc reserves only VERB BY VERB (`gmail:send`): every other
    # verb of that util stays open, which is how reading a mailbox stays behind nothing but its
    # credential while sending needs the permission.
    gated_verbs: dict = field(default_factory=dict)
    kind_sources: dict = field(default_factory=dict)  # gated kind → library docs requiring it
    confirm: str = "always"                    # write_util approval policy
    rule_confirm: str = "always"               # write_rule approval policy (own blast radius)
    remind_confirm: str = "always"             # GLOBAL-reminder approval (its own blast radius)
    run_history: str = "none"                  # previous-runs read access: none | last | all
    # Consequence reminders: none | local | global. `local` reads and writes the routine's own
    # store; `global` additionally reads the library's curated one (local overriding it) and is
    # the only level that may WRITE there. See rsched/reminders.py.
    reminders: str = "none"
    # The four-state grant model's persistent NO: entity ids (entities.py) the user has
    # denied FOREVER (routine.yaml `grants:` false rows). deny() stops routing these to a
    # request — the answer is already given.
    denied: frozenset = frozenset()
    # Run-scoped overlay (with_overlay): one-time user decisions for THIS run only —
    # in-memory on the RunContext, folded in here so every consumer reads ONE policy.
    granted_now: frozenset = frozenset()
    denied_now: frozenset = frozenset()
    # own RECIPE writable? (routine.yaml never is.) loopsetup derives it once from the
    # `write_recipe` capability, or a revise leg (engine/revise.py); `with_overlay` raises it
    # when a one-run grant of `action:write_recipe` lands. The recipe set includes tuning.yaml
    # (machine-tunable behavior parameters, e.g. deliberation) — the file boundary IS the
    # permission boundary, no key-level gates.
    recipe_unlocked: bool = False
    # D62 admin conversation: the operator authenticated this leg with RSCHED_ADMIN_TOKEN,
    # so CAPABILITY gating is lifted (gated kinds, reserved utils, previous-run read depth).
    # STRUCTURAL / ownership gates STILL apply — runs/ write, routine.yaml config, the recipe
    # seal, and the root-conversation-only handler gates. Never persisted, never inherited by
    # a subrun (see engine/admin.py). Enforced in allows_kind() and deny() below.
    admin: bool = False
    # The live run's ts: paths under runs/<current_run_ts>/ are the run's OWN tree (status,
    # archived history) and stay readable regardless of run_history — the engine itself
    # points the model there after compaction.
    current_run_ts: str = ""
    # True for a spawned/subtask child run: sub-workflows run with capabilities OFF by
    # design (childrun._sub_routine), independent of what the PARENT routine holds. It
    # only reshapes denial WORDING — a child's gated-kind denial must name the child
    # workflow as the scope that lacks the kind, not claim the routine lacks it (R46).
    is_subrun: bool = False
    # The util library root, so the reserved-util gate can resolve a call's `calls:` TREE and
    # not just its name, and write_util can tell a creation from a revision. None (hand-built
    # policies, tests) skips the tree check and reads every write_util as a creation — the
    # direct-name gate is unaffected either way.
    libraries_home: Path | None = None

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
        actions, utils = set(self.actions), set(self.utils)
        run_history, reminders = self.run_history, self.reminders
        for eid in granted_now:
            cls, _, name = eid.partition(":")
            if cls == "action":
                actions.add(name)
            elif cls == "util":
                utils.add(name)
            elif cls == "runs" and _RUNS_RANK.get(name, 0) > _RUNS_RANK.get(run_history, 0):
                run_history = name
            elif cls == "reminders" and (_REMINDER_RANK.get(name, 0)
                                         > _REMINDER_RANK.get(reminders, 0)):
                reminders = name
        # `write_recipe` is a capability TOKEN carried in `actions` (grants.py CAPABILITY_ACTIONS),
        # but the recipe seal reads the separate `recipe_unlocked` flag, which loopsetup derives
        # ONCE from static config. Re-derive it from the folded set so a grant that landed mid-leg
        # actually unlocks the recipe — the observation already tells the run it is usable now
        # (D135/F498, from R1617). Never lowers it: a routine that holds the capability keeps it.
        return replace(self, actions=frozenset(actions), utils=frozenset(utils),
                       run_history=run_history, reminders=reminders,
                       recipe_unlocked=self.recipe_unlocked or "write_recipe" in actions,
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
        return (f'If it is essential, {REQUEST_ROUTE_MARK} "{eid}" and a '
                f"question saying what you need it for{hint}. The user decides: allow/deny, "
                f"once or forever.")

    @property
    def reminders_on(self) -> bool:
        """Is the consequence-reminder layer active at all for this run? Off means the two
        side fields are projected out of the schema entirely — a channel a run cannot use is
        not described to it.
        """
        return self.reminders != "none"

    def reminder_denial(self, scope: str) -> str | None:
        """May this run WRITE a reminder at that scope — or the refusal saying why not.

        Checked on the `remind` field rather than on a kind, because the field rides every
        action kind, including the always-available ones the kind gate skips.
        """
        if self.admin or _REMINDER_RANK.get(self.reminders, 0) >= _REMINDER_RANK.get(scope, 9):
            return None
        if self.reminders == "none":
            return (f"`remind` is switched OFF in this routine's settings — only the user "
                    f"can switch it on. Record what you learned with `note` or memory_write "
                    f"instead. {self.request_route('reminders:local')}")
        return (f"a GLOBAL reminder holds matching actions in other routines too; "
                f"writing the shared store is the curator's setting — leave it local (the "
                f"honest default until its own tally proves the consequence is general; the "
                f"reviewer of the shared store promotes what earns it), or "
                f"{self.request_route('reminders:global')}")

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

    def needs_remind_confirm(self, creating: bool) -> bool:
        """Must the user approve this GLOBAL reminder write? (creating=False → revising or
        deleting one that is already there)

        Its own dial: a new global reminder starts interrupting routines that never asked for
        it, which is not the decision `confirm` or `rule_confirm` governs.
        """
        return (self.remind_confirm == "always"
                or (self.remind_confirm == "creations" and creating))

    def _kind_denial(self, kind: str, mode: str) -> str:
        """Why a gated kind this run does not hold is refused and the way out."""
        if kind == "detach":
            # STRUCTURAL, not a setting: added to a root conversation's policy at setup and to
            # nothing else, so a denial pointing at a permission would send the run after a
            # switch that does not exist
            return ("kind=detach starts a job that outlives a conversation's REPLY, so it exists "
                    "only in a root conversation — no setting switches it on here. Use spawn or "
                    "subtask for work inside this run.")
        srcs = ", ".join(self.kind_sources.get(kind)
                         or [_DEFAULT_KIND_SOURCE.get(kind, "util-authoring")])
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

    def _util_exists(self, name: str) -> bool:
        """Is `name` already in the library — is this write_util a REVISION?

        Asked at the call, of the live library, whenever the answer can matter: unless BOTH
        halves of the split are held. It used to be a catalog loaded with the policy, and only
        for a routine holding exactly one half. A routine holding neither loaded nothing, so
        an existing util read as a CREATION — the denial asked for `action:write_util` — and a
        one-run grant of that half, folded in by `with_overlay`, then let write_util REVISE the
        existing util. Live also means a util this run created is a revision when it is
        written again.
        """
        if {"write_util", "revise_util"} <= self.actions or self.libraries_home is None:
            return False
        from .utils_lib import exists

        try:
            return exists(self.libraries_home, name)
        except OSError:      # an unreadable library: a creation, as a missing name would be
            return False

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
            revising = self._util_exists(name)
            need = "revise_util" if revising else "write_util"
            mode = (f"util {name!r} {'already exists' if revising else 'does not exist yet'}, "
                    f"so this is a {'REVISION' if revising else 'CREATION'}. ")
        if need in GATED_KINDS and need not in self.actions and not self.admin:
            return self._kind_denial(need, mode)
        if kind == "util":
            refusal = utilgate.deny_util(self, action)
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
                            f"(main.md / stages/ / tuning.yaml), which needs the recipe-authoring "
                            f"permission — this routine does not hold it, so its instructions are "
                            f"the user's. Describe the change you need in a deferred ask_user "
                            f"(or a report). {self.request_route('action:write_recipe')}")
        return None
