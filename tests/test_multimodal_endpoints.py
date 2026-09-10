"""Native multimodal (image/PDF) plumbing across the three endpoint adapters + the shared
base helpers. No network — each test asserts the exact provider block a `media`-carrying
message becomes, plus the supports_media capability matrix for the HTTP adapters."""

from __future__ import annotations

import base64

from rsched.config import EndpointConfig
from rsched.endpoints import anthropic_api, openai_compat
from rsched.endpoints.base import (
    PDF_MIME,
    guess_media_type,
    read_media_b64,
    supports_media_type,
)


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
    assert supports_media_type("image/png", multimodal=True, pdf=True)
    assert supports_media_type("application/pdf", multimodal=True, pdf=True)
    assert not supports_media_type("application/pdf", multimodal=True, pdf=False)
    assert not supports_media_type("image/png", multimodal=False, pdf=True)
    assert not supports_media_type("text/plain", multimodal=True, pdf=True)


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
    assert ep.supports_media("image/png", multimodal=True)
    assert ep.supports_media("application/pdf", multimodal=True)
    assert not ep.supports_media("image/png", multimodal=False)


def test_anthropic_render_media_image_and_pdf(tmp_path):
    png, pdf = _file(tmp_path, "s.png", b"IMG"), _file(tmp_path, "d.pdf", b"%PDF")
    msgs = [{"role": "user", "content": "look",
             "media": [{"path": str(png), "media_type": "image/png"},
                       {"path": str(pdf), "media_type": PDF_MIME}]}]
    blocks = anthropic_api._render_media(msgs)[0]["content"]
    assert blocks[0] == {"type": "text", "text": "look"}
    assert blocks[1] == {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": base64.b64encode(b"IMG").decode()}}
    assert blocks[2]["type"] == "document" and blocks[2]["source"]["media_type"] == PDF_MIME
    # a text-only message keeps plain string content (cache-stable)
    assert anthropic_api._render_media([{"role": "user", "content": "hi"}]) == \
        [{"role": "user", "content": "hi"}]


def test_anthropic_merge_consecutive_carries_media(tmp_path):
    png = _file(tmp_path)
    merged = anthropic_api.merge_consecutive(
        [{"role": "user", "content": "a"},
         {"role": "user", "content": "b", "media": _media(png)}])
    assert len(merged) == 1 and merged[0]["content"] == "a\n\nb"
    assert merged[0]["media"] == _media(png)


def test_anthropic_mark_tail_handles_both_shapes():
    listy = anthropic_api._mark_tail([{"role": "user", "content": [
        {"type": "text", "text": "hi"}, {"type": "image", "source": {"x": 1}}]}])
    assert listy[-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    plain = anthropic_api._mark_tail([{"role": "user", "content": "hi"}])
    assert plain[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}


# --- openai ------------------------------------------------------------------

def test_openai_supports_media():
    ep = openai_compat.OpenAICompatEndpoint(EndpointConfig(kind="openai", name="o"))
    assert ep.supports_media("image/png", multimodal=True)
    assert not ep.supports_media("application/pdf", multimodal=True)   # PDFs route to the vision util
    assert not ep.supports_media("image/png", multimodal=False)       # a text-only model


def test_openai_render_media_image(tmp_path):
    png = _file(tmp_path, "s.png", b"IMG")
    parts = openai_compat._render_media(
        [{"role": "user", "content": "look", "media": _media(png)}])[0]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1] == {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(b"IMG").decode()}}
