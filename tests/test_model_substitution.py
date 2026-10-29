"""F539 — a fallback that steps DOWN in context window is a SUBSTITUTION the run can see.

Two live pathologies are behind this:

* `llmsectest-weekday:20260922-060001` — bound to a 200k-class model, served OpenRouter's
  `z-ai/glm-5.2:free` (32,768 tokens), died at turn 106 having spent $1.03 on a run that was
  unrunnable from turn 0: "prompt is too long and cannot be shrunk further: 57,250 estimated
  tokens … provider maximum 32,768". Nothing was stale — the limits file was right, `window.py`
  sized against it, `overflow.py` reported it accurately. Every component did its job and the
  run still died, because the only part that could have stopped planning against a window it no
  longer had was the RUN, and the run was never told.
* `weightloss:20260923-220004` — stepped down two rungs (503 auth → 402 → GLM 5.2) and was never
  told, then read its own field-shifted output and concluded "the model cannot reliably hold the
  action schema; pick a stronger model". A false diagnosis about itself, $1.01 over 193 turns.

D146-B already tells the run WHICH model is serving it — as the turn's debris
(`engine/turndebris.py`), dropped the moment the turn's action is accepted. So a few turns later
the run holds exactly one statement about its own window: the `CAPABILITIES` line composed at
boot under the BOUND model's figure (`engine/capabilities.py`), which a downward substitution
silently falsifies and which cannot be rewritten (the prompt is immutable under the caching
contract). These tests pin the durable half — an ENGINE NOTE, once per downward step, that is
still in the prompt the turn after.

The module under test is imported INSIDE each unit test on purpose: a module-level import of a
symbol that does not exist yet turns the whole file into a collection error, and the
end-to-end test below is the one that has to fail BEHAVIOURALLY before the fix (red-first).
"""

from types import SimpleNamespace

from conftest import ScriptedEndpoint, finish, write_file
from rsched.config import EndpointConfig, ModelConfig, ModelRef, ServerConfig
from rsched.endpoints import EndpointRegistry, InstrumentedEndpoint
from rsched.endpoints.base import EndpointError
from rsched.engine.runtime import run_routine

TS = "20260717-081500"


def _loop(rungs=1, configured="epA/m-a"):
    """The three things the announcer touches: the ctx counters, the transcript, and the
    message list `enginenote`/`turndebris` append to. Deliberately NOT a RunContext — the
    announcer must stay narrow enough to work on exactly these."""
    events: list[dict] = []
    ctx = SimpleNamespace(
        failover_rungs=rungs, configured_model=configured,
        transcript=SimpleNamespace(event=lambda kind, payload: events.append(
            {"type": kind, **payload})))
    return SimpleNamespace(ctx=ctx, messages=[], _turn_debris=[]), events


def _member(endpoint, model, window):
    return object(), ModelRef(endpoint=endpoint, model=model, name=model,
                              context_tokens=window)


def _notes(loop):
    return [m for m in loop.messages
            if str(m.get("content") or "").startswith("ENGINE NOTE:")]


# ---- the announcer -------------------------------------------------------------------------

def test_a_downward_step_is_announced_with_both_windows():
    from rsched.engine import substitution

    loop, events = _loop()
    chain = [_member("epA", "m-a", 200_000), _member("epB", "m-b", 32_768)]
    substitution.announce(loop, chain, chain[0][1], chain[1][1],
                          EndpointError("epA is down: HTTP 503 auth_unavailable"))

    notes = _notes(loop)
    assert len(notes) == 1, (
        "a substitution that steps DOWN in context window never reached the running model: "
        "every record of it went to readers after the fact (the transcript event, the fleet "
        "health event, the usage row)")
    said = str(notes[0]["content"])
    assert "32,768" in said and "200,000" in said, (
        "the note must carry BOTH windows as numbers — a run cannot correct a figure it is not "
        "given, and the figure it holds (the boot-time CAPABILITIES line) is the wrong one")
    assert "epB/m-b" in said and "epA/m-a" in said, (
        "the note must name the model now serving AND the bound model it replaced, in the "
        "<endpoint>/<model> form the engine uses everywhere else")
    assert "CAPABILITIES" in said, (
        "the note has to name the prompt section it corrects; otherwise the run holds two "
        "contradictory window figures with nothing to say which one is current")

    # DURABLE, not the turn's debris — that is the half D146-B's notice could not be
    assert notes[0] not in loop._turn_debris, (
        "appended as debris, the note is dropped the moment this turn's action is accepted — "
        "and the run is back to planning against a window it does not have")
    recorded = [e for e in events if e["type"] == "user_injection"]
    assert recorded and recorded[0]["source"] == "engine", (
        "it must go through the one engine-note seam, so a resumed leg replays it as an ENGINE "
        "NOTE and not as a phantom user message (engine/enginenote.py)")


