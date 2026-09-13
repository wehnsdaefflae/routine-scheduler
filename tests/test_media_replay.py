"""Previously attached files can disappear before a later provider request."""
import pytest

from rsched.endpoints.anthropic_api import _content_blocks
from rsched.endpoints.openai_compat import _openai_content


@pytest.mark.parametrize("render", [_content_blocks, _openai_content])
def test_deleted_historical_attachment_is_explicit_and_other_media_survives(tmp_path, render):
    """Deleting a viewed scratch file must not crash the next model send."""
    old = tmp_path / "old.png"
    kept = tmp_path / "kept.png"
    old.write_bytes(b"old-image")
    kept.write_bytes(b"kept-image")
    media = [{"path": str(p), "media_type": "image/png"} for p in (old, kept)]
    assert len(render("earlier observation", media)) == 3
    old.unlink()
    blocks = render("earlier observation", media)
    assert len(blocks) == 3
    assert blocks[0] == {"type": "text", "text": "earlier observation"}
    assert blocks[1]["type"] == "text"
    assert "unavailable" in blocks[1]["text"].lower()
    assert str(old) in blocks[1]["text"]
    assert blocks[2]["type"] in {"image", "image_url"}


def test_native_ollama_keeps_available_images_when_history_file_disappears(tmp_path, monkeypatch):
    """Native provider requests retain valid media and explicitly name missing history."""
    import base64

    import httpx

    from rsched.config import EndpointConfig
    from rsched.endpoints.openai_compat import OpenAICompatEndpoint

    kept = tmp_path / "kept.png"
    kept.write_bytes(b"kept-image")
    missing = tmp_path / "deleted.png"
    captured = []

    def post(_url, body, _headers, _timeout, **_kwargs):
        captured.append(body)
        return httpx.Response(200, json={"message": {"content": "ok"}},
                              request=httpx.Request("POST", "http://fixture/api/chat"))

    monkeypatch.setattr("rsched.endpoints.openai_compat.post_json", post)
    endpoint = OpenAICompatEndpoint(EndpointConfig(
        name="fixture", kind="openai", base_url="http://fixture/v1", schema_mode="ollama_native"))
    reply = endpoint.complete([{"role": "user", "content": "earlier observation", "media": [
        {"path": str(missing), "media_type": "image/png"},
        {"path": str(kept), "media_type": "image/png"}]}], model="fixture", schema={"type": "object"})
    assert reply.text == "ok"
    message = captured[0]["messages"][0]
    assert message["images"] == [base64.b64encode(b"kept-image").decode("ascii")]
    assert "Attachment unavailable" in message["content"]
    assert str(missing) in message["content"]
