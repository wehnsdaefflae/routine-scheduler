"""The recommender's semantic COVERAGE: every field it may propose is either checked against
the accept route's own validator or named on a decided allowlist.

R2884, measured on 0.447.3: `_semantic_problem` checked four of the 23 keys in
`fields.GOVERNABLE` and the other 19 were offered untested — so a value the SYSTEM generated
was refused on a settings page the person never typed into. Both values the operator actually
hit (`deliberation`, `capabilities`) have validators on the accept route that the recommender
never consulted. The accept is whole-draft, so one refused field 400s every other change the
person kept: that is how `connections: {"fau-mail": {...}}` cost ewsan-bohrdaten-steward all
nine of its proposed settings.

This is the `runrecord.BEHAVIOUR` / `NOT_BEHAVIOUR` idiom applied to that coverage: a new
governable field must be CHECKED or DECIDED, never silently offered untested. The checked set
is read from the dispatch tables `_semantic_problem` and `valid` actually use, never from a
second hand-kept list — a list that agreed with itself while the coverage drifted is the
defect this test exists to catch.
"""
from rsched.patterns import fields
from rsched.patterns.recommend import (
    NO_SEMANTIC_CHECK,
    semantic_checks,
    valid,
    whole_field_checks,
)


def _checked() -> set[str]:
    """The fields the recommender really does check, read from the CODE that checks them:
    `semantic_checks()` is what `_semantic_problem` dispatches on, `whole_field_checks()` is
    what `valid()` answers a whole field with.
    """
    return set(semantic_checks()) | set(whole_field_checks())


def test_every_governable_field_is_checked_or_decided():
    """A field in neither set is offered to the person untested — the R2884 defect."""
    undecided = sorted(set(fields.GOVERNABLE) - _checked() - set(NO_SEMANTIC_CHECK))
    assert not undecided, (
        f"{len(undecided)} governable field(s) the recommender may propose are neither "
        f"semantically checked nor decided: {undecided}. Either add the accept route's own "
        f"validator to recommend.semantic_checks(), or name the field in "
        f"recommend.NO_SEMANTIC_CHECK with the reason it needs none.")


def test_no_field_is_both_checked_and_allowlisted():
    """Contradictory: the allowlist would claim a reason for a check that runs anyway."""
    both = sorted(_checked() & set(NO_SEMANTIC_CHECK))
    assert not both, f"checked AND allowlisted: {both}"


def test_the_allowlist_cannot_rot():
    """Every allowlisted key must still be a governable field, with a reason a person can
    read: a key that stopped being governable (or was renamed) would otherwise sit here
    forever excusing a check nothing asks for."""
    stale = sorted(set(NO_SEMANTIC_CHECK) - set(fields.GOVERNABLE))
    assert not stale, f"allowlisted but not governable: {stale}"
    assert not [k for k, why in NO_SEMANTIC_CHECK.items() if not str(why).strip()]


#: The allowlist as it stood when this test was written (0.447.3, 18 fields). A RATCHET: an
#: entry leaves it when its check is implemented, and nothing new may be added — a field added
#: to `fields.GOVERNABLE` must get a check, not an excuse. Shrink this set in the same change
#: that shrinks the allowlist.
BASELINE_UNCHECKED = frozenset({
    "budgets", "capabilities", "deliberation", "fs_read_roots", "fs_write_roots", "grants",
    "improve", "keep_runs", "ladder", "ladder_turn_interval", "oversight_turns", "permissions",
    "reminders", "rules", "run_gate", "schedule", "tags", "triggers",
})


def test_the_allowlist_only_shrinks():
    """Without this the forcing test is satisfiable by excusing the new field, which is the
    omission it exists to prevent wearing a reason."""
    added = sorted(set(NO_SEMANTIC_CHECK) - BASELINE_UNCHECKED)
    assert not added, (
        f"newly allowlisted: {added}. A governable field gets the accept route's validator in "
        f"recommend.semantic_checks(), not an entry here. If this really is a field nothing can "
        f"refuse, add it to BASELINE_UNCHECKED in the same change and say why.")


def test_each_gap_names_a_validator_the_recommender_does_not_consult():
    """The 13 `GAP —` reasons are the measured defect R2884 found, kept countable so a later
    increment can close them one at a time and watch the number fall."""
    gaps = sorted(k for k, why in NO_SEMANTIC_CHECK.items() if why.startswith("GAP —"))
    assert len(gaps) == 13, gaps
    assert "deliberation" in gaps and "capabilities" in gaps   # the two the operator hit


def test_the_checked_set_still_refuses_what_the_accept_refuses():
    """The dispatch table replaced a chain of `if key == …` branches, so this pins the
    behaviour it must keep: `efforts` is the branch no other test covered."""
    assert not valid("efforts", {"main": "turbo"})          # not an EFFORT_LEVELS level
    assert not valid("efforts", {"no_such_role": "low"})    # not a chat role
    assert valid("efforts", {"main": "low"})
