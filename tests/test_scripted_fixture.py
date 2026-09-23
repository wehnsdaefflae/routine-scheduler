"""What the `scripted` fixture guarantees beyond replaying replies: no test sleeps at
production pace.

The engine's blocking waits — a user ask, the three authoring approvals, the two call-time
secret gates, a child `wait` — poll at POLL_S, which is 2 s in production. Two modules import
it by value, and for a long time the fixture patched only one: every ask/approval-shaped test
spent whole 2 s ticks between polls, ~33 s of pure sleep per fast gate over 13 tests. A module
split can restore that silently, so the patched value is asserted where it is READ.
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
