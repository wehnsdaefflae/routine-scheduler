"""The media an endpoint may be handed NATIVELY — ONE allowlist, tagged by MODALITY.

The allowlist is ONE mapping, mime → modality (D192), and every question about a file ("may it
ride a message?", "which provider block is it?", "is it measured in pixels?") is asked of the
MODALITY, so adding a modality is one entry.

The modality does NOT decide whether a given MODEL and WIRE carry it. That is
`supports_media_type`: the modality ANDed with the model's catalog bits (`multimodal`,
`audio`/`video` — config/modelconf.py) and the adapter's own wire facts (`pdf`/`audio`/`video`).

This module is PURE: mime tables, file-header measurements, and predicates over them. It imports
nothing from the rest of the package, so every layer — adapters, the engine's media ops, the
decide action — can read the one allowlist without importing a transport.
"""

from __future__ import annotations

import base64
import mimetypes
import struct
from pathlib import Path

#: Every media type an endpoint might take natively, and its modality.
#:
#: Several spellings per format on purpose: `mimetypes.guess_type` reads the host's
#: /etc/mime.types, so the same .wav answers `audio/x-wav` on one host and `audio/wav` on another.
MEDIA_MODALITIES: dict[str, str] = {
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "image/gif": "image",
    "application/pdf": "pdf",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/wave": "audio",
    "audio/vnd.wave": "audio",
    "audio/mpeg": "audio",
    "audio/mp3": "audio",
    "audio/mp4": "audio",
    "audio/ogg": "audio",
    "audio/flac": "audio",
    "audio/webm": "audio",
    "video/mp4": "video",
    "video/mpeg": "video",
    "video/webm": "video",
    "video/quicktime": "video",
    "video/x-matroska": "video",
}

#: The modalities above, in the order a human lists them — for prose and for validation.
MODALITIES = ("image", "pdf", "audio", "video")

# Base64 inflates ~33%, so the raw-byte ceiling keeps most providers' ~10 MB request limit; a
# larger file (or an unlisted type) routes to the `vision` util or a transcription util instead.
NATIVE_MEDIA_MAX_BYTES = 7 * 1024 * 1024

#: The per-DIMENSION pixel ceiling (F623), the provider's own figure for "many-image requests"
#: (the strictest form, which an observation-accumulating prompt is). One over-tall image makes
#: the provider refuse the WHOLE message, so it is checked BEFORE the request and such an image
#: routes to the vision util instead.
NATIVE_MEDIA_MAX_PIXELS_PER_SIDE = 2000

#: Said AT every refusal: audio and especially video meet the byte ceiling almost at once, so a
#: refused run needs the number and the way around it in the same sentence.
NATIVE_MEDIA_LIMIT_NOTE = (
    f"A native attachment is capped at {NATIVE_MEDIA_MAX_BYTES // 1024 // 1024} MiB of RAW bytes "
    f"(base64 inflates it ~33% toward most providers' ~10 MB request limit), which a video "
    f"exceeds within seconds of footage: extract the audio or a few frames first "
    f"(the `video-extract` util) and attach that, or transcribe it (the `deepgram` util)."
)


def modality_of(mime: str | None) -> str | None:
    """`image` / `pdf` / `audio` / `video` for a media type an endpoint might take, else None."""
    return MEDIA_MODALITIES.get(mime or "")


def guess_media_type(path: str | Path) -> str | None:
    """The mime for a path IF it is a media type an endpoint might take natively, else None."""
    mime = mimetypes.guess_type(str(path))[0]
    return mime if mime in MEDIA_MODALITIES else None


def read_media_b64(path: str | Path) -> str:
    """The file's bytes as a base64 ASCII string (built at send time, never stored)."""
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def supports_media_type(mime: str, *, multimodal: bool, pdf: bool, audio: bool,
                        video: bool) -> bool:
    """Shared `supports_media` core: images when the model is multimodal; a PDF, audio or video
    file only where the matching bit is ALSO true. Everything else → the caller's fallback.

    `multimodal` is the master gate (a text-only model takes nothing). `pdf`, `audio` and
    `video` are per-modality permissions on top: the adapter states what its WIRE carries and,
    for audio/video, ANDs in the per-model catalog bit.
    """
    if not multimodal:
        return False
    modality = modality_of(mime)
    if modality == "image":
        return True
    if modality == "pdf":
        return pdf
    if modality == "audio":
        return audio
    if modality == "video":
        return video
    return False


