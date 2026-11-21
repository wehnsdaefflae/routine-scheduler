"""The wrong-turn fold's CAUSE masking — what makes one mistake one row.

`readmodels/wrong_turns.cause()` turns a validator's rejection message into the key the fold
groups on: the values are masked out, the shape is kept. Its own correctness is a separate
responsibility from recording the event (`test_wrong_turn_event.py`), and it has one input the
event tests never produce — a detail the RECORDER truncated. `engine/completion.py` keeps
`str(exc)[:200]`, so a long message arrives cut, often mid-value.
"""

from __future__ import annotations

# --- the fold's mask survives the TRUNCATION the recorder applies ------------------------

#: The real shape of the problem, from `.control/health-events.jsonl` on 2026-11-21: the
#: recorder keeps `str(exc)[:200]`, so a long validator message arrives cut — 109 of that
#: window's 333 rows, 83 of them inside an unterminated quote. A mask that required the
#: CLOSING delimiter left each of those details carrying its own value, so one mistake was
#: filed as one row per occurrence: `accounting: '…'` was 18 turns across 12 routines as 18
#: singletons, and `kind: '…' is not one of {…}` 16 turns as 4 fragments whose biggest showed
#: 9. Both fell under `MAX_ROWS`, which is the bound that makes the fold readable — so the
#: classes that recurred most were the ones the answer could not show.
TRUNCATED_PAIRS = [
    ("accounting: '[\"d2 met: every part of the subject was read live this run",
     "accounting: '[\"d2 met: a different run's own accounting, cut at another point"),
    ("kind: 'code-search' is not one of ['util', 'write_util', 'remove_util', 'read_file",
     "kind: 'websearch' is not one of ['util', 'write_util', 'remove_util', 'read_fi"),
    ("paths: ['STATE.md', 'WORKLOG.md', 'state/obligations.json', 'stages/orient",
     "paths: ['/srv/tagfs/state/units.json', '/srv/tagfs/state/feed-status.json"),
]


def test_a_detail_cut_mid_value_folds_with_the_same_mistake_cut_elsewhere():
    """Two runs make the SAME mistake, and the recorder cuts each message at a different
    point. They are one row — otherwise the fold counts occurrences of a truncation, not
    occurrences of a mistake."""
    from rsched.readmodels.wrong_turns import cause

    for left, right in TRUNCATED_PAIRS:
        assert cause(f"turn 12: {left}") == cause(f"turn 300: {right}"), (
            f"a cut value is still carried into the cause:\n  {cause(left)}\n  {cause(right)}")


def test_a_complete_detail_is_masked_exactly_as_before():
    """The guard on the fix: the trailing branches are anchored at the end of the string, so a
    detail whose values all close is matched by the paired branch first. Every one of these is
    a real row from the stream, with its quotes, brackets and braces balanced."""
    from rsched.readmodels.wrong_turns import cause

    assert cause("turn 3: max_lines: 2500 is greater than the maximum of 500") == (
        "max_lines: N is greater than the maximum of N")
    assert cause("turn 59: kind=ask_user: ['options'] belong to EACH question when you pass "
                 "'questions'") == (
        "kind=ask_user: ['…'] belong to EACH question when you pass '…'")
    assert cause("turn 50: remind_feedback: [{'id': 'rem-1', 'label': 'would_have'}, "
                 "{'id': 'rem-2', 'label': 'didnt'}] is not of type 'object'") == (
        "remind_feedback: {…} is not of type '…'")
    # a brace that DOES close cannot reach the unclosed branch, however long the detail
    assert cause("turn 7: paths: ['a', 'b'] has too many items") == (
        "paths: ['…', '…'] has too many items")


def test_two_different_mistakes_do_not_fold_into_one():
    """The opposite failure the fix could introduce: masking a cut value must not swallow the
    text that DISTINGUISHES two messages, or the fold would merge mistakes and report a cause
    nobody can act on."""
    from rsched.readmodels.wrong_turns import cause

    assert cause("turn 1: accounting: 'b1 met: something") != cause(
        "turn 1: paths: ['one.md', 'two.md")
    assert cause("turn 1: kind=report: 'closes' is valid only together with 'answers'") != (
        cause("turn 1: kind=report: 'supersedes' needs 'target'"))

