"""Native multimodal (image/PDF) plumbing across the three endpoint adapters + the shared
base helpers. No network — each test asserts the exact provider block a `media`-carrying
message becomes, plus the supports_media capability matrix and the claude-cli probe gate."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from rsched.config import EndpointConfig
from rsched.endpoints import anthropic_api, claude_cli, openai_compat
from rsched.endpoints.base import (PDF_MIME, EndpointError, guess_media_type, read_media_b64,
                                   supports_media_type)


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


def test_native_multimodal_defaults_by_kind():
    assert EndpointConfig(kind="anthropic").native_multimodal() is True
    assert EndpointConfig(kind="claude-cli").native_multimodal() is True
    assert EndpointConfig(kind="openai").native_multimodal() is False
    assert EndpointConfig(kind="openai", multimodal=True).native_multimodal() is True
    assert EndpointConfig(kind="anthropic", multimodal=False).native_multimodal() is False


# --- anthropic ---------------------------------------------------------------

def test_anthropic_supports_media():
    ep = anthropic_api.AnthropicEndpoint(EndpointConfig(kind="anthropic", name="a"))
    assert ep.supports_media("image/png") and ep.supports_media("application/pdf")
    off = anthropic_api.AnthropicEndpoint(EndpointConfig(kind="anthropic", name="a", multimodal=False))
    assert not off.supports_media("image/png")


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
    ep = openai_compat.OpenAICompatEndpoint(EndpointConfig(kind="openai", name="o", multimodal=True))
    assert ep.supports_media("image/png")
    assert not ep.supports_media("application/pdf")   # PDFs route to the vision util
    assert not openai_compat.OpenAICompatEndpoint(
        EndpointConfig(kind="openai", name="o")).supports_media("image/png")


def test_openai_render_media_image(tmp_path):
    png = _file(tmp_path, "s.png", b"IMG")
    parts = openai_compat._render_media(
        [{"role": "user", "content": "look", "media": _media(png)}])[0]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1] == {"type": "image_url", "image_url": {
        "url": "data:image/png;base64," + base64.b64encode(b"IMG").decode()}}


# --- claude-cli --------------------------------------------------------------

def test_claude_cli_supports_media_and_probe():
    ep = claude_cli.ClaudeCliEndpoint(EndpointConfig(kind="claude-cli", name="c"))
    assert ep.supports_media("image/png")
    assert not ep.supports_media("application/pdf")   # stream-json is images-only
    ep._media_capable = False                         # probe learned the CLI can't take images
    assert not ep.supports_media("image/png")


def test_claude_cli_stream_json_stdin(tmp_path):
    png = _file(tmp_path, "s.png", b"IMG")
    obj = json.loads(claude_cli.stream_json_stdin(
        [{"role": "user", "content": "look", "media": _media(png)}]))
    assert obj["type"] == "user"
    blocks = obj["message"]["content"]
    assert blocks[0] == {"type": "text", "text": "look"}
    assert blocks[1] == {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": base64.b64encode(b"IMG").decode()}}


def test_claude_cli_build_cmd_stream_json_pairs_output_and_verbose():
    # the CLI rejects --input-format stream-json unless --output-format matches (+ --verbose)
    on = claude_cli.build_cmd("claude", "opus", system=None, schema_str=None, effort=None,
                              input_stream_json=True)
    assert "--input-format" in on and "--verbose" in on and on.count("stream-json") == 2
    assert on[on.index("--output-format") + 1] == "stream-json"
    off = claude_cli.build_cmd("claude", "opus", system=None, schema_str=None, effort=None)
    assert "--input-format" not in off and "--verbose" not in off
    assert off[off.index("--output-format") + 1] == "json"


def test_claude_cli_parse_stream_json_output():
    stream = "\n".join([
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "message": {"content": []}}),
        json.dumps({"type": "result", "subtype": "success", "is_error": False,
                    "result": '{"say": "hi", "kind": "finish"}',
                    "structured_output": {"say": "hi", "kind": "finish"},
                    "usage": {"input_tokens": 3, "output_tokens": 2}}),
    ])
    text, parsed, usage = claude_cli.parse_result(stream, want_json=True, stream_out=True)
    assert parsed == {"say": "hi", "kind": "finish"} and usage["in"] == 3 and usage["out"] == 2
    with pytest.raises(EndpointError):   # no result event in the stream → clean error
        claude_cli.parse_result(json.dumps({"type": "system"}), want_json=True, stream_out=True)


def test_claude_cli_encode_gates_on_probe(tmp_path):
    png = _file(tmp_path)
    ep = claude_cli.ClaudeCliEndpoint(EndpointConfig(kind="claude-cli", name="c"))
    media_msgs = [{"role": "user", "content": "look", "media": _media(png)}]
    stdin, stream = ep._encode(media_msgs, plain="IGNORED")     # untested probe → stream-json
    assert stream is True and json.loads(stdin)["type"] == "user"
    ep._media_capable = False                                    # known-broken → raise → loop falls back
    with pytest.raises(EndpointError):
        ep._encode(media_msgs, plain="IGNORED")
    assert ep._encode([{"role": "user", "content": "hi"}], plain="PLAIN") == ("PLAIN", False)


def test_claude_cli_msg_hashes_text_stable_media_distinct(tmp_path):
    legacy = hashlib.sha1("user\x00hi".encode()).hexdigest()
    assert claude_cli._msg_hashes([{"role": "user", "content": "hi"}]) == [legacy]
    assert claude_cli._msg_hashes(
        [{"role": "user", "content": "hi", "media": _media(_file(tmp_path))}]) != [legacy]