def test_a_sideways_or_upward_step_says_nothing_about_the_window():
    """The note is the correction of a FALSE figure. A rung with the same window (or a larger
    one) falsifies nothing, and a note per switch regardless would be the layer talking about
    itself — the reason `_warn_before_eviction` is once-per-run and this is per DOWNWARD step.
    """
    from rsched.engine import substitution

    loop, _events = _loop()
    chain = [_member("epA", "m-a", 200_000), _member("epB", "m-b", 200_000),
             _member("epC", "m-c", 400_000)]
    substitution.announce(loop, chain, chain[0][1], chain[1][1], EndpointError("503"))
    substitution.announce(loop, chain, chain[1][1], chain[2][1], EndpointError("402"))

    assert not _notes(loop), "neither rung shrank the window; there is nothing to correct"
    # …while D146-B's notice is still appended for EVERY switch, as the turn's debris
    assert sum(1 for m in loop.messages
               if "MODEL FAILOVER" in str(m.get("content") or "")) == 2


def test_the_step_is_measured_against_the_bound_model_not_the_previous_rung():
    """`weightloss:20260923-220004` stepped down TWICE. Measuring each step against the rung
    that just failed hides a descent that is only visible against the model the operator bound
    and the CAPABILITIES line was composed from: 200k → 200k → 32k has no shrinking STEP, and a
    run served from the third rung is nevertheless 168k short of what it was told."""
    from rsched.engine import substitution

    loop, _events = _loop(rungs=2)
    chain = [_member("epA", "m-a", 200_000), _member("epB", "m-b", 200_000),
             _member("epC", "m-c", 32_768)]
    substitution.announce(loop, chain, chain[1][1], chain[2][1], EndpointError("402"))

    notes = _notes(loop)
    assert len(notes) == 1 and "200,000" in str(notes[0]["content"]), (
        "the comparison must be against the BOUND model's window — the figure the run was "
        "actually given — not against the rung that happened to fail last")


def test_the_correction_is_given_once_per_window_and_survives_a_resume():
    """A run holds ONE wrong figure, so it needs ONE correction per window it has been
    corrected down to — a note per switch regardless would be the layer talking about itself
    (`window._warn_before_eviction` is once-per-run for the same reason).

    And the latch has to be rebuildable, because a resumed leg REPLAYS the note it was given
    (that is what `enginenote` records it verbatim for) while starting its in-memory guards
    empty: without the rebuild the run is handed a second copy the moment the chain steps
    again. `engine/guardscope.py` is where that contract lives, so the floor rides the note's
    own record as `substitution_floor` and is read back from it.
    """
    from rsched.engine import substitution

    loop, events = _loop()
    chain = [_member("epA", "m-a", 200_000), _member("epB", "m-b", 100_000),
             _member("epC", "m-c", 100_000)]
    substitution.announce(loop, chain, chain[0][1], chain[1][1], EndpointError("503"))
    substitution.announce(loop, chain, chain[1][1], chain[2][1], EndpointError("402"))
    assert len(_notes(loop)) == 1, "the second rung costs the run no window it was not told"

    recorded = [e for e in events if e["type"] == "user_injection"]
    assert recorded[0]["substitution_floor"] == 100_000, (
        "the floor must ride the note's RECORD — a resumed leg rebuilds its guards from the "
        "transcript and never from a side file the transcript could contradict")

    # a resumed leg: the guards start empty, then are seeded from the events in scope
    resumed, _ = _loop()
    substitution.rebuild(resumed, [{"type": "user_injection", "payload": p}
                                   for p in [{"text": "…", "source": "engine",
                                              "substitution_floor": 100_000}]])
    substitution.announce(resumed, chain, chain[1][1], chain[2][1], EndpointError("402"))
    assert not _notes(resumed), (
        "the resumed leg repeated a correction its replayed prompt already carries")
    # …and a further step DOWN is still announced, because that one is new information
    smaller = (*chain, _member("epD", "m-d", 32_768))
    substitution.announce(resumed, list(smaller), smaller[2][1], smaller[3][1],
                          EndpointError("503"))
    assert len(_notes(resumed)) == 1 and "32,768" in str(_notes(resumed)[0]["content"])


