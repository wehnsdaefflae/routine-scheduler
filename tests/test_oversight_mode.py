"""Steps 2 and 3 of the ladder: the fourth child-run MODE and the delivery CHANNEL.

Both are small on purpose — three dict entries and three set memberships — and both are tested
because their failure mode is silent. A mode missing from one of the three tables falls through
to a default that reads as "child run", which is exactly the drift `engine/child.py`'s own
docstring says those tables exist to prevent; a via missing from one of the three inbox sets
produces a message that is either never consumed or consumed as the USER speaking.
"""

from rsched.engine import child, inbox

# --------------------------------------------------------------------- the fourth mode

def test_oversight_is_a_mode_with_all_three_names():
    """Fails if OVERSIGHT is added to the constants but missed in a rendering table — the
    surface where it would then read as a generic "child run" and nobody would notice."""
    assert child.OVERSIGHT == "oversight"
    assert child.mode_noun(child.OVERSIGHT) != child.mode_noun("nonsense-mode")
    assert child.mode_shout(child.OVERSIGHT) != child.mode_shout("nonsense-mode")
    assert child.mode_short(child.OVERSIGHT) != child.mode_short("nonsense-mode")


def test_oversight_mode_names_say_supervision_not_delegation():
    """The mode's whole novelty is that authority is INVERTED, so its prose must not read like
    the three delegating modes. Fails if it is described as a sub-workflow or a subtask."""
    noun = child.mode_noun(child.OVERSIGHT)
    assert "oversight" in noun.lower() or "supervis" in noun.lower(), noun
    assert "sub-workflow" not in child.mode_shout(child.OVERSIGHT).lower()


def test_the_three_mode_tables_have_identical_keys():
    """The invariant behind F338: the mode vocabulary is the KEYS of these tables, so a mode
    present in one and absent from another is the bug. Fails on any future fifth mode too."""
    assert set(child.MODE_NOUN) == set(child.MODE_SHOUT) == set(child.MODE_SHORT)
    assert child.OVERSIGHT in child.MODE_NOUN


def test_unknown_mode_still_falls_back_rather_than_raising():
    """The fallback the existing modes rely on must survive the addition."""
    assert child.mode_noun("not-a-mode") == "child run"


# --------------------------------------------------------------------- the channel

def test_oversight_is_a_known_via():
    """A writer must be able to file on it at all: `file_message` refuses a via VIAS does not
    know. Fails if the channel is used before it is declared."""
    assert "oversight" in inbox.VIAS


def test_oversight_is_deliverable_to_a_running_run():
    """The point of the channel. A directive that only reached the next fresh run would arrive
    after the drift it was meant to correct. Fails if it is left off LIVE_MESSAGE_VIAS."""
    assert "oversight" in inbox.LIVE_MESSAGE_VIAS


def test_oversight_is_not_the_user_speaking():
    """`ctx.user_replies` gates the create_routine confirm and the `user-spoke` assist. A
    supervisor advancing it would let machine freight stand in for the operator's consent."""
    assert "oversight" in inbox.MACHINE_VIAS
    assert inbox.user_authored("oversight") is False


def test_oversight_is_not_in_the_user_message_vias():
    """The narrower set means "the user is talking to THIS run" — a resumed leg's boot drains
    only these. A machine channel in it would let a stale directive re-enter at a boot."""
    assert "oversight" not in inbox.USER_MESSAGE_VIAS
