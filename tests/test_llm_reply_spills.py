"""An oversized `llm` reply SPILLS instead of losing its middle — D246's first increment."""

from __future__ import annotations

from helpers import action_ctx
from rsched.engine import outputs
from rsched.engine.llmaction import do_llm
from rsched.engine.observations import OBS_CAP_CHARS, format_observation


class _Completion:
    def __init__(self, text: str):
        self.text = text
        self.parsed = None
        self.usage = {"in": 1, "out": 1}


class _Endpoint:
    """The one call `do_llm` makes, answering with a reply of the size the test chose."""

    def __init__(self, text: str):
        self.text = text

    def complete(self, messages, **kwargs):
        return _Completion(self.text)


class _Ref:
    endpoint = "scripted"
    model = "test-model"
    name = "test-model"
    effort = None
    temperature = None
    max_tokens = 4096


class _Registry:
    def __init__(self, endpoint):
        self._endpoint = endpoint

    def for_model(self, role, models, efforts):
        return self._endpoint, _Ref()


def _ctx(make_routine, tmp_path, reply: str):
    ctx = action_ctx(make_routine(), tmp_path)
    ctx.registry = _Registry(_Endpoint(reply))
    return ctx


def _long_reply() -> str:
    """Over the cap, with a middle that the OBSERVATION genuinely cannot carry."""
    head = "H" * OBS_CAP_CHARS
    middle = "the-middle-nobody-could-read " * (OBS_CAP_CHARS // 29)
    tail = "T" * OBS_CAP_CHARS
    return head + middle + tail


def test_an_oversized_llm_reply_is_written_in_full_to_the_spill_store(make_routine, tmp_path):
    """The defect: the middle was elided and nothing kept it."""
    reply = _long_reply()
    ctx = _ctx(make_routine, tmp_path, reply)

    obs = do_llm({"kind": "llm", "prompt": "analyse this", "say": "a long answer"}, ctx)

    assert obs.get("truncated") is True, "the fixture reply did not exceed the cap"
    pointer = obs.get("full_output")
    assert pointer, "an oversized llm reply left no pointer — its middle is gone for good"
    spilled = (ctx.routine.dir / pointer["stdout"]).read_text(encoding="utf-8")
    assert spilled == reply
    assert "the-middle-nobody-could-read" in spilled
    assert pointer["stdout_chars"] == len(reply)


def test_the_observation_names_the_path_so_the_run_can_read_it(make_routine, tmp_path):
    """A spill nobody is told about is the same loss with extra steps. The pointer line is the
    one the callable kinds already use, so there is one sentence to learn, not two.
    """
    ctx = _ctx(make_routine, tmp_path, _long_reply())

    obs = do_llm({"kind": "llm", "prompt": "analyse this"}, ctx)
    rendered = format_observation(obs)

    assert "[full output]" in rendered
    assert obs["full_output"]["stdout"] in rendered
    assert "the-middle-nobody-could-read" not in rendered     # still capped, by design


def test_a_reply_under_the_cap_spills_nothing_and_says_nothing(make_routine, tmp_path):
    """The common case must be untouched: no file, no pointer, no extra line in the prompt."""
    ctx = _ctx(make_routine, tmp_path, "a short answer")

    obs = do_llm({"kind": "llm", "prompt": "briefly"}, ctx)

    assert obs["reply"] == "a short answer"
    assert not obs.get("truncated")
    assert obs.get("full_output") is None
    assert "[full output]" not in format_observation(obs)
    assert not (ctx.routine.dir / outputs.OUTPUTS_DIR).exists()
