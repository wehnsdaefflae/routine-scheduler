"""Previously attached files can disappear before a later provider request."""
import pytest

from rsched.endpoints.anthropic_messages import content_blocks
from rsched.endpoints.openai_media import content_parts


@pytest.mark.parametrize("render", [content_blocks, content_parts])
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
