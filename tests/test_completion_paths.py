"""Three edges of the completion cycle: re-fitting after a switch, who gets classified, and
what a turn leaves in the prompt.

1. The prompt is compacted ONCE per turn, under the window of the model that then failed. A
   chain step down to a smaller member used to post the same prompt unchanged and burn one of
   its two oversize retries proving it could not fit — llmsectest-weekday:20260922-060001
   handed a 156,656-token prompt to a 32,768-token fallback and died on the second attempt.
2. The refusal classifier is a serial `tool_call` round-trip on the retry path. It ran on every
   non-JSON *validation* failure as well as on prose: 784 `refusal · classify reply` calls in
   21 days against 8 refusals. Only PROSE can be a refusal.
3. The re-fit in (1) can COMPACT the list mid-turn, so the turn's retry debris cannot be found
   by a length taken at the top of the turn.
"""

from types import SimpleNamespace

from rsched.endpoints import failover
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine.completion import _adopt_model, _is_prose_reply, next_action
from test_loop_referral import _FakeEndpoint, _loop
from test_oversize_prompt import VALID, _ChainRegistry


def test_a_switched_to_model_gets_the_prompt_re_fit_to_its_window(monkeypatch):
    from rsched.engine import completion as completion_mod

    fitted = []
    monkeypatch.setattr(completion_mod, "compact_if_needed",
                        lambda _loop, endpoint, ref: fitted.append((endpoint, ref.model)))
    monkeypatch.setattr(completion_mod, "_override_window", lambda _loop, ref: ref)

    loop = SimpleNamespace(ctx=SimpleNamespace(main_model=""))
    small = SimpleNamespace(endpoint="openrouter", model="glm-5.2-free", context_tokens=32_768)

    endpoint, ref = _adopt_model(loop, ("ep2", small))

    assert (endpoint, ref) == ("ep2", small)
    assert fitted == [("ep2", "glm-5.2-free")], "the new model's window must re-fit the prompt"
    assert loop.ctx.main_model == "openrouter/glm-5.2-free"


def test_only_a_prose_reply_reaches_the_refusal_classifier():
    assert _is_prose_reply("I can't help with that. It would be unsafe.") is True
    assert _is_prose_reply("") is True
    # a JSON object that merely failed VALIDATION is a malformed action, not a decline
    assert _is_prose_reply('{"kind": "util", "say": "go"}') is False
    assert _is_prose_reply('Sure:\n```json\n{"kind": "finish"}\n```') is False


def test_a_compaction_mid_turn_still_drops_the_turns_retry_debris(make_routine, monkeypatch):
    """A schema retry, then a hard failure: the switch re-fits the prompt to the fallback's
    smaller window, and that compaction rebuilds the list. The turn's debris — the rejected
    reply, its correction, the failover notice — sat past the top-of-turn LENGTH the cleanup
    used to cut at, which the compacted list no longer reached, so all three rode along for
    the rest of the run and were re-read on every turn.
    """
    from rsched.engine import completion as completion_mod

    malformed = Completion(text='{"kind": "util", "say": "no name"}', parsed=None,
                           usage={"in": 1, "out": 1})
    head = _FakeEndpoint([malformed, EndpointError("head: HTTP 503 overloaded")])
    tail = _FakeEndpoint([VALID])
    loop = _loop(make_routine, _ChainRegistry(head, tail, name="debris-probe"))
    loop.messages += [{"role": "assistant" if i % 2 else "user", "content": f"turn {i}"}
                      for i in range(20)]
    kickoff = loop.messages[0]
    digest = {"role": "user", "content": "CONTEXT COMPACTED — the middle is archived"}

    def refit(lp, _endpoint, ref):
        # the fallback's window holds less: the middle goes, the newest three stay
        if ref.model == "tail-model":
            lp.messages = [lp.messages[0], digest, *lp.messages[-3:]]

    monkeypatch.setattr(completion_mod, "compact_if_needed", refit)
    try:
        action, _usage = next_action(loop)
    finally:
        failover.reset()
    assert action is not None
    assert action["kind"] == "read_file"
    assert (head.calls, tail.calls) == (2, 1)
    assert loop.messages == [kickoff, digest], (
        "the turn's retry debris outlived the compaction that moved it: "
        + repr([m["content"][:60] for m in loop.messages[2:]]))
