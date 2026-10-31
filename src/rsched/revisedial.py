"""The `revise_confirm` dial — who may rewrite THIS routine's recipe without a card (D198).

The operator's answer (2026-10-19) to "`create_routine` can create but not REVISE a routine —
who may rewrite whose recipe, and under which approval dial?" is one sentence: *"Add a revise
path to `create_routine` (or a sibling action) gated by a per-routine dial, like
`config_confirm`"*. This is that dial; `engine/reviseroutine.py` is that path.

WHOSE dial it is — the sentence does not say, and the tree does. It belongs to the routine
whose recipe would be REWRITTEN, never to the caller proposing the rewrite. The model the
answer names settles it: `configdial.refusals(..., cross_routine=True)` answers "a change to
someone else's config is always the operator's decision, whatever this routine's
`config_confirm` says" (`configdial.py:138-140`), and `engine/autoask.py:24` says the same of
its levels — "`config_confirm` is a per-routine dial and no value of it speaks for the other
routine". A caller-side dial would be exactly that: routine A's own setting authorizing a
rewrite of routine B's instructions. So the authority sits with the subject of the change.

Its ladder is its OWN, as `config_confirm`'s is, and it has two rungs rather than three
because neither of that dial's shapes exists here: there is no create-vs-revise rung (every
revision revises) and no field allow-list to make a middle rung from (a recipe is prose).

- `always` — the DEFAULT for every routine, and today's behaviour: a revision another routine
  proposes for this one is a decision card, and nothing reaches the recipe until the operator
  approves it.
- `invited` — a revision proposed by a routine the operator already trusts with recipes (the
  `write_recipe` capability) is armed on this routine's NEXT run with no card. It is still not
  a write by the proposer: the invitation hands the PROSE to the run that owns the recipe
  (`rsched/reciperevise.py`), which edits its own files under the one-leg unlock
  `engine/revise.py` already implements, records it in its LEDGER, and the engine commits it.

WHAT NO LEVEL DOES, stated here rather than left to each caller:

- `routine.yaml` is never touched. Config — permissions, capabilities, budgets, roots — is the
  operator's at every level (`engine/fileops._write_gate`); a revision that needs a config
  change files it as its own decision, exactly as a revise leg does today.
- No level lets the PROPOSER write the target's files. A level that let A edit B's `main.md`
  directly would be a second route to a boundary the engine guards per run — and the measured
  precedent for a second legitimate-looking route is `config-optimizer` reaching admin API
  authority through a token fallback (R2615) while breaking no rule it was told.

Pure policy over its own vocabulary, importing nothing, so the engine, the web layer and the
routine page all read one answer (`configdial`'s reason for the same shape).
"""

from __future__ import annotations

#: The capabilities key, and its ladder least → most permissive.
DIAL = "revise_confirm"
LEVELS = ("always", "invited")
#: What a routine holds when the mapping leaves the dial out — today's behaviour, for every
#: routine, so installing this dial changes nothing until the operator turns one.
DEFAULT_LEVEL = "always"
#: What a PROPOSER must hold to propose a revision at all: the SAME capability that lets a
#: routine rewrite its own recipe (`grants.CAPABILITY_ACTIONS`), not a new one. The answer asks
#: for a dial, and a second capability would be a second decision the sentence does not make.
PROPOSER_CAPABILITY = "write_recipe"


def level_of(caps: object) -> str:
    """The dial a capabilities mapping holds, defaulting to `always` — the ONE reading, so the
    routine page cannot show one value while the handler enforces another (which is what
    `configdial.level_of` exists for, after an omitted `confirm` read two ways).
    """
    value = (caps or {}).get(DIAL) if isinstance(caps, dict) else None
    return value if value in LEVELS else DEFAULT_LEVEL


def armed_without_a_card(caps: object) -> bool:
    """Would a revision for THIS routine be armed on its next run without the operator's click?

    An unrecognised value reads as the DEFAULT, never as the widest: a typo in `routine.yaml`
    must fail closed, which is `configdial.allowed_fields`' rule as one boolean.
    """
    return level_of(caps) == "invited"
