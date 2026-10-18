"""Native multimodal plumbing — image, PDF, audio and video (D192) — across the endpoint
adapters and the shared allowlist in `endpoints/media_types.py`. No network: each test asserts
the exact provider block a `media`-carrying message becomes, plus the supports_media capability
matrix for the HTTP adapters."""

from __future__ import annotations

import base64

from rsched.config import EndpointConfig
from rsched.endpoints import anthropic_api, anthropic_messages, openai_compat, openai_media
from rsched.endpoints.media_types import (
    MEDIA_MODALITIES,
    MODALITIES,
    NATIVE_MEDIA_LIMIT_NOTE,
    NATIVE_MEDIA_MAX_BYTES,
    guess_media_type,
    modality_of,
    model_modalities,
    oversize_reason,
    read_media_b64,
    supports_media_type,
)

PDF_MIME = "application/pdf"
WAV_MIME = "audio/x-wav"
MP4_MIME = "video/mp4"


def _file(tmp_path, name="shot.png", data=b"PNGDATA"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def _media(path, mime="image/png"):
    return [{"path": str(path), "media_type": mime}]


# --- base helpers ------------------------------------------------------------

def test_guess_media_type():
    assert guess_media_type("a.png") == "image/png"
    assert guess_media_type("A.JPG") == "image/jpeg"
    assert guess_media_type("a.pdf") == PDF_MIME
    assert guess_media_type("a.txt") is None
    assert guess_media_type("a.tiff") is None   # not in the supported set


def test_read_media_b64(tmp_path):
    assert base64.b64decode(read_media_b64(_file(tmp_path, data=b"hello"))) == b"hello"


def test_supports_media_type_matrix():
    every = {"pdf": True, "audio": True, "video": True}
    assert supports_media_type("image/png", multimodal=True, **every)
    assert supports_media_type("application/pdf", multimodal=True, **every)
    assert not supports_media_type("application/pdf", multimodal=True,
                                   pdf=False, audio=True, video=True)
    assert not supports_media_type("image/png", multimodal=False, **every)
    assert not supports_media_type("text/plain", multimodal=True, **every)


# --- D192: audio and video reach a model natively ----------------------------

def test_the_allowlist_is_one_structure_tagged_by_modality():
    """The old shape was IMAGE_MIMES + a lone PDF_MIME, so a third modality meant a third
    constant and finding every reading of the first two. One mapping, one vocabulary."""
    assert set(MEDIA_MODALITIES.values()) == set(MODALITIES)
    assert modality_of("image/png") == "image"
    assert modality_of(PDF_MIME) == "pdf"
    assert modality_of(WAV_MIME) == "audio"
    assert modality_of(MP4_MIME) == "video"
    assert modality_of("text/plain") is None
    assert modality_of(None) is None


def test_guess_media_type_admits_audio_and_video():
    """The bug D192 fixes: guess_media_type returned None for these, so the file was refused
    before any adapter or fallback was asked — whatever the model could actually take."""
    assert modality_of(guess_media_type("clip.wav")) == "audio"
    assert modality_of(guess_media_type("clip.mp3")) == "audio"
    assert modality_of(guess_media_type("clip.mp4")) == "video"
    assert modality_of(guess_media_type("notes.txt")) is None


def test_supports_media_type_gates_audio_and_video_exactly_like_pdf():
    on = {"multimodal": True, "pdf": True, "audio": True, "video": True}
    assert supports_media_type(WAV_MIME, **on)
    assert supports_media_type(MP4_MIME, **on)
    assert not supports_media_type(WAV_MIME, **{**on, "audio": False})
    assert not supports_media_type(MP4_MIME, **{**on, "video": False})
    # multimodal is still the master gate: a text-only model takes nothing
    assert not supports_media_type(WAV_MIME, **{**on, "multimodal": False})
    # and one modality's bit never answers for another
    assert not supports_media_type(MP4_MIME, multimodal=True, pdf=True, audio=True, video=False)


def test_model_modalities_reads_the_resolved_refs_bits():
    from rsched.config import ModelRef
    ref = ModelRef(endpoint="e", model="m", multimodal=True, audio=True)
    assert model_modalities(ref) == {"multimodal": True, "audio": True, "video": False}


def test_an_oversized_audio_file_is_told_the_byte_bound_at_the_refusal(tmp_path, monkeypatch):
    """D192 item 5: a video meets the 7 MiB ceiling within seconds of footage, so the refusal
    a run READS has to carry the number and the way around it — not only the docs."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF" + b"x" * 64)
    assert oversize_reason(clip, WAV_MIME) is None
    monkeypatch.setattr("rsched.endpoints.media_types.NATIVE_MEDIA_MAX_BYTES", 16)
    reason = oversize_reason(clip, WAV_MIME) or ""
    assert "ceiling" in reason and "MiB" in reason
    assert "base64" in reason and "33%" in reason, "the inflation is part of the bound"
    assert "video-extract" in reason or "deepgram" in reason, "say what to do instead"


def test_the_limit_note_states_the_ceiling_and_the_inflation():
    assert f"{NATIVE_MEDIA_MAX_BYTES // 1024 // 1024} MiB" in NATIVE_MEDIA_LIMIT_NOTE
    assert "33%" in NATIVE_MEDIA_LIMIT_NOTE


def test_catalog_multimodal_defaults_by_kind():
    """multimodal lives on the catalog MODEL now: unset → the endpoint kind default
    (anthropic on, openai off); an explicit per-model value overrides."""
    from rsched.config import ModelConfig, ServerConfig
    from rsched.endpoints import EndpointRegistry
    server = ServerConfig()
    server.endpoints = {
        "anth": EndpointConfig(name="anth", kind="anthropic"),
        "or": EndpointConfig(name="or", kind="openai", base_url="http://x"),
    }
    server.models = {
        "a": ModelConfig(name="a", endpoint="anth", model="claude"),            # → default True
        "o": ModelConfig(name="o", endpoint="or", model="glm"),                 # → default False
        "ov": ModelConfig(name="ov", endpoint="or", model="gpt-4o", multimodal=True),    # explicit on
        "ax": ModelConfig(name="ax", endpoint="anth", model="claude", multimodal=False),  # explicit off
    }
    reg = EndpointRegistry(server)
    assert reg.resolve("a")[1].multimodal is True
    assert reg.resolve("o")[1].multimodal is False
    assert reg.resolve("ov")[1].multimodal is True
    assert reg.resolve("ax")[1].multimodal is False


# --- anthropic ---------------------------------------------------------------

def test_anthropic_supports_media():
    # the resolved model's multimodal flag is passed in; anthropic takes images AND PDFs
    ep = anthropic_api.AnthropicEndpoint(EndpointConfig(kind="anthropic", name="a"))
    off = {"audio": False, "video": False}
    assert ep.supports_media("image/png", multimodal=True, **off)
    assert ep.supports_media("application/pdf", multimodal=True, **off)
    assert not ep.supports_media("image/png", multimodal=False, **off)


def test_anthropic_reports_false_for_audio_and_video_even_when_the_model_says_yes():
    """D192: the Messages API publishes no block for either, so the WIRE answers false however
    the catalog model is configured — the same split `pdf` has, with the endpoints swapped."""
    ep = anthropic_api.AnthropicEndpoint(EndpointConfig(kind="anthropic", name="a"))
    assert not ep.supports_media(WAV_MIME, multimodal=True, audio=True, video=True)
    assert not ep.supports_media(MP4_MIME, multimodal=True, audio=True, video=True)


def test_anthropic_render_media_image_and_pdf(tmp_path):
    png, pdf = _file(tmp_path, "s.png", b"IMG"), _file(tmp_path, "d.pdf", b"%PDF")
    msgs = [{"role": "user", "content": "look",
             "media": [{"path": str(png), "media_type": "image/png"},
                       {"path": str(pdf), "media_type": PDF_MIME}]}]
    blocks = anthropic_messages.render_media(msgs)[0]["content"]
    assert blocks[0] == {"type": "text", "text": "look"}
    assert blocks[1] == {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": base64.b64encode(b"IMG").decode()}}
    assert blocks[2]["type"] == "document" and blocks[2]["source"]["media_type"] == PDF_MIME
    # a text-only message keeps plain string content (cache-stable)
    assert anthropic_messages.render_media([{"role": "user", "content": "hi"}]) == \
        [{"role": "user", "content": "hi"}]


def test_anthropic_merge_consecutive_carries_media(tmp_path):
    png = _file(tmp_path)
    merged = anthropic_messages.merge_consecutive(
        [{"role": "user", "content": "a"},
         {"role": "user", "content": "b", "media": _media(png)}])
    assert len(merged) == 1 and merged[0]["content"] == "a\n\nb"
    assert merged[0]["media"] == _media(png)


def test_anthropic_mark_tail_handles_both_shapes():
    listy = anthropic_messages.mark_tail([{"role": "user", "content": [
        {"type": "text", "text": "hi"}, {"type": "image", "source": {"x": 1}}]}])
    assert listy[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    plain = anthropic_messages.mark_tail([{"role": "user", "content": "hi"}])
    assert plain[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}


# --- openai ------------------------------------------------------------------

def test_openai_supports_media():
    ep = openai_compat.OpenAICompatEndpoint(EndpointConfig(kind="openai", name="o"))
    off = {"audio": False, "video": False}
    assert ep.supports_media("image/png", multimodal=True, **off)
    assert not ep.supports_media("application/pdf", multimodal=True, **off)  # → the vision util
    assert not ep.supports_media("image/png", multimodal=False, **off)       # a text-only model


def test_openai_carries_audio_on_the_model_bit_and_never_video():
    """D192: this wire HAS an `input_audio` part, so audio rides it when the resolved MODEL
    carries the bit (one endpoint serves a model with ears and one without). It has no video
    part its servers agree on, so video is false whatever the catalog says."""
    ep = openai_compat.OpenAICompatEndpoint(EndpointConfig(kind="openai", name="o"))
    assert ep.supports_media(WAV_MIME, multimodal=True, audio=True, video=True)
    assert not ep.supports_media(WAV_MIME, multimodal=True, audio=False, video=True)
    assert not ep.supports_media(MP4_MIME, multimodal=True, audio=True, video=True)


def test_openai_render_media_image(tmp_path):
    png = _file(tmp_path, "s.png", b"IMG")
    parts = openai_media.render_media(
        [{"role": "user", "content": "look", "media": _media(png)}])[0]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1] == {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(b"IMG").decode()}}


def test_openai_renders_audio_as_an_input_audio_part(tmp_path):
    """OpenAI's audio shape: `input_audio` carrying RAW base64 `data` plus a bare `format`
    word — the one part on this wire that is not a data URI."""
    wav = _file(tmp_path, "clip.wav", b"RIFFDATA")
    parts = openai_media.render_media(
        [{"role": "user", "content": "hear", "media": _media(wav, WAV_MIME)}])[0]["content"]
    assert parts[0] == {"type": "text", "text": "hear"}
    assert parts[1] == {"type": "input_audio", "input_audio": {
        "data": base64.b64encode(b"RIFFDATA").decode(), "format": "wav"}}
    # the format word is the API's, not the mime subtype, where the two differ
    assert openai_media.audio_format("audio/mpeg") == "mp3"
    assert openai_media.audio_format("audio/flac") == "flac"


def test_openai_renders_video_as_a_data_uri_part(tmp_path):
    """A shape exists for completeness; `supports_media` is what decides a file ever gets
    here, and on this wire it never does for video."""
    mp4 = _file(tmp_path, "clip.mp4", b"MP4")
    parts = openai_media.render_media(
        [{"role": "user", "content": "", "media": _media(mp4, MP4_MIME)}])[0]["content"]
    assert parts[0] == {"type": "video_url", "video_url": {
        "url": "data:video/mp4;base64," + base64.b64encode(b"MP4").decode()}}


def test_view_image_hands_audio_to_a_model_configured_for_it(tmp_path):
    """End to end through the engine (D192): the file reaches the message as a media entry
    instead of being refused by `guess_media_type` before anything was asked."""
    from types import SimpleNamespace

    from rsched.engine import mediaops

    wav = _file(tmp_path, "clip.wav", b"RIFFDATA")
    ep = openai_compat.OpenAICompatEndpoint(EndpointConfig(kind="openai", name="o"))
    ref = SimpleNamespace(multimodal=True, audio=True, video=False)
    ctx = SimpleNamespace(
        routine=SimpleNamespace(dir=tmp_path, fs_read_roots=[], fs_write_roots=[],
                                models={}, efforts={}),
        grants=None, depth=0, root_run_dir=tmp_path / "runs" / "x",
        read_roots=list, write_roots=list,
        server=SimpleNamespace(libraries_home=tmp_path / "u", routines_home=tmp_path / "r"),
        registry=SimpleNamespace(for_model=lambda _k, _m, _e: (ep, ref)), seen_paths=set())
    obs = mediaops.do_view_image({"kind": "view_image", "path": wav.name}, ctx)
    assert obs["files"][0].get("native") is True, obs["files"][0]
    assert obs["media"][0]["media_type"] == WAV_MIME
    assert obs["media"][0]["b64"] == base64.b64encode(b"RIFFDATA").decode()


def test_a_model_without_the_bit_is_told_why_and_told_the_bound(tmp_path):
    """There is no vision-util fallback for a sound file, so the refusal has to be the useful
    thing: what to configure, what to use instead, and the native size bound (D192 item 5)."""
    from types import SimpleNamespace

    from rsched.engine import mediaops

    wav = _file(tmp_path, "clip.wav", b"RIFFDATA")
    ep = openai_compat.OpenAICompatEndpoint(EndpointConfig(kind="openai", name="o"))
    ref = SimpleNamespace(multimodal=True, audio=False, video=False)
    ctx = SimpleNamespace(
        routine=SimpleNamespace(dir=tmp_path, fs_read_roots=[], fs_write_roots=[],
                                models={}, efforts={}),
        grants=None, depth=0, root_run_dir=tmp_path / "runs" / "x",
        read_roots=list, write_roots=list,
        server=SimpleNamespace(libraries_home=tmp_path / "u", routines_home=tmp_path / "r"),
        registry=SimpleNamespace(for_model=lambda _k, _m, _e: (ep, ref)), seen_paths=set())
    obs = mediaops.do_view_image({"kind": "view_image", "path": wav.name}, ctx)
    err = obs["files"][0]["error"]
    assert "not configured for audio" in err, err
    assert "MiB" in err and "33%" in err, "the refusal states the native byte bound"
    assert "media" not in obs


def test_catalog_audio_and_video_bits_reach_the_resolved_model():
    """The bits are per-MODEL and inherit no endpoint default (D192) — an operator turns one
    on for the one model that has ears, and the ModelRef the engine reads carries it."""
    from rsched.config import ModelConfig, ServerConfig
    from rsched.endpoints import EndpointRegistry
    server = ServerConfig()
    server.endpoints = {"or": EndpointConfig(name="or", kind="openai", base_url="http://x")}
    server.models = {
        "plain": ModelConfig(name="plain", endpoint="or", model="glm"),
        "ears": ModelConfig(name="ears", endpoint="or", model="gpt-audio",
                            multimodal=True, audio=True),
        "eyes": ModelConfig(name="eyes", endpoint="or", model="gemini",
                            multimodal=True, video=True),
    }
    reg = EndpointRegistry(server)
    assert (reg.resolve("plain")[1].audio, reg.resolve("plain")[1].video) == (False, False)
    assert reg.resolve("ears")[1].audio is True and reg.resolve("ears")[1].video is False
    assert reg.resolve("eyes")[1].video is True
