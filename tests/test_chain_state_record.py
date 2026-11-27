"""A chain exhaustion records WHICH members were unusable, so the record distinguishes a chain
that was genuinely all down from one abandoned forward past a member whose cooldown had already
lapsed. Before these fields the event said only "failed hard or is cooling" and both cases read
identically; recovering the difference meant differencing timestamps against COOLDOWN_S across
the health stream, which a provider-lengthened Retry-After defeats outright. The reader is
deliberately read-only: it reports an expired mark as not cooling without clearing it, so asking
what the chain looked like can never change what the next pick or failover sees.
"""

from __future__ import annotations

import time

from rsched.config import ModelRef
from rsched.endpoints import failover


def _ref(name: str, *, endpoint: str = "ep", model: str | None = None) -> tuple:
    return object(), ModelRef(endpoint=endpoint, model=model or f"id-{name}", name=name)


def test_every_member_is_reported_with_its_cooldown_remaining():
    failover.reset()
    chain = [_ref("a"), _ref("b"), _ref("c")]
    failover.mark_failed("ep", "id-b", cooldown_s=120.0)
    state = failover.chain_state(chain)
    assert [m["name"] for m in state] == ["a", "b", "c"], state
    assert [m["cooling"] for m in state] == [False, True, False], state
    assert 0 < state[1]["cooldown_left_s"] <= 120.0, state
    # a member with no mark carries no remaining time at all, never a negative or a null
    assert state[0]["cooldown_left_s"] == 0.0 and state[2]["cooldown_left_s"] == 0.0


def test_a_lapsed_cooldown_reads_as_usable_which_is_the_whole_point():
    failover.reset()
    chain = [_ref("head"), _ref("fallback")]
    failover.mark_failed("ep", "id-head", cooldown_s=0.01)
    assert failover.chain_state(chain)[0]["cooling"] is True
    time.sleep(0.03)
    state = failover.chain_state(chain)
    assert state[0]["cooling"] is False, "an expired mark must read as usable"
    assert state[0]["cooldown_left_s"] == 0.0, state


def test_reading_the_state_changes_nothing_the_failover_will_see():
    """The reader must not clear an expired mark, because `pick` and `next_after` decide on
    the same registry and a reader with a side effect would make an exhaustion's own record
    part of the chain's behaviour.
    """
    failover.reset()
    chain = [_ref("a"), _ref("b")]
    failover.mark_failed("ep", "id-a", cooldown_s=0.01)
    time.sleep(0.03)
    before = failover.pick(chain)
    failover.chain_state(chain)
    failover.chain_state(chain)
    assert failover.pick(chain) is before, "reading the state moved the chain"


def test_the_exhaustion_event_carries_the_members_and_the_usable_count(tmp_path):
    """The two fields a sweep needs: the per-member list, and a count it can group on without
    parsing that list. `not_cooling` excludes the member that just failed, since that one is
    cooling by construction at this seam and counting it would report every exhaustion as
    having had a usable member left.
    """
    from types import SimpleNamespace

    from rsched.engine import degrade

    failover.reset()
    chain = [_ref("head"), _ref("mid"), _ref("tail")]
    failover.mark_failed("ep", "id-mid", cooldown_s=300.0)
    failover.mark_failed("ep", "id-tail", cooldown_s=0.01)
    time.sleep(0.03)                                  # tail's mark lapses; mid's does not
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True)
    loop = SimpleNamespace(ctx=SimpleNamespace(
        server=SimpleNamespace(routines_home=home),
        routine=SimpleNamespace(slug="probe"), run_id="probe:20260717-080000"))
    degrade._log_chain_exhausted(loop, chain, chain[0][1],
                                 Exception("provider rejected the request"))
    import json
    rows = [json.loads(line) for line
            in (home / ".control" / "health-events.jsonl").read_text().splitlines() if line]
    row = next(r for r in rows if r["event"] == "model_chain_exhausted")
    assert [m["name"] for m in row["chain_members"]] == ["head", "mid", "tail"], row
    assert row["chain_members"][1]["cooling"] is True
    assert row["chain_members"][2]["cooling"] is False
    # head failed at this seam and is excluded; mid is cooling; only tail counts as usable
    assert row["not_cooling"] == 1, row
