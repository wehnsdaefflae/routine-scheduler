"""What the `scripted` fixture guarantees beyond replaying replies: no test sleeps at
production pace.
"""

from __future__ import annotations

from rsched.engine import actionroute, loop, loopconst


def test_production_poll_is_the_slow_one():
    """The constant itself is unchanged — this is a test-harness fix, not a pace change."""
    assert loopconst.POLL_S == 2.0


def test_scripted_patches_every_reader_of_poll_s(scripted):
    scripted([])
    assert loop.POLL_S < 0.1
    assert actionroute.POLL_S < 0.1
