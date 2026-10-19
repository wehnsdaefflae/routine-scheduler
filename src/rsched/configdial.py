"""The `config_confirm` dial — which of a routine's OWN config changes need no decision (D191).

A run can never write `routine.yaml`: it proposes the change on an `ask_user` carrying a
`config_patch`, and the Decisions page applies it with one click. Measured 2026-10-17 across
176 question records since 2026-10-01, 39 of them were that one shape — config-optimizer 13,
routine-improver 8, self-audit 7: the three routines whose whole job is proposing
configuration. Every one of those cost the operator a read and a click for a change he had
already decided in principle, which is the friction he named on 2026-10-16 ("they ask for
permission to do their job, every fucking time").

The dial is his answer, decided 2026-10-18, and it is the FOURTH approval dial beside
`confirm` (write_util), `rule_confirm` (write_rule) and `remind_confirm` (a shared reminder).
It is a separate dial rather than a widening of those for the reason they are separate from
each other: each blast radius is a different decision, and collapsing two would make one
permissive answer silently authorize the other.

Its ladder is its OWN, which is why it is not simply another member of `CONFIRM_LEVELS`:

- `always` — today's behaviour, and the DEFAULT for every routine. Nothing changes until the
  operator sets the dial: every config change the routine proposes is a decision card.
- `own_fields` — a patch the routine proposes FOR ITSELF, naming only fields in the declared
  allow-list below, is applied without a card. Anything else is still a decision.
- `never` — no confirmation for the routine's own patches, in any field EXCEPT the sealed set.

WHAT `never` ADDS OVER `own_fields`, stated plainly because the two levels are otherwise easy
to read as the same thing: `own_fields` is the allow-list; `never` is every patch field that is
not SEALED. So `never` reaches `schedule`, `run_gate`, `rules`, `outcome`, `improve`, `enabled`
and the labels, which `own_fields` does not. That includes a routine taking ITSELF off the
schedule (a `schedule.friendly` of "disabled" writes `enabled: false`), and a routine rewriting
the outcome contract it is measured against. Those are real consequences, which is why `never`
is a deliberate per-routine choice and `own_fields` is the level this dial was built for.

THE SEALED SET IS ABSOLUTE. No value of this dial — `never` included — lets a run patch it
autonomously. The operator named five on 2026-10-18 (`permissions`, `capabilities`,
`fs_read_roots`, `fs_write_roots`, anything under `models.`); the other four here are the same
surface under different names, and are sealed for reasons given beside them. Enforcement is
the APPLY's, never the asking run's restraint: a second legitimate-looking route to the same
boundary is worse than the first, and the measured precedent is `config-optimizer` reaching
admin API authority through a token fallback (R2615) while breaking no rule it was told.

This module is pure policy over `configflow`'s vocabulary and imports nothing else, so the
engine, the daemon and the web layer can all read the same answer. The WRITE lives where the
one validated writer already is (`web/patch_authority.guard_autonomous`, called by
`web/api_routine_patch.apply_updates` before anything is written).
"""

from __future__ import annotations

from .configflow import ROUTINE_PATCH_FIELDS

#: The capabilities key, and its ladder least → most permissive.
DIAL = "config_confirm"
LEVELS = ("always", "own_fields", "never")
#: What a routine holds when the mapping leaves the dial out — today's behaviour, for every
#: routine, so installing this dial changes nothing until the operator turns one.
DEFAULT_LEVEL = "always"

#: Never autonomous, whatever the dial says. Each line says why it is on this list, because a
#: seal whose reason is unrecorded is a seal a later change argues away.
SEALED: frozenset[str] = frozenset({
    # the operator's five (2026-10-18) — the authority surface and the role bindings
    "permissions",      # the held conduct docs: what the routine may reach at all
    "capabilities",     # the machine-enforced half of that same authority
    "fs_read_roots",    # what it may read — the sandbox jail
    "fs_write_roots",   # what it may write — likewise
    "models",           # a role naming an absent model takes the routine dark (F643)
    # the same surface under other names
    "trial",            # a TrialConfig CARRIES a models mapping (config/trialconf.py), so an
    #                     autonomous trial is an autonomous role binding
    "grants",           # the decision rows: secret exposure and deny-forever tombstones
    "connections",      # OAuth account bindings — which account's data it reaches
    "machines",         # the SSH hosts it may act on
})

