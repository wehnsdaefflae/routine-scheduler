"""Two edges of the completion cycle: re-fitting after a switch, and who gets classified.

1. The prompt is compacted ONCE per turn, under the window of the model that then failed. A
   chain step down to a smaller member used to post the same prompt unchanged and burn one of
   its two oversize retries proving it could not fit — llmsectest-weekday:20260922-060001
   handed a 156,656-token prompt to a 32,768-token fallback and died on the second attempt.
2. The refusal classifier is a serial `tool_call` round-trip on the retry path. It ran on every
   non-JSON *validation* failure as well as on prose: 784 `refusal · classify reply` calls in
   21 days against 8 refusals. Only PROSE can be a refusal.
"""

from types import SimpleNamespace

from rsched.engine.completion import _adopt_model, _is_prose_reply


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
