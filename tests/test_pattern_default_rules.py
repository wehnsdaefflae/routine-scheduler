"""Every shipped pattern carries the DEFAULT rules, so following one never costs a rule."""
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