#: The DECLARED autonomous allow-list of `own_fields`: the knobs whose blast radius is the
#: routine's own run, and which a routine can justify from its own measured behaviour — which
#: is exactly what the 39 asks were about. Deliberately NOT here: `schedule` and `enabled` (a
#: routine must not quietly stop running), `run_gate` (admission is the operator's floor),
#: `outcome` (a routine marking its own homework), `improve` (opting out of the improver),
#: `rules` and `shared_reminders` (prose and holds that reach other routines' surfaces), and
#: the labels, which are cheap to click and are how a person finds a routine again.
OWN_FIELDS: frozenset[str] = frozenset({
    "budgets",              # the runaway backstops — the measured majority of the 39 asks
    "efforts",              # reasoning effort per role: a fixed vocabulary, a cost/quality knob
    "deliberation",         # the say contract's level
    "keep_runs",            # run retention
    "ladder_rung_height",   # the escalation ladder's interval …
    "oversight_turns",      # … and a rung's turn cap: tuning a meta-routine re-levels on evidence
})


def level_of(caps: object) -> str:
    """The dial a capabilities mapping holds, defaulting to `always` — the ONE reading, so a
    page cannot show one value while the apply enforces another (which is how an omitted
    `confirm` once read two ways, `grants.effective_settings`).
    """
    value = (caps or {}).get(DIAL) if isinstance(caps, dict) else None
    return value if value in LEVELS else DEFAULT_LEVEL


def allowed_fields(level: str) -> frozenset[str]:
    """The patch fields this dial value may apply autonomously for the routine's OWN config.
    An unrecognised value reads as the DEFAULT, never as the widest — a typo in routine.yaml
    must fail closed.
    """
    if level == "never":
        return frozenset(ROUTINE_PATCH_FIELDS) - SEALED
    if level == "own_fields":
        return OWN_FIELDS
    return frozenset()


def _sealed_keys(patch: dict) -> list[str]:
    """The patch's keys that touch the sealed surface, as the caller spelled them. The
    dotted form is checked too: the PATCH surface is flat, but a proposal naming
    `models.main` must not slip past a membership test on `models`.
    """
    return sorted(k for k in patch if k in SEALED or str(k).split(".", 1)[0] in SEALED)


def refusals(patch: dict, level: str, *, cross_routine: bool = False) -> list[str]:
    """Why this patch may NOT be applied without a decision — empty when it may.

    ALL OR NOTHING, in the order the reasons outrank each other: a patch with one sealed key
    is refused whole rather than applied in part. A partial autonomous apply would settle the
    record as handled while silently dropping the half that needed the operator — the one
    state a decision surface must never be in.
    """
    if not patch:
        return ["the config_patch names no field to apply."]
    if sealed := _sealed_keys(patch):
        return [(f"config_patch key(s) {', '.join(repr(k) for k in sealed)} are SEALED: no "
                 f"{DIAL} value lets a run change its own authority surface, role bindings or "
                 f"sandbox roots autonomously. This stays the operator's decision.")]
    if cross_routine:
        return [(f"the config_patch names another routine: a change to someone else's config "
                 f"is always the operator's decision, whatever this routine's {DIAL} says.")]
    if level not in ("own_fields", "never"):
        return [(f"{DIAL} is 'always' for this routine: every config change it proposes is the "
                 f"operator's decision.")]
    allowed = allowed_fields(level)
    if stray := sorted(k for k in patch if k not in allowed):
        return [(f"{DIAL} is {level!r} and config_patch key(s) "
                 f"{', '.join(repr(k) for k in stray)} are not autonomous at that level. "
                 f"Autonomous here: {', '.join(sorted(allowed)) or 'nothing'}.")]
    return []


def may_apply(patch: dict, level: str, *, cross_routine: bool = False) -> bool:
    """Would the apply take this patch without the operator's click?"""
    return not refusals(patch, level, cross_routine=cross_routine)
