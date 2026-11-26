"""D315: the uncensored turn-takeover converts a turn's media to vision-util TEXT *before* it
makes its request. Every routine here binds a non-multimodal uncensored model, so a refused turn
carrying an image used to post the image anyway, collect the provider's "does not support image
inputs" 400, and only then convert on the post-error path
(`mediafallback.apply_media_fallback`) — recovered, but one wasted provider round-trip per turn
(fired twice live on feeld-android:20261124-090018), with nothing in the transcript saying why
until after it failed. Selection deliberately stays `failover.pick`: routing the takeover through
`failover.fits`, which holds the media test, would reject every non-multimodal chain member and
leave a refused image turn with NO takeover at all.
"""

from __future__ import annotations

from helpers import run_context
from rsched.config import ModelRef
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine import mediaops, turntakeover
from rsched.engine.transcript import read_events

ANSWER_TEXT = '{"say": "served", "kind": "do_nothing"}'


class _Ep:
    """An endpoint that records the WHOLE message list of every call, so a test can ask what
    the provider was actually posted rather than what the loop meant to post.
    """

    context_tokens = 1_000_000
    multimodal = False

    def __init__(self):
        self.posted: list[list[dict]] = []

    def complete(self, messages, **kw):
        self.posted.append([dict(m) for m in messages])
        return Completion(text=ANSWER_TEXT)


class _Reg:
    """A registry whose uncensored role resolves to `ref` (non-multimodal by default, as every
    routine on this instance binds it).
    """

    def __init__(self, ep, ref=None):
        self.ep = ep
        self.ref = ref or ModelRef("nano gpt", "glm-unc", name="GLM uncensored")

    def for_uncensored(self, models, efforts):
        return self.ep, self.ref

    def for_model(self, kind, models, efforts):
        raise EndpointError("no model")

    def for_model_chain(self, kind, models, efforts):
        return [(self.ep, ModelRef("x", "y"))]


def _loop_with_an_image(make_routine, registry, tmp_path):
    """A loop mid-run whose prompt carries an image, with a takeover window open: exactly the
    state a refused image turn reaches the takeover seam in.
    """
    from rsched.engine.loop import EngineLoop
    from test_loop import TS, _server
    d = make_routine(slug="d315")
    loop = EngineLoop(run_context(d, TS, server=_server(d), registry=registry),
                      "## Run flow", "instr")
    loop.messages = [
        {"role": "user", "content": "OBS",
         "media": [{"path": str(tmp_path / "shot.png"), "media_type": "image/png"}]},
        {"role": "assistant", "content": "{}"},
        {"role": "user", "content": "now do the thing"},
    ]
    loop.ctx.takeover_left = 1          # a back-off window is open: the uncensored model serves
    return loop


def test_a_refused_image_turn_posts_no_images_to_the_uncensored_model(
        make_routine, tmp_path, monkeypatch):
    """The acceptance check's first half: no provider call carries images — the turn head
    resolves the takeover and the media is already text by the time anything is posted, so the
    description reaches the model and the attachment does not.
    """
    monkeypatch.setattr(mediaops, "vision_describe", lambda _ctx, _path, _prompt: "DESCRIBED")
    ep = _Ep()
    loop = _loop_with_an_image(make_routine, _Reg(ep), tmp_path)

    _chain, endpoint, ref, on_takeover = turntakeover.serve_turn_head(loop)
    assert on_takeover is True, "a window is open, so the uncensored model must serve the turn"
    assert ref.multimodal is False, "the fixture's uncensored model is the non-multimodal one"

    endpoint.complete(loop.messages, model=ref.model)
    posted = ep.posted[-1]
    assert all("media" not in m for m in posted), (
        "the uncensored model cannot see an image, so the request must carry none: "
        f"{[sorted(m) for m in posted]}")
    assert "DESCRIBED" in posted[0]["content"], (
        "the content must still reach it, as vision-util text")


def test_the_transcript_records_the_pre_request_conversion(
        make_routine, tmp_path, monkeypatch):
    """The acceptance check's second half: the transcript says WHY, before the request —
    recorded through the `error`/`where: "media"` event the console and the CLI renderer already
    render, not a new event type, which `transcript.EVENT_TYPES` and both readers would have to
    be extended for together.
    """
    monkeypatch.setattr(mediaops, "vision_describe", lambda _ctx, _path, _prompt: "DESCRIBED")
    loop = _loop_with_an_image(make_routine, _Reg(_Ep()), tmp_path)

    turntakeover.serve_turn_head(loop)

    events, _off = read_events(loop.ctx.run_dir / "transcript.jsonl")
    media = [e for e in events
             if e["type"] == "error" and e["payload"].get("where") == "media"]
    assert media, "the conversion left no trace a reader can find"
    payload = media[-1]["payload"]
    assert "GLM uncensored" in payload["message"], (
        f"the record must name the model that is reading a description: {payload['message']}")
    assert "BEFORE the request" in payload["message"], (
        f"the record must say the conversion preceded the request: {payload['message']}")
    assert any("DESCRIBED" in d for d in payload["descriptions"]), (
        "the descriptions the model then reasons from go on the record")


def test_a_multimodal_takeover_target_keeps_its_media(make_routine, tmp_path, monkeypatch):
    """The conversion is conditional on the SERVING model, not on the takeover: an uncensored
    model that can see an image is handed the image, because converting unconditionally would
    degrade the one case that needs no help.
    """
    monkeypatch.setattr(mediaops, "vision_describe", lambda _ctx, _path, _prompt: "DESCRIBED")
    seeing = ModelRef("nano gpt", "glm-vision", name="GLM vision", multimodal=True)
    loop = _loop_with_an_image(make_routine, _Reg(_Ep(), ref=seeing), tmp_path)

    _chain, _endpoint, ref, on_takeover = turntakeover.serve_turn_head(loop)
    assert on_takeover is True and ref.multimodal is True
    assert loop.messages[0].get("media"), "a model that can see the image must be handed it"
    events, _off = read_events(loop.ctx.run_dir / "transcript.jsonl")
    assert not [e for e in events
                if e["type"] == "error" and e["payload"].get("where") == "media"], (
        "nothing was converted, so nothing may be recorded as converted")
