"""A message's `media` list → OpenAI-compatible content PARTS, one part shape per modality.

Split out of `openai_compat.py` (D192), which was at the house size bar and may only shrink.
It is also a cleaner seam: turning files into content parts is a pure mapping from the one
modality-tagged allowlist (`media_types.MEDIA_MODALITIES`) to the four part shapes this wire
knows, with no transport, no auth and no retry around it.

The shapes, each the one OpenAI publishes (or, where it publishes none, the one the
OpenAI-compatible servers that take the modality agree on):

* image → `image_url` with a base64 data URI
* pdf   → `file` / `file_data` with a base64 data URI
* audio → `input_audio` with raw base64 `data` plus a bare `format` word ("wav", "mp3") — the
  only part here that does NOT take a data URI, which is why format is derived separately
* video → `video_url` with a base64 data URI, mirroring `image_url`

Nothing reaches a provider because a shape exists for it: `supports_media` decides that, per
model and per adapter, before a file is ever put in a message.
"""

from __future__ import annotations

from pathlib import Path

from .base import Message
from .media_types import modality_of, read_media_b64

#: mime → the bare `format` word OpenAI's `input_audio` part wants. It is NOT the mime subtype
#: in every case (audio/mpeg is "mp3"), and the API rejects an unknown word, so the mapping is
#: explicit rather than derived by string-splitting.
AUDIO_FORMATS: dict[str, str] = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/vnd.wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "mp4",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/webm": "webm",
}


def audio_format(mime: str) -> str:
    """The `format` word for an audio mime — the mapped one, else the subtype as a last resort."""
    return AUDIO_FORMATS.get(mime) or mime.split("/", 1)[-1]


def media_part(item: dict, b64: str) -> dict:
    """ONE media entry (already read) as the content part its modality takes on this wire."""
    mime = item["media_type"]
    modality = modality_of(mime)
    if modality == "pdf":
        return {"type": "file", "file": {"filename": Path(item["path"]).name,
                                         "file_data": f"data:{mime};base64,{b64}"}}
    if modality == "audio":
        # The one part that carries raw base64 rather than a data URI (OpenAI's own shape).
        return {"type": "input_audio", "input_audio": {"data": b64, "format": audio_format(mime)}}
    if modality == "video":
        return {"type": "video_url", "video_url": {"url": f"data:{mime};base64,{b64}"}}
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def content_parts(content: str, media: list[dict]) -> list[dict]:
    """A message's string content + media list → OpenAI content parts: text first, then one
    part per file, built from bytes the engine captured when it verified the file.
    """
    parts: list[dict] = [{"type": "text", "text": content}] if content else []
    for item in media:
        try:
            # R1493: prefer bytes the engine captured when it verified the file — see the same
            # comment in anthropic_messages.content_blocks. Re-reading the path on every send
            # lets a later overwrite or cleanup invalidate an attachment already promised to
            # the model.
            b64 = item.get("b64") or read_media_b64(item["path"])
        except OSError as exc:
            parts.append({"type": "text", "text":
                          f"[Attachment unavailable: {item['path']}: {exc}. "
                          "The earlier observation remains in the conversation.]"})
            continue
        parts.append(media_part(item, b64))
    return parts


def render_media(messages: list[Message]) -> list[Message]:
    """Rewrite any message carrying `media` into OpenAI content-array form; text-only
    messages keep their plain string content. Drops the engine-side `media` key.
    """
    return [{"role": m["role"], "content": content_parts(m.get("content", ""), m["media"])}
            if m.get("media") else {"role": m["role"], "content": m["content"]}
            for m in messages]
