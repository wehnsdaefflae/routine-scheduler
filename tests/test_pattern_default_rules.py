"""Every shipped pattern carries the DEFAULT rules, so following one never costs a rule.

A pattern's `rules` list REPLACES the computed defaults rather than adding to them:
`workflows/scaffold` builds `rules: active_rules` (DEFAULT_RULES ∩ library, plus the workflow's
own includes) and then spreads `patterns/apply.routine_yaml(settings)` OVER that config, and
`rules` is one of the keys that function copies through. So a rule missing from a pattern is a
rule missing from every routine created while following it — silently, because a pattern's list
is honoured rather than diffed against the defaults.

MEASURED 2026-11-12, which is why this file exists: all fourteen shipped patterns omitted
`outcome-first` — and only `outcome-first`, the newest default rule, six of the seven being
present in all fourteen. The patterns were written in `307e127b` before that rule existed and
nothing compared the two lists afterwards. `git log -G` on the installed `project-steward.yaml`
shows exactly one commit touching those lines, so nobody had narrowed it by hand either. The
consequence was live: of 26 routines on this instance, 25 held the rule, and the one that did
not — `drawerrr-irreversible-actions` — was the newest and the only one born from a pattern
since the rule was added. The rule it lost is the one that makes a run answer for what it
PRODUCES.

Its sibling `test_patterns.py` already pins this shape for budgets
(`test_a_shipped_pattern_carries_every_budget`, `set(budgets) == set(DEFAULT_BUDGETS)`); rules
never had the equivalent. This lives in its own module because that file sits at its size
ratchet ceiling and may only shrink.

A pattern may hold MORE rules than the defaults — that is its whole purpose (`audit-coverage`
for a watcher, `write-as-the-principal` for a steward). It may not hold fewer.
"""
from __future__ import annotations

from test_patterns import _seed_patterns


def test_every_shipped_pattern_carries_every_default_rule():
    from rsched.config.base import DEFAULT_RULES

    for p in _seed_patterns():
        missing = [r for r in DEFAULT_RULES if r not in p["settings"]["rules"]]
        assert not missing, (
            f"pattern {p['slug']!r} omits default rule(s) {missing} — a pattern's rules list "
            f"REPLACES the defaults, so every routine created while following it loses them")


def test_the_defaults_are_all_rules_the_seed_library_actually_ships():
    """The other half of the same guard: a DEFAULT_RULES entry the seed library has no document
    for would be dropped by `scaffold`'s own `if r in available_rules` filter — so adding it to
    all fourteen patterns would satisfy the test above while still reaching no routine.
    """
    from rsched import library_docs
    from rsched.config.base import DEFAULT_RULES

    shipped = set(library_docs.slugs(_seed_patterns.__globals__["SEED"] / "rules"))
    assert not [r for r in DEFAULT_RULES if r not in shipped]
