"""D318. A failover chain may reach BACK to a recovered member, but only when the alternative is
reporting itself exhausted and killing the run — forward-only stays the rule everywhere else, so
the reach-back is reachable only through the branch `next_after` takes when nothing forward is
left (`failover._last_resort`), and the quota half of the forward-only argument is defended here
as hard as the reach-back itself: a cooldown expires, an allowance does not, and both stop
reading as cooling on the same schedule, so a member whose failure was DURABLE must stay
unreachable even as a last resort or this exception becomes the five-minute flap forward-only was
built to stop. The measurement behind it: 13 of 23 chain exhaustions over ten days happened while
the head's cooldown had already lapsed (by 344 s to 11,759 s), and five runs died in one day
after advancing exactly one rung and meeting a second failure.
"""

from __future__ import annotations

import time

import pytest

from conftest import finish as _finish
from conftest import write_file as _write_file
from rsched.config import ModelRef
from rsched.endpoints import failover
from rsched.endpoints.base import EndpointError
from rsched.endpoints.errorsignal import ErrorSignal
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

#: every member takes anything — fit is exercised on its own below
ANY_REQUEST = {"has_media": False, "prompt_tokens": 0}


def _ref(name: str, *, endpoint: str = "ep", model: str | None = None, **attrs) -> tuple:
    return object(), ModelRef(endpoint=endpoint, model=model or f"id-{name}", name=name,
                              **attrs)


@pytest.fixture(autouse=True)
def _clean_registry():
    failover.reset()
    yield
    failover.reset()


# ---- the decision itself ---------------------------------------------------------------------

def test_a_two_member_chain_reaches_back_to_its_recovered_head():
    """The acceptance predicate. The head failed, the chain advanced, the tail then failed too —
    and the head's cooldown has lapsed in the meantime. Before D318 this returned None and the
    run was killed beside a model that could serve it.
    """
    chain = [_ref("head"), _ref("tail")]
    failover.mark_failed("ep", "id-head", cooldown_s=0.01)
    assert failover.next_after(chain, chain[0][1], **ANY_REQUEST) is chain[1]
    time.sleep(0.03)                                    # the head recovers
    failover.mark_failed("ep", "id-tail")               # …and the tail dies
    assert failover.next_after(chain, chain[1][1], **ANY_REQUEST) is chain[0], (
        "the chain reported itself exhausted while its head had recovered")


def test_the_mark_follows_the_reach_back_so_later_turns_do_not_re_fail():
    """Every turn re-resolves through `pick`. A reach-back that left the serving mark on the
    dead tail would spend one failed call per turn rediscovering the same exhaustion.
    """
    chain = [_ref("head"), _ref("tail")]
    failover.mark_failed("ep", "id-head", cooldown_s=0.01)
    failover.next_after(chain, chain[0][1], **ANY_REQUEST)
    time.sleep(0.03)
    failover.mark_failed("ep", "id-tail")
    failover.next_after(chain, chain[1][1], **ANY_REQUEST)
    assert failover.pick(chain) is chain[0], "the serving mark stayed on the failed tail"


def test_a_chain_with_a_usable_member_ahead_still_goes_forward():
    """The other half of the predicate: the exception must be unreachable while anything
    forward remains, even when a recovered member sits behind.
    """
    chain = [_ref("head"), _ref("mid"), _ref("tail")]
    failover.mark_failed("ep", "id-head", cooldown_s=0.01)
    time.sleep(0.03)                                    # the head is usable again…
    assert failover.next_after(chain, chain[1][1], **ANY_REQUEST) is chain[2], (
        "a recovered member behind the failure outranked a usable one ahead of it")
    assert failover.pick(chain) is chain[2]


def test_nothing_recovered_behind_it_is_still_exhaustion():
    """The run must still die when the chain really is down — the forward-only rule's own
    outcome, unchanged."""
    chain = [_ref("head"), _ref("tail")]
    failover.mark_failed("ep", "id-head")               # a full 300 s mark, still cooling
    failover.next_after(chain, chain[0][1], **ANY_REQUEST)
    failover.mark_failed("ep", "id-tail")
    assert failover.next_after(chain, chain[1][1], **ANY_REQUEST) is None


def test_an_unknown_member_is_not_a_reach_back():
    """A ref the chain does not carry has no position, so there is nothing to reach back
    from: the answer stays None rather than defaulting to the head."""
    chain = [_ref("head"), _ref("tail")]
    assert failover.next_after(chain, _ref("ghost")[1], **ANY_REQUEST) is None


