"""An oversized `llm` reply SPILLS instead of losing its middle — D246's first increment.

`docs/designs.md`, under the model-facing wording that drifted: *"An `llm` reply over the
observation cap loses its middle: spill it like util output (`engine/outputs.py`) and name the
path."* The entry marks it **the only item that loses information**, which is why it is the
increment.

`llmaction.do_llm` ended with `reply, truncated = truncate(reply)` — the head-and-tail form,
which elides the MIDDLE and keeps nothing. For a util, script or shell command the elided part is
written to `.util_outputs/` and the observation carries a `[full output]` pointer to it
(`engine/outputs.spill` + `pointer_line`, one store for every callable kind). The one kind whose
output is a MODEL's answer — the most expensive output a run buys, and the least reproducible,
since a re-ask returns a different reply — was the kind that threw the middle away.

Now it spills through the same store and the same pointer, so a run that asked for 60 kB of
analysis can read all of it.
"""

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
    """Over the cap, with a middle that the OBSERVATION genuinely cannot carry.

    `truncate`'s head+tail form keeps `0.6 * cap` from the front and the rest from the back, so
    a marker placed at the exact centre of a reply only slightly over the cap still lands inside
    the kept tail. The elided span has to be wider than what is dropped: here the reply is three
    caps long, so the middle third is unreachable from the observation and readable only from the
    spill file — which is the whole claim.
    """
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