# ---- end to end: it is still there the turn after ------------------------------------------

def _server(routines_home) -> ServerConfig:
    """A two-rung chain whose rungs differ in WINDOW and in nothing else.

    100k rather than the live specimen's 32,768: a 32k window minus the default 16,384-token
    output reservation cannot hold this harness at all, so the run would die of the overflow
    this note exists to explain instead of demonstrating the substitution."""
    s = ServerConfig(
        endpoints={"epA": EndpointConfig(kind="openai", base_url="http://127.0.0.1:1/v1"),
                   "epB": EndpointConfig(kind="openai", base_url="http://127.0.0.1:2/v1")},
        models={"prime": ModelConfig(endpoint="epA", model="m-a", context_tokens=200_000,
                                     fallbacks=["backup"]),
                "backup": ModelConfig(endpoint="epB", model="m-b", context_tokens=100_000)},
        system_model="prime")
    for name, ep in s.endpoints.items():
        ep.name = name
    for name, mc in s.models.items():
        mc.name = name
    s.routines_home = routines_home
    s.libraries_home = routines_home.parent / "test-library"
    return s


def _wire(monkeypatch, server, replies_by_endpoint) -> dict[str, ScriptedEndpoint]:
    """run_routine over the REAL registry chain/pick logic, each configured endpoint served by
    its own ScriptedEndpoint (the transport is the only fake)."""
    import rsched.engine.loop as loop_mod
    import rsched.engine.runtime as runtime_mod

    monkeypatch.setattr(loop_mod, "POLL_S", 0.02)
    eps = {}
    for name, replies in replies_by_endpoint.items():
        eps[name] = ScriptedEndpoint(replies)
        eps[name].name = name       # cooldown marks key on the CONFIG endpoint name

    class ChainRegistry(EndpointRegistry):
        def get(self, name):
            return InstrumentedEndpoint(eps[name])

    monkeypatch.setattr(runtime_mod, "EndpointRegistry", lambda s: ChainRegistry(server))
    return eps


def test_the_smaller_window_is_still_in_the_prompt_the_turn_after(make_routine, monkeypatch):
    """The whole finding in one run: the primary dies, a HALF-SIZED fallback serves the rest of
    the run, and the question is what the run can still see about that on its SECOND turn.

    Before this change the answer was nothing. The failover notice is the turn's debris and is
    dropped on success; the transcript event and the `model_failover` health event are for
    readers after the fact; the usage row names the serving model to an auditor, not to the
    model. What the run was left holding is its boot-time CAPABILITIES line — 200,000 tokens —
    while it is being served 100,000.
    """
    d = make_routine("substitution-window")
    server = _server(d.parent)
    eps = _wire(monkeypatch, server, {
        "epA": [EndpointError("epA is down: HTTP 503 auth_unavailable")],
        "epB": [write_file("state/probe.txt", say="grounding work"),
                finish(summary="served by the backup model")]})
    status, _run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    assert len(eps["epB"].calls) == 2, "the backup served the inherited turn and the next one"

    later = eps["epB"].calls[1]["messages"]
    assert not [m for m in later if "MODEL FAILOVER" in str(m.get("content") or "")], (
        "D146-B's notice is the turn's debris and is dropped once the turn's action is "
        "accepted — which is what makes a durable note necessary rather than redundant")

    window_notes = [str(m.get("content") or "") for m in later
                    if str(m.get("content") or "").startswith("ENGINE NOTE:")
                    and "CONTEXT WINDOW" in str(m.get("content") or "")]
    assert len(window_notes) == 1, (
        "nothing in the prompt tells this run its window is now 100,000 rather than the "
        "200,000 its CAPABILITIES section states — the substitution is visible only in the "
        "health stream and the usage row (llmsectest-weekday:20260922-060001)")
    said = window_notes[0]
    assert "100,000" in said and "200,000" in said
    assert "epB/m-b" in said and "epA/m-a" in said