def test_the_head_itself_failing_has_nowhere_to_reach_back_to():
    """A one-member chain, and the first rung of any chain: `_last_resort` looks at the
    members BEFORE the failure, and there are none."""
    assert failover.next_after([_ref("only")], _ref("only")[1], **ANY_REQUEST) is None


# ---- the quota half: what must NOT be reached back to ----------------------------------------

def test_a_durably_exhausted_member_is_never_reached_back_to():
    """The whole reason forward-only exists. A spent allowance stops reading as cooling on
    exactly the same schedule as a transient outage, and refuses exactly as before — so the
    reach-back asks whether the failure OUTLASTS its cooldown, not merely whether the mark
    lapsed.
    """
    chain = [_ref("head"), _ref("tail")]
    failover.mark_failed("ep", "id-head", cooldown_s=0.01, durable=True)
    failover.next_after(chain, chain[0][1], **ANY_REQUEST)
    time.sleep(0.03)
    assert not failover.is_cooling("ep", "id-head"), "the mark must really have lapsed"
    assert failover.is_durable("ep", "id-head")
    failover.mark_failed("ep", "id-tail")
    assert failover.next_after(chain, chain[1][1], **ANY_REQUEST) is None, (
        "a quota-exhausted head was handed the run because its cooldown had expired")


def test_the_durable_mark_latches_against_a_later_transient_failure():
    """A model that answered `insufficient_quota` once must not be made reachable again by a
    plain timeout on the same model a minute later."""
    failover.mark_failed("ep", "m", cooldown_s=0.01, durable=True)
    failover.mark_failed("ep", "m", cooldown_s=0.01)     # a transient one on top
    assert failover.is_durable("ep", "m")


def test_clearing_a_mark_clears_its_durability():
    """`clear` is for a failure later explained by the REQUEST rather than the provider (the
    media fallback). The provider was not at fault, so neither is its allowance."""
    failover.mark_failed("ep", "m", durable=True)
    failover.clear("ep", "m")
    assert not failover.is_durable("ep", "m")


def test_a_spent_allowance_is_classified_durable_and_a_throttle_is_not():
    """Where the distinction is READ: the provider's declared enum, never its prose. A
    per-minute throttle and a weekly quota both arrive as HTTP 429, so the status alone cannot
    decide it, and treating the throttle as durable would strand a model that was only busy.
    """
    quota = EndpointError("429", retryable=True,
                          signal=ErrorSignal(status=429, code="insufficient_quota",
                                             structured=True))
    throttle = EndpointError("429", retryable=True,
                             signal=ErrorSignal(status=429, code="rate_limit_exceeded",
                                                structured=True))
    assert failover.durable_for(quota) is True
    assert failover.durable_for(throttle) is False
    # a rejected credential outlasts a cooldown as surely as a spent quota
    assert failover.durable_for(EndpointError(
        "401", signal=ErrorSignal(status=401, structured=True))) is True
    # and an undecidable failure stays recoverable: one wasted attempt beats a stranded model
    assert failover.durable_for(EndpointError("boom")) is False
    # the engine's own findings (a refusal, an empty reply) are not allowance facts at all
    assert failover.durable_for(EndpointError(
        "refused", signal=ErrorSignal(engine="refusal"))) is False


def test_the_chain_record_says_which_lapsed_marks_were_refused():
    """`cooling: false, durable: true` is the one combination a reader of a chain exhaustion
    would otherwise misread as a member the run could have gone back to."""
    chain = [_ref("head"), _ref("tail")]
    failover.mark_failed("ep", "id-head", cooldown_s=0.01, durable=True)
    time.sleep(0.03)
    state = failover.chain_state(chain)
    assert state[0]["cooling"] is False and state[0]["durable"] is True, state
    assert state[1]["durable"] is False, state


# ---- the reach-back obeys the same preferences as a forward step ------------------------------

def test_the_reach_back_prefers_a_member_that_can_take_this_request():
    """Images and prompt size travel with the request going backwards too: a text-only rung
    400s on an image turn whichever direction it was reached from."""
    chain = [_ref("textonly", multimodal=False, context_tokens=200_000),
             _ref("roomy", multimodal=True, context_tokens=200_000),
             _ref("tail", multimodal=True, context_tokens=200_000)]
    failover.mark_failed("ep", "id-tail")
    got = failover.next_after(chain, chain[2][1], has_media=True, prompt_tokens=0)
    assert got is chain[1], "an image turn was reached back to a text-only member"