def model_modalities(ref: object) -> dict[str, bool]:
    """A resolved model's modality bits as the keywords `supports_media` takes.

    One spelling of "what this model can be shown", so a new modality changes no call site.
    `getattr` with a default keeps a minimal stand-in ref working.
    """
    return {"multimodal": bool(getattr(ref, "multimodal", False)),
            "audio": bool(getattr(ref, "audio", False)),
            "video": bool(getattr(ref, "video", False))}


def image_dimensions(path: str | Path) -> tuple[int, int] | None:
    """`(width, height)` in pixels for a PNG / JPEG / GIF / WEBP, or None if unreadable.

    Deliberately dependency-free (no Pillow for one measurement). None means "could not
    measure", never "within the limit" — the caller decides what an unmeasurable file may do.
    """
    try:
        with Path(path).open("rb") as fh:
            head = fh.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            if head[:6] in (b"GIF87a", b"GIF89a"):
                w, h = struct.unpack("<HH", head[6:10])
                return int(w), int(h)
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return _webp_dimensions(head)
            if head[:2] == b"\xff\xd8":
                return _jpeg_dimensions(fh)
    except (OSError, struct.error, ValueError):
        return None
    return None


def _webp_dimensions(head: bytes) -> tuple[int, int] | None:
    """WEBP, the three chunk layouts: VP8 (lossy), VP8L (lossless), VP8X (extended)."""
    chunk = head[12:16]
    if chunk == b"VP8X":
        w = int.from_bytes(head[24:27], "little") + 1
        h = int.from_bytes(head[27:30], "little") + 1
        return w, h
    if chunk == b"VP8 ":
        w, h = struct.unpack("<HH", head[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if chunk == b"VP8L":
        bits = int.from_bytes(head[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def _jpeg_dimensions(fh) -> tuple[int, int] | None:
    """JPEG, by walking the markers to the first SOFn frame header — the only place the size
    is stated. Bounded: a file whose markers do not lead to a frame returns None rather than
    reading forever.
    """
    fh.seek(2)
    for _ in range(256):                       # a real JPEG reaches its SOF in a handful
        marker = fh.read(2)
        if len(marker) < 2 or marker[0] != 0xFF:
            return None
        kind = marker[1]
        if kind in (0xD8, 0xD9) or 0xD0 <= kind <= 0xD7:
            continue
        size_bytes = fh.read(2)
        if len(size_bytes) < 2:
            return None
        size = struct.unpack(">H", size_bytes)[0]
        # SOF0..SOF15, excluding the DHT/DAC/DNL markers that share the range
        if kind in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            frame = fh.read(5)
            if len(frame) < 5:
                return None
            h, w = struct.unpack(">HH", frame[1:5])
            return int(w), int(h)
        fh.seek(size - 2, 1)
    return None


def oversize_reason(path: str | Path, mime: str) -> str | None:
    """Why this file may NOT ride a message natively, as a sentence naming the LIMIT and the
    file's own measured size — or None when it may (F623).

    One predicate, shared by `view_image` and conversation auto-attach. Only an IMAGE is
    measured in pixels; the rest by bytes alone, and an audio/video byte refusal carries
    NATIVE_MEDIA_LIMIT_NOTE.
    """
    try:
        size = Path(path).stat().st_size
    except OSError as exc:
        return f"could not be measured: {exc}"
    modality = modality_of(mime)
    if size > NATIVE_MEDIA_MAX_BYTES:
        note = f" {NATIVE_MEDIA_LIMIT_NOTE}" if modality in ("audio", "video") else ""
        return (f"{size / 1024 / 1024:.1f} MiB exceeds the "
                f"{NATIVE_MEDIA_MAX_BYTES / 1024 / 1024:.0f} MiB native-attachment ceiling"
                f"{note}")
    if modality != "image":
        return None
    dims = image_dimensions(path)
    if dims is None:
        return None            # unmeasurable: the byte cap stays the only bound it must clear
    width, height = dims
    if max(width, height) > NATIVE_MEDIA_MAX_PIXELS_PER_SIDE:
        return (f"{width}x{height} px exceeds the provider's limit of "
                f"{NATIVE_MEDIA_MAX_PIXELS_PER_SIDE} pixels per side for a request carrying "
                f"several images — one such file makes the provider refuse the WHOLE message, "
                f"so it is described by the vision util instead. Downscale it (e.g. "
                f"`gu img-fetch`'s resize, or any tool that bounds the long side to "
                f"{NATIVE_MEDIA_MAX_PIXELS_PER_SIDE} px) to have the model see it directly")
    return None