def test_chain_order_decides_between_recovered_members():
    """The configured head is preferred to a rung below it — the same ordering a forward step
    uses, so the run returns to the model it was configured with when it can."""
    chain = [_ref("head"), _ref("mid"), _ref("tail")]
    failover.mark_failed("ep", "id-tail")
    assert failover.next_after(chain, chain[2][1], **ANY_REQUEST) is chain[0]


def test_a_reach_back_cannot_oscillate_within_a_turn():
    """Each failure re-cools the member that produced it, so a member can serve a given turn at
    most twice — once forward, once as the last resort — and the next exhaustion returns None.
    """
    chain = [_ref("head"), _ref("tail")]
    failover.mark_failed("ep", "id-head", cooldown_s=0.01)
    assert failover.next_after(chain, chain[0][1], **ANY_REQUEST) is chain[1]
    time.sleep(0.03)
    failover.mark_failed("ep", "id-tail")
    assert failover.next_after(chain, chain[1][1], **ANY_REQUEST) is chain[0]
    failover.mark_failed("ep", "id-head")               # the reach-back failed too
    assert failover.next_after(chain, chain[0][1], **ANY_REQUEST) is None


# ---- the run that used to die ------------------------------------------------------------------

def test_a_run_whose_whole_chain_failed_survives_on_the_recovered_head(make_routine,
                                                                      monkeypatch):
    """The measured case, end to end: a two-member chain, the head fails, the fallback serves and
    then fails too, and the head's cooldown has lapsed by then — the shape that killed five runs
    in one day (self-audit twice, rules-review, llmsectest-weekday, moltbook-heartbeat), each
    after advancing exactly one rung and meeting a second failure. The harness comes from
    `test_failover` by import rather than by copy: that module carries the only wiring of
    `run_routine` over the REAL registry, and it is at its ceiling in `tests/size_ratchet.json`.
    """
    from test_failover import TS, _catalog_server, _wire

    # Both failures happen microseconds apart, so a SHORT cooldown would make this a race with
    # the clock. A zero-length one is the same situation at its limit — a mark that has already
    # lapsed when the chain runs out of members — and it is deterministic.
    monkeypatch.setattr(failover, "cooldown_for", lambda exc: 0.0)
    d = make_routine("last-resort")
    server = _catalog_server(d.parent)
    eps = _wire(monkeypatch, server, {
        # the head fails, serves again after the reach-back, and does the run's work there
        "epA": [EndpointError("epA is down"),
                _write_file("state/probe.txt", say="grounding work"),
                _finish(summary="served after the chain reached back to me")],
        "epB": [EndpointError("epB is down too")]})

    status, run_dir = run_routine(d, server, run_ts=TS)

    assert status == "ok", "the run died although its head had recovered"
    assert len(eps["epA"].calls) == 3 and len(eps["epB"].calls) == 1
    events, _ = read_events(run_dir / "transcript.jsonl")
    switches = [e["payload"]["failover"] for e in events
                if e["type"] == "error" and e["payload"].get("failover")]
    assert [s["to"] for s in switches] == ["backup", "prime"], switches
    # the forward switch keeps its plain payload; only the reach-back is marked
    assert "last_resort" not in switches[0]
    assert switches[1]["last_resort"] is True
    # and the run never filed a chain exhaustion, because the chain was not exhausted
    rows = (server.routines_home / ".control" / "health-events.jsonl")
    assert "model_chain_exhausted" not in (rows.read_text(encoding="utf-8")
                                           if rows.exists() else "")


# ---- forward-only stays true everywhere it was ------------------------------------------------

def test_pick_still_never_walks_back_on_its_own():
    """The invariant the reach-back must not touch: `pick` READS the serving mark and never
    writes it, so a lapsed cooldown alone can never move a run back up the chain. The
    exception lives only in the branch `next_after` takes when it would otherwise return None.
    """
    chain = [_ref("a"), _ref("b"), _ref("c")]
    failover.mark_failed("ep", "id-a", cooldown_s=0.01)
    assert failover.next_after(chain, chain[0][1], **ANY_REQUEST) is chain[1]
    time.sleep(0.03)
    for _ in range(3):
        assert failover.pick(chain) is chain[1], "a lapsed cooldown moved the run backwards"
